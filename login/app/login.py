# coding:utf-8
"""
login 组登录服务

客户端登录流程:
    1. 客户端 TCP 连接 login 服务，发送 login.auth {account, password}
    2. 校验账号（演示版：密码 123456 或 guest 开头免密）
    3. 生成 token，跨组调用 game 组 world 进程登记令牌
    4. 按账号哈希分配一个 gameN 进程，把其客户端地址返回给客户端
    5. 客户端拿 token 去连对应的 gameN 进程完成进入游戏
"""
import hashlib
import uuid

from engine import api
from engine.context import CTX
from engine.core.handler import handler
from engine.util import log

GAME_CLUSTER = 'game'  # game 进程组的组间名


def _pick_game_addr(account):
    """按账号哈希把玩家分配到某个 gameN 进程"""
    game_servers = CTX.config.get('gameServers', {})
    if not game_servers:
        return None
    names = sorted(game_servers.keys())
    idx = int(hashlib.md5(account.encode('utf-8')).hexdigest(), 16) % len(names)
    return names[idx], game_servers[names[idx]]


@handler('login.auth')
async def auth(session, data):
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
    game_service, game_addr = picked

    token = uuid.uuid4().hex
    # 跨组调用 game 组的全局进程登记令牌
    await api.call_cluster(GAME_CLUSTER, 'world.prepareLogin', account, token)

    session.account = account
    log.notice('login success: %s -> %s(%s)', account, game_service, game_addr)
    return {
        'ok': True,
        'account': account,
        'token': token,
        'gameAddr': game_addr,
    }


def init():
    log.notice('login service inited')
