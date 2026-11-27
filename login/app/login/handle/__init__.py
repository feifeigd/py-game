"""
login 组登录服务

客户端登录流程（先验账号，后选服，再选角/创角）:
    1. 客户端 TCP 连接 login 服务，发送 login.auth {account, password}
       -> 校验账号（演示版：密码 123456 或 guest 开头免密），落 PG accounts 表，
          从 servers 表返回可选服务器列表；只有一个服时直接顺路走进服流程
    2. 玩家选定服务器，发送 login.enterServer {server}（server = 展示服号 show_id）
       -> 状态拦截（维护/备用/未开服拒入；已合并服放行，自动路由到迁入服），
       注册控制（新注册遇爆满/满员拒入，
          老玩家回本服不受影响），然后跨组查该服的角色列表，统一返回 roles
          （0/1/多个都进角色面板：选已有角色或再创新角色）
    3a. 玩家自选名字/性别创角，发送 login.createRole {server, name, sex}
       -> 跨组调 game 组 world.createRole 建档（角色名本服唯一），发令牌
    3b. 玩家选定已有角色，发送 login.enterRole {server, role}，发令牌
    4. 客户端拿 token 去连分配的 gameN 进程，game.enter {account, role, token}
       完成进入游戏

数据来源:
    - servers 表（DB）: 选服列表与注册控制（master 启动时按 config 种子，
      之后以表为准，GM 可直接改表上下架服）
    - config servers.<real_id>.gameNodes: 服内 game 节点地址（网络拓扑，含动态扩容）
    - 角色列表/创角: 实时跨组调 game 组 world（player 档在 game 组 Mongo）
    - servers 表同时是合服重定向表：老服行（real_id 指向迁入服）保留不删，
      login.resolveServer（注册在本组 master 上）供 recharge 解析
      role_id 前缀 -> 现居服
"""
import hashlib
import uuid
from datetime import UTC, datetime

from app.const import BLOCKED_STATUSES, VISIBLE_STATUSES, ServerStatus
from engine import api, db
from engine.context import CTX
from engine.core.handler import handler
from engine.net.server import ClientSession
from engine.util import log


def _pick_game_addr(account, real_id):
    """按账号哈希把玩家分配到指定服的某个 gameN 进程
    @return game_service, {'tcp': ..., 'ws': ...}
    """
    server_cfg = CTX.config.get('servers', {}).get(real_id)
    if server_cfg is None:
        return None
    game_nodes = server_cfg.get('gameNodes', {})
    if not game_nodes:
        return None
    names = sorted(game_nodes.keys())
    idx = int(hashlib.md5(account.encode('utf-8')).hexdigest(), 16) % len(names)
    return names[idx], game_nodes[names[idx]]


async def _visible_servers():
    """可选服务器列表（servers 表）: 维护 / 备用 / 未到开服时间的不展示；
    已合并服仍展示（老玩家的入口保留，进入路由到迁入服）。
    推荐服排前面，其余按开服时间倒序"""
    return await db.get().fetch('''
        SELECT real_id, show_id, name, status, is_recommend,
               register_count, max_register
        FROM servers
        WHERE status = ANY($1) AND (start_time IS NULL OR start_time <= now())
        ORDER BY is_recommend DESC, start_time DESC NULLS LAST, id''',
        VISIBLE_STATUSES)


async def _load_available_server(show_id):
    """按展示服号查服并做进入拦截（维护 / 备用 / 未到开服时间；
    已合并服放行，row['real_id'] 自然指向迁入服）
    @return (row, None) 或 (None, 错误响应)
    """
    row = await db.get().fetchrow('''
        SELECT id, real_id, show_id, name, status, start_time,
               register_count, max_register, role_count
        FROM servers WHERE show_id = $1''', show_id)
    if row is None:
        return None, {'ok': False, 'error': f'unknown server {show_id}'}
    start_time = row['start_time']
    if row['status'] in BLOCKED_STATUSES or (
            start_time is not None and start_time > datetime.now(UTC)):
        return None, {'ok': False, 'error': 'server unavailable'}
    return row, None


@handler('login.serviceUp')
def on_service_up(real_id, service, addrs):
    """game 组扩容通知（经本组 master 转发）：新 gameN 纳入该服的分配池"""
    server_cfg = CTX.config.setdefault('servers', {}).setdefault(real_id, {})
    server_cfg.setdefault('gameNodes', {})[service] = addrs
    log.notice(f'game node joined: server={real_id} {service} {addrs}')


