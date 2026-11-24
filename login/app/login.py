"""
login 组登录服务

客户端登录流程:
    1. 客户端 TCP 连接 login 服务，发送 login.auth {account, password}
    2. 校验账号（演示版：密码 123456 或 guest 开头免密）
    3. 账号落 PG accounts 表（新号插入，老号只更新 login_count/last_login 两列）
    4. 生成 token，跨组调用 game 组 world 进程登记令牌
    5. 按账号哈希分配一个 gameN 进程，把其客户端地址返回给客户端
    6. 客户端拿 token 去连对应的 gameN 进程完成进入游戏
"""
import hashlib
import uuid
from datetime import datetime, timezone

from engine import api
from engine import db
from engine.context import CTX
from engine.core.handler import handler
from engine.util import log

GAME_CLUSTER = 'game'  # game 进程组的组间名


def _pick_game_addr(account):
    """按账号哈希把玩家分配到某个 gameN 进程
    @return game_service, {'tcp': ..., 'ws': ...}
    """
    game_servers = CTX.config.get('gameServers', {})
    if not game_servers:
        return None
    names = sorted(game_servers.keys())
    idx = int(hashlib.md5(account.encode('utf-8')).hexdigest(), 16) % len(names)
    return names[idx], game_servers[names[idx]]


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


@handler('login.auth')
async def auth(session, data):
    log.notice('login auth: %s', data)
    account = str(data.get('account', '')).strip()
    password = str(data.get('password', ''))
    if not account:
        return {'ok': False, 'error': 'account required'}
    # 演示版账号校验：真实项目在这里接账号平台 / SDK 验证
    if not (password == '123456' or account.startswith('guest')):
        return {'ok': False, 'error': 'bad password'}

    picked = _pick_game_addr(account)
    if picked is None:
        return {'ok': False, 'error': 'no game server'}
    game_service, game_addrs = picked

    await _record_login(account)

    token = uuid.uuid4().hex
    # 跨组调用 game 组的全局进程登记令牌
    await api.call_cluster(GAME_CLUSTER, 'world.prepareLogin', account, token)

    session.account = account
    log.notice('login success: %s -> %s(%s)', account, game_service, game_addrs)
    return {
        'ok': True,
        'account': account,
        'token': token,
        'gameAddr': game_addrs['tcp'],      # Unity 等原生客户端走 TCP
        'gameWsAddr': game_addrs['ws'],     # Cocos / H5 / 小程序走 WebSocket
    }


async def init():
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            account     TEXT PRIMARY KEY,
            login_count INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL,
            last_login  TIMESTAMPTZ NOT NULL
        )''')
    log.notice('login service inited')
