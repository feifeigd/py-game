"""
game 组单人进程（game1 / game2，玩家进程）

每个登录成功的玩家在本进程创建一个 Player actor:
  - 客户端发来的 player.* 消息进入该玩家的 actor 邮箱串行处理
  - 玩家的所有状态都在 actor 内，无并发冲突
  - 掉线 / 被顶号时 actor 收到 __logout__ 消息做清理

数据持久化（MongoDB，增量更新）:
  - 进入游戏时从 Mongo 加载角色档，新号建档
  - 角色字段放在 DirtyRecord 里，顶层赋值即标脏
  - 每 dataSaveInterval 秒把脏字段 $set 回库，下线时兜底落一次

本进程对客户端暴露 tcpServer（见 config.json），客户端消息:
    game.enter      进入游戏（验证令牌）
    player.chat     聊天
    player.getInfo  查询角色信息
"""
import asyncio

from engine import api
from engine import db
from engine.context import CTX
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.util import log

COLL_PLAYER = 'player'  # mongo 集合名

# 新号默认角色档
DEFAULT_DATA = {
    'name': '',
    'level': 1,
    'exp': 0,
    'diamond': 0,
}


class Player(Actor):
    """玩家 actor：一个在线玩家一个实例"""

    def __init__(self, account, session, doc, is_new):
        super().__init__()
        self.account = account
        self.session = session
        self._is_new = is_new
        # 角色数据走 DirtyRecord: 顶层字段赋值即标脏，增量落库
        self.data = db.DirtyRecord(doc)

    # 角色字段属性化: 读写在 data 上，写操作自动标脏
    @property
    def name(self):
        return self.data.name

    @property
    def level(self):
        return self.data.level

    @level.setter
    def level(self, value):
        self.data.level = value

    @property
    def exp(self):
        return self.data.exp

    @exp.setter
    def exp(self, value):
        self.data.exp = value

    @property
    def diamond(self):
        return self.data.diamond

    @diamond.setter
    def diamond(self, value):
        self.data.diamond = value

    # ---------------------------------------------------------------
    # 落库
    # ---------------------------------------------------------------
    async def save(self, force=False):
        """把脏字段增量写回 Mongo; force=True 无视脏标记整档覆盖"""
        if not force and not self._is_new and not self.data.is_dirty:
            return 0
        n = await db.get().save(COLL_PLAYER, 'account', self.account,
                                self.data, is_new=self._is_new or force)
        self._is_new = False
        if n:
            log.info('player %s saved, %d fields', self.account, n)
        return n

    async def do_save(self):
        """周期落库入口: _save_loop 经 actor 邮箱调用，与业务消息串行"""
        return await self.save()

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
            'name': self.data.name,
            'level': self.data.level,
            'exp': self.data.exp,
            'diamond': self.data.diamond,
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
        # 充值是钱，立刻落库不等周期
        await self.save()
        await self.session.push('recharge.push',
                                {'item': item, 'count': count, 'diamond': self.diamond})

    async def do_kick(self, reason='relogin'):
        await self.save()
        await self.session.push('sys.kick', {'reason': reason})
        self.session.close()

    async def do___logout__(self):
        """连接断开清理（do_ + __logout__），下线前兜底落库"""
        await self.save()
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
    """客户端进入游戏：验证令牌 -> 加载角色档 -> 创建玩家 actor"""
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

    # 从 Mongo 加载角色档，新号按默认档建档
    doc = await db.get().load(COLL_PLAYER, 'account', account)
    is_new = doc is None
    if is_new:
        doc = dict(DEFAULT_DATA)
        doc['name'] = f'勇者_{account}'
    doc.pop('account', None)  # account 是主键，不进 DirtyRecord

    player = Player(account, session, doc, is_new).start()
    _players[account] = player
    session.player = player
    session.account = account
    log.notice('player %s enter %s (%s)', account, CTX.service_name,
               'new' if is_new else 'loaded')
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


# ---------------------------------------------------------------------
# 周期落库
# ---------------------------------------------------------------------
async def _save_loop():
    """每 dataSaveInterval 秒把所有在线玩家的脏字段增量写回 Mongo"""
    interval = CTX.config.get('dataSaveInterval', 30)
    while True:
        await asyncio.sleep(interval)
        for player in list(_players.values()):
            try:
                await player.ask('save')
            except Exception:
                log.exception('save player %s failed', player.account)


def init():
    asyncio.ensure_future(_save_loop())
    log.notice('%s (player process) inited', CTX.service_name)
