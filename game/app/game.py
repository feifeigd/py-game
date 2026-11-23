"""
game 组单人进程（game1 / game2，玩家进程）

每个登录成功的玩家在本进程创建一个 Player actor:
  - 客户端发来的 player.* 消息进入该玩家的 actor 邮箱串行处理
  - 玩家的所有状态都在 actor 内，无并发冲突
  - 掉线 / 被顶号时 actor 收到 __logout__ 消息做清理

本进程对客户端暴露 tcpServer（见 config.json），客户端消息:
    game.enter      进入游戏（验证令牌）
    player.chat     聊天
    player.getInfo  查询角色信息
"""
from engine import api
from engine.context import CTX
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.util import log


class Player(Actor):
    """玩家 actor：一个在线玩家一个实例"""

    def __init__(self, account, session):
        super().__init__()
        self.account = account
        self.session = session
        # 角色数据（真实项目里走 db/cache 层，这里内存演示）
        self.name = f'勇者_{account}'
        self.level = 1
        self.exp = 0
        self.diamond = 0

    # ---------------------------------------------------------------
    # 客户端消息（player.xxx -> do_xxx）
    # ---------------------------------------------------------------
    async def do_chat(self, data):
        text = str(data.get('text', ''))[:200]
        log.info('[%s] %s chat: %s', CTX.service_name, self.account, text)
        # 经全局进程做全服跑马灯广播
        api.send_service('master', 'world.chatMarquee',
                         self.account, text, CTX.service_name)
        return {'ok': True}

    async def do_get_info(self, data):
        return {
            'account': self.account,
            'name': self.name,
            'level': self.level,
            'exp': self.exp,
            'diamond': self.diamond,
            'service': CTX.service_name,
        }

    async def do_add_exp(self, data):
        self.exp += int(data.get('exp', 0))
        while self.exp >= self.level * 100:
            self.exp -= self.level * 100
            self.level += 1
            await self.session.push('player.levelUp', {'level': self.level})
        return {'level': self.level, 'exp': self.exp}

    # ---------------------------------------------------------------
    # 内部消息（其它进程经 handler 投递进来）
    # ---------------------------------------------------------------
    async def do_recharge(self, item, count):
        """充值到账"""
        if item == 'diamond':
            self.diamond += int(count)
        log.notice('player %s recharge %s x%s, diamond=%d',
                   self.account, item, count, self.diamond)
        await self.session.push('recharge.push',
                                {'item': item, 'count': count, 'diamond': self.diamond})

    async def do_kick(self, reason='relogin'):
        await self.session.push('sys.kick', {'reason': reason})
        self.session.close()

    async def do___logout__(self):
        """连接断开清理（do_ + __logout__）"""
        logout(self.account, self)

    def on_hotfix(self):
        log.info('player %s hotfixed (class rebound)', self.account)


# 热更新安全：reload 复用模块 __dict__，保住在线玩家表
if '_players' not in globals():
    _players = {}  # account -> Player


def logout(account, player):
    if _players.get(account) is player:
        del _players[account]
    player.stop()
    api.send_service('master', 'world.playerLogout', account, CTX.service_name)
    log.notice('player %s logout from %s', account, CTX.service_name)


# ---------------------------------------------------------------------
# handler 入口
# ---------------------------------------------------------------------
@handler('game.enter')
async def enter(session, data):
    """客户端进入游戏：验证令牌 -> 创建玩家 actor"""
    account = str(data.get('account', ''))
    token = str(data.get('token', ''))
    if not account or not token:
        return {'ok': False, 'error': 'account/token required'}

    result = await api.call_service(
        'master', 'world.verifyToken', account, token, CTX.service_name)
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error', 'verify failed')}

    # 已在的（同进程重进）先清掉
    old = _players.get(account)
    if old is not None:
        old.tell('kick', 'reenter')

    player = Player(account, session).start()
    _players[account] = player
    session.player = player
    session.account = account
    log.notice('player %s enter %s', account, CTX.service_name)
    return {'ok': True, 'player': await player.ask('get_info', {})}


@handler('game.addRecharge')
def add_recharge(account, item, count):
    """world 进程转发的充值"""
    player = _players.get(account)
    if player is None:
        return {'ret': 1, 'msg': 'player not in this service'}
    player.tell('recharge', item, count)
    return {'ret': 0}


@handler('game.kick')
def kick(account):
    player = _players.get(account)
    if player is not None:
        player.tell('kick')
        return True
    return False


@handler('game.broadcastPush')
def broadcast_push(key, data):
    """world 进程的全服广播 -> 推给本进程所有在线客户端"""
    for player in list(_players.values()):
        session = player.session
        import asyncio
        asyncio.ensure_future(session.push(key, data))
    return len(_players)


@handler('game.onlineCount')
def online_count():
    return len(_players)


def init():
    log.notice('%s (player process) inited', CTX.service_name)
