"""
login 组登录服务

客户端登录流程（两段式：先验账号，后选服）:
    1. 客户端 TCP 连接 login 服务，发送 login.auth {account, password}
       -> 校验账号（演示版：密码 123456 或 guest 开头免密），落 PG accounts 表，
          返回可选服务器列表；只有一个服时直接发令牌返回 game 地址，
          客户端免去第 2 步
    2. 多个服时玩家选定服务器，发送 login.enterServer {server}（server = 展示服号 show_id）
       -> 状态拦截（维护/备用/未开服拒入），注册控制（新注册遇爆满/满员拒入，
          老玩家回本服不受影响），生成 token 并登记到该服 world，
          按账号哈希在服内分配 gameN，返回其客户端地址
          （只有 auth 成功的会话才能选服）
    3. 客户端拿 token 去连对应的 gameN 进程完成进入游戏

数据来源:
    - servers 表（DB）: 选服列表与注册控制（master 启动时按 config 种子，
      之后以表为准，GM 可直接改表上下架服）
    - config servers.<real_id>.gameNodes: 服内 game 节点地址（网络拓扑，含动态扩容）
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
    server_cfg = CTX.config.get('servers', {}).get(server)
    if server_cfg is None:
        return None
    game_nodes = server_cfg.get('gameNodes', {})
    if not game_nodes:
        return None
    names = sorted(game_nodes.keys())
    idx = int(hashlib.md5(account.encode('utf-8')).hexdigest(), 16) % len(names)
    return names[idx], game_nodes[names[idx]]


async def _visible_servers():
    """可选服务器列表（servers 表）: 维护(0) / 备用(4) / 未到开服时间的不展示，
    推荐服排前面，其余按开服时间倒序"""
    return await db.get().fetch('''
        SELECT real_id, show_id, name, status, is_recommend,
               register_count, max_register
        FROM servers
        WHERE status IN (1, 2, 3) AND (start_time IS NULL OR start_time <= now())
        ORDER BY is_recommend DESC, start_time DESC NULLS LAST, id''')


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
        # 只有一个服：直接发令牌返回 game 地址，客户端免去 login.enterServer
        rsp.update(await _issue_token(account, servers[0]['server']))
    return rsp


async def _register(account, row):
    """进服注册控制: 已在该服注册过直接放行（爆满/满员不影响老玩家回服）;
    新注册时爆满(ServerStatus.FULL)拒入，再检查人数上限并计数
    @return 拒绝原因，None 表示放行
    """
    real_id = row['real_id']
    gdb = db.get()
    exists = await gdb.fetchrow(
        'SELECT 1 FROM account_servers WHERE account = $1 AND real_id = $2',
        account, real_id)
    if exists is not None:
        # 无需注册
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


async def _issue_token(account, show_id):
    """为账号在指定服发令牌：状态拦截 -> 注册控制 -> 登记到该服 world -> 返回分配的 game 地址"""
    row = await db.get().fetchrow('''
        SELECT id, real_id, show_id, name, status, start_time,
               register_count, max_register
        FROM servers WHERE show_id = $1''', show_id)
    if row is None:
        return {'ok': False, 'error': f'unknown server {show_id}'}
    # 绕过选服列表直接传隐藏服号也要拦住：维护 / 备用 / 未到开服时间
    start_time = row['start_time']
    if row['status'] in BLOCKED_STATUSES or (
            start_time is not None and start_time > datetime.now(UTC)):
        return {'ok': False, 'error': 'server unavailable'}
    error = await _register(account, row)
    if error:
        return {'ok': False, 'error': error}

    real_id = row['real_id']
    picked = _pick_game_addr(account, real_id)
    if picked is None:
        return {'ok': False, 'error': f'no game node in server {show_id}'}
    game_service, game_addrs = picked

    token = uuid.uuid4().hex
    # 跨组调用所选服的 world 进程登记令牌（cluster 名 = game<server>）
    await api.call_cluster(f'game{server}', 'world.prepareLogin', account, token)

    log.notice('enter server: %s server=%s(real=%s) -> %s(%s)',
               account, show_id, real_id, game_service, game_addrs)
    return {
        'ok': True,
        'account': account,
        'server': server,
        'token': token,
        'gameAddr': game_addrs['tcp'],      # Unity 等原生客户端走 TCP
        'gameWsAddr': game_addrs['ws'],     # Cocos / H5 / 小程序走 WebSocket
    }


@handler('login.enterServer', public=True)
async def enter_server(session, data):
    """第二步：选服。发令牌并登记到该服 world，返回分配的 game 地址"""
    if not isinstance(session, ClientSession):
        log.warning('login.enterServer rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = session.account
    if not account:
        return {'ok': False, 'error': 'auth first'}
    server = str(data.get('server') or '')
    if not server:
        return {'ok': False, 'error': 'server required'}
    return await _issue_token(account, server)
