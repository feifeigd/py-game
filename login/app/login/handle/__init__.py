"""
login 组登录服务

客户端登录流程:
    1. 客户端 TCP 连接 login 服务，发送 login.auth {account, password, zone}
       zone 为大区号（如 "1001"/"1002"），缺省 "1001"
    2. 校验账号（演示版：密码 123456 或 guest 开头免密）
    3. 账号落 PG accounts 表（新号插入，老号只更新 login_count/last_login 两列）
    4. 生成 token，跨组调用所选大区（game<zone>）的 world 进程登记令牌
    5. 按账号哈希在该大区内分配一个 gameN 进程，把其客户端地址返回给客户端
    6. 客户端拿 token 去连对应的 gameN 进程完成进入游戏
"""
import hashlib
import uuid
from datetime import datetime, timezone

from engine import api, db
from engine.context import CTX
from engine.core.handler import handler
from engine.net.server import ClientSession
from engine.util import log

DEFAULT_ZONE = '1001'


def _pick_game_addr(account, zone):
    """按账号哈希把玩家分配到指定大区的某个 gameN 进程
    @return game_service, {'tcp': ..., 'ws': ...}
    """
    zone_cfg = CTX.config.get('zones', {}).get(zone)
    if zone_cfg is None:
        return None
    game_servers = zone_cfg.get('gameServers', {})
    if not game_servers:
        return None
    names = sorted(game_servers.keys())
    idx = int(hashlib.md5(account.encode('utf-8')).hexdigest(), 16) % len(names)
    return names[idx], game_servers[names[idx]]


@handler('login.serviceUp')
def on_service_up(zone, service, addrs):
    """game 组扩容通知（经本组 master 转发）：新 gameN 纳入该大区的分配池"""
    zone_cfg = CTX.config.setdefault('zones', {}).setdefault(zone, {})
    zone_cfg.setdefault('gameServers', {})[service] = addrs
    log.notice(f'game server joined: zone={zone} {service} {addrs}')


@handler('login.serviceDown')
def on_service_down(zone, service):
    """game 组缩容通知（经本组 master 转发）：把 gameN 移出该大区的分配池"""
    zone_cfg = CTX.config.get('zones', {}).get(zone, {})
    servers = zone_cfg.get('gameServers', {})
    if servers.pop(service, None) is not None:
        log.notice(f'game server left: zone={zone} {service}')


async def _record_login(account):
    """账号落库: 新号插入整行; 老号增量更新 login_count / last_login 两列"""
    gdb = db.get()
    now = datetime.now(timezone.utc)
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
async def auth(session, data):
    log.notice('login auth: %s', data)
    # 同 game.enter：只认真实客户端连接，防止集群内 RPC 伪造调用
    # （否则会空跑落库和令牌登记，最后才在 session.account 处炸掉）
    if not isinstance(session, ClientSession):
        log.warning('login.auth rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = str(data.get('account', '')).strip()
    password = str(data.get('password', ''))
    zone = str(data.get('zone') or DEFAULT_ZONE)
    if not account:
        return {'ok': False, 'error': 'account required'}
    # 演示版账号校验：真实项目在这里接账号平台 / SDK 验证
    if not (password == '123456' or account.startswith('guest')):
        return {'ok': False, 'error': 'bad password'}

    picked = _pick_game_addr(account, zone)
    if picked is None:
        return {'ok': False, 'error': f'no game server in zone {zone}'}
    game_service, game_addrs = picked

    await _record_login(account)

    token = uuid.uuid4().hex
    # 跨组调用所选大区的 world 进程登记令牌（cluster 名 = game<zone>）
    await api.call_cluster(f'game{zone}', 'world.prepareLogin', account, token)

    session.account = account
    log.notice('login success: %s zone=%s -> %s(%s)', account, zone,
               game_service, game_addrs)
    return {
        'ok': True,
        'account': account,
        'zone': zone,
        'token': token,
        'gameAddr': game_addrs['tcp'],      # Unity 等原生客户端走 TCP
        'gameWsAddr': game_addrs['ws'],     # Cocos / H5 / 小程序走 WebSocket
    }