@handler('login.serviceDown')
def on_service_down(real_id, service):
    """game 组缩容通知（经本组 master 转发）：把 gameN 移出该服的分配池"""
    server_cfg = CTX.config.get('servers', {}).get(real_id, {})
    nodes = server_cfg.get('gameNodes', {})
    if nodes.pop(service, None) is not None:
        log.notice(f'game node left: server={real_id} {service}')


async def _record_login(account):
    """账号落库: 新号插入整行; 老号增量更新 login_count / last_login 两列"""
    gdb = db.get()
    now = datetime.now(UTC)
    row = await gdb.fetchrow(
        'SELECT login_count FROM accounts WHERE account = $1', account)
    if row is None:
        await gdb.insert('accounts', {
            'account': account,
            'login_count': 1,
            'created_at': now,
            'last_login': now,
        })
    else:
        await gdb.update_columns('accounts', 'account', account, {
            'login_count': row['login_count'] + 1,
            'last_login': now,
        })


@handler('login.auth', public=True)
async def auth(session: ClientSession, data):
    """第一步：账号校验。成功返回可选服务器列表，会话记入 session.account"""
    log.notice('login auth: %s', data)
    # 同 game.enter：只认真实客户端连接，防止集群内 RPC 伪造调用
    if not isinstance(session, ClientSession):
        log.warning('login.auth rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = str(data.get('account', '')).strip()
    password = str(data.get('password', ''))
    if not account:
        return {'ok': False, 'error': 'account required'}
    # 演示版账号校验：真实项目在这里接账号平台 / SDK 验证
    if not (password == '123456' or account.startswith('guest')):
        return {'ok': False, 'error': 'bad password'}

    await _record_login(account)
    session.account = account

    rows = await _visible_servers()
    servers = [
        {'server': r['show_id'], 'name': r['name'],
         'status': r['status'], 'recommend': r['is_recommend']}
        for r in rows
    ]
    log.notice('auth success: %s, servers=%s', account, [s['server'] for s in servers])
    rsp = {'ok': True, 'account': account, 'servers': servers}
    if len(servers) == 1:
        # 只有一个服：直接顺路走进服流程，客户端免去 login.enterServer
        rsp.update(await _enter_server(account, servers[0]['server']))
    return rsp


async def _register(account, row, roles=None):
    """进服注册控制: 已在该服注册过直接放行（爆满/满员不影响老玩家回服）;
    新注册时爆满(ServerStatus.FULL)拒入，再检查人数上限并计数

    合服兜底: 被合并服的老玩家注册行还是老 real_id，按目标服查不到；
    查目标服角色列表，有角色就是合服迁来的老玩家，放行并补一行注册
    记录愈合数据（按一次注册计数，之后登录走快路径不再计）。
    @param roles 目标服角色列表；None 表示未知，需要时现查
    @return 拒绝原因，None 表示放行
    """
    real_id = row['real_id']
    gdb = db.get()
    exists = await gdb.fetchrow(
        'SELECT 1 FROM account_servers WHERE account = $1 AND real_id = $2',
        account, real_id)
    if exists is not None:
        return None
    if roles is None:
        roles = await api.call_cluster(
            f'game{real_id}', 'world.listRoles', account)
    # 有角色
    if roles:
        log.notice(f'merged player {account} back to {real_id} '
                   f'({len(roles)} roles), heal account_servers')
        status = await gdb.execute('''
            INSERT INTO account_servers (account, real_id, created_at)
            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING''',
            account, real_id, datetime.now(UTC))
        if not status.endswith(' 0'): # 成功插入： status=INSERT 0 1
            # 愈合成功按一次注册计数（按行主键更新：合服时多行共享 real_id），
            # 之后登录走快路径不再计；并发下撞行则已由别的请求计过
            await gdb.update_columns('servers', 'id', row['id'], {
                'register_count': row['register_count'] + 1,
            })
        return None
    if row['status'] == ServerStatus.FULL:
        return 'server busy'
    if row['max_register'] > 0 and row['register_count'] >= row['max_register']:
        return 'server full'
    status = await gdb.execute('''
        INSERT INTO account_servers (account, real_id, created_at)
        VALUES ($1, $2, $3) ON CONFLICT DO NOTHING''',
        account, real_id, datetime.now(UTC))
    if status.endswith(' 0'):
        return None  # 并发下已被别的请求登记，按已注册放行（不重复计数）
    # 按行主键更新计数: 合服时多行共享 real_id，不能按 real_id 定位
    await gdb.update_columns('servers', 'id', row['id'], {
        'register_count': row['register_count'] + 1,
    })
    return None


async def _enter_server(account, show_id):
    """选服: 拦截 + 注册控制 -> 查角色列表 -> 统一返回 roles 列表

    不做"单角色直发令牌"的快捷路径：客户端永远进角色面板，
    已有角色一键进游戏（login.enterRole），创角表单常驻面板底部，
    玩家随时可以再建角色（login.createRole）。
    """
    row, err = await _load_available_server(show_id)
    if err:
        return err
    real_id = row['real_id']
    # 先查角色列表：注册控制要用它识别合服迁来的老玩家，结果也直接返回给客户端
    roles = await api.call_cluster(f'game{real_id}', 'world.listRoles', account)
    error = await _register(account, row, roles)
    if error:
        return {'ok': False, 'error': error}
    return {'ok': True, 'account': account, 'server': show_id, 'roles': roles}


async def _issue_token(account, row, role):
    """为账号的指定角色发令牌：登记到该服 world，返回分配的 game 地址"""
    show_id, real_id = row['show_id'], row['real_id']
    picked = _pick_game_addr(account, real_id)
    if picked is None:
        return {'ok': False, 'error': f'no game node in server {show_id}'}
    game_service, game_addrs = picked

    token = uuid.uuid4().hex
    # 跨组调用所选服的 world 进程登记令牌（cluster 名 = game<real_id>）
    await api.call_cluster(f'game{real_id}', 'world.prepareLogin',
                           account, token, role)

    log.notice(f'enter server: {account} server={show_id}(real={real_id}) '
               f'role={role} -> {game_service}({game_addrs})')
    return {
        'ok': True,
        'account': account,
        'server': show_id,
        'role': role,
        'token': token,
        'gameAddr': game_addrs['tcp'],      # Unity 等原生客户端走 TCP
        'gameWsAddr': game_addrs['ws'],     # Cocos / H5 / 小程序走 WebSocket
    }


@handler('login.enterServer', public=True)
async def enter_server(session, data):
    """第二步：选服。拦截 + 注册控制后统一返回角色列表（进角色面板）"""
    if not isinstance(session, ClientSession):
        log.warning('login.enterServer rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = session.account
    if not account:
        return {'ok': False, 'error': 'auth first'}
    server = str(data.get('server') or '')
    if not server:
        return {'ok': False, 'error': 'server required'}
    return await _enter_server(account, server)


@handler('login.createRole', public=True)
async def create_role(session, data):
    """第三步（创角）：玩家自选角色名/性别创角，发令牌

    名字/性别的完整校验与建档在 game 组 world.createRole（角色名本服唯一），
    这里只做准入（状态拦截 + 注册控制）与基本参数检查。
    """
    if not isinstance(session, ClientSession):
        log.warning('login.createRole rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = session.account
    if not account:
        return {'ok': False, 'error': 'auth first'}
    server = str(data.get('server') or '')
    name = str(data.get('name') or '').strip()
    sex = int(data.get('sex') or 0)
    if not server:
        return {'ok': False, 'error': 'server required'}
    if not name:
        return {'ok': False, 'error': 'name required'}

    row, err = await _load_available_server(server)
    if err:
        return err
    error = await _register(account, row)   # 老账号 no-op；合服老玩家兜底现查
    if error:
        return {'ok': False, 'error': error}
    result = await api.call_cluster(
        f"game{row['real_id']}", 'world.createRole', account, name, sex)
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error', 'create role failed')}
    # 创建角色数（与 register_count 注册账号数是两个口径）按行主键递增
    await db.get().update_columns('servers', 'id', row['id'], {
        'role_count': row['role_count'] + 1,
    })
    return await _issue_token(account, row, result['role'])


@handler('login.enterRole', public=True)
async def enter_role(session, data):
    """第三步（选角）：选定已有角色发令牌"""
    if not isinstance(session, ClientSession):
        log.warning('login.enterRole rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = session.account
    if not account:
        return {'ok': False, 'error': 'auth first'}
    server = str(data.get('server') or '')
    role = str(data.get('role') or '')
    if not server:
        return {'ok': False, 'error': 'server required'}
    if not role:
        return {'ok': False, 'error': 'role required'}

    row, err = await _load_available_server(server)
    if err:
        return err
    # 角色必须存在且属于本账号；列表也供注册控制识别合服老玩家
    roles = await api.call_cluster(
        f"game{row['real_id']}", 'world.listRoles', account)
    error = await _register(account, row, roles)   # 老账号 no-op
    if error:
        return {'ok': False, 'error': error}
    if role not in [r['role'] for r in roles]:
        return {'ok': False, 'error': 'role not found'}
    return await _issue_token(account, row, role)
