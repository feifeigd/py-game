"""
game 组单人进程（game1 / game2，玩家进程）

每个登录成功的角色在本进程创建一个 Player actor:
  - 客户端发来的 player.* 消息进入该角色的 actor 邮箱串行处理
  - 角色的所有状态都在 actor 内，无并发冲突
  - 掉线 / 被顶号时 actor 收到 __logout__ 消息做清理

多角色模型: 一个账号在一个服可有多个角色（合服后常见）。
身份一律用 role_id（player 档主键，actor 寻址名 player:<role_id>），
account 只用于展示与按号查角色列表。同账号多角色能否同时在线由
config singleRoleOnline 决定（见 world.py 顶号策略）。

数据持久化（MongoDB，增量更新）:
  - 角色档由 world.createRole 创建（玩家自选名字/性别），本进程只按 role_id 加载
  - 角色字段放在 DirtyRecord 里，顶层赋值即标脏
  - 每 dataSaveInterval 秒把脏字段 $set 回库，下线时兜底落一次

本进程对客户端暴露 tcpServer（见 config.json），客户端消息:
    game.enter      进入游戏（验证令牌 + role_id）
    player.chat     聊天
    player.getInfo  查询角色信息
"""
import asyncio
import time

from app.const import COLL_PLAYER
from engine import api, db
from engine.context import CTX
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.net.server import ClientSession
from engine.util import log


class Player(Actor):
    """玩家 actor：一个在线角色一个实例"""

    def __init__(self, account, role, session, doc, is_new):
        super().__init__()
        self.account = account
        self.role = role
        self.session = session
        self._is_new = is_new
        # 角色数据走 DirtyRecord: 顶层字段赋值即标脏，增量落库
        self.data = db.DirtyRecord(doc)
        # 刷新最近登录时间（角色面板按它排序、默认选中上次玩的角色）
        self.data.login_time = int(time.time() * 1000)

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
        n = await db.get().save(COLL_PLAYER, 'role', self.role,
                                self.data, is_new=self._is_new or force)
        self._is_new = False
        if n:
            log.info('role %s saved, %d fields', self.role, n)
        return n

    async def do_save(self):
        """周期落库入口: _save_loop 经 actor 邮箱调用，与业务消息串行"""
        return await self.save()

    # ---------------------------------------------------------------
    # 客户端消息（player.xxx -> do_xxx）
    # ---------------------------------------------------------------
    async def do_chat(self, data):
        text = str(data.get('text', ''))[:200]
        log.info('[%s] %s(%s) chat: %s', CTX.service_name, self.name, self.role, text)
        # 经全局进程做全服跑马灯广播（展示用角色名）
        api.send_service('master', 'world.chatMarquee',
                         self.name, text, CTX.service_name)
        return {'ok': True}

    async def do_get_info(self, data):
        return {
            'account': self.account,
            'role': self.role,
            'name': self.data.name,
            'sex': self.data.sex,
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
        log.notice('role %s recharge %s x%s, diamond=%d',
                   self.role, item, count, self.diamond)
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
        logout(self.role, self)

    def on_hotfix(self):
        # 引擎只重绑 Player 自己的类；若持有嵌套业务对象（如 self.abc = Abc()），
        # 它的类不会自动更新，需在此手动重绑: self.rebind_fields('abc')
        log.info(f'role {self.role} hotfixed (class rebound)')


# 热更新安全：reload 复用模块 __dict__，保住在线玩家表
if '_players' not in globals():
    _players = {}  # role -> Player


def logout(role, player):
    if _players.get(role) is player:
        del _players[role]
    player.stop()
    api.send_service('master', 'world.playerLogout', role, CTX.service_name)
    log.notice('role %s logout from %s', role, CTX.service_name)


# ---------------------------------------------------------------------
# handler 入口
# ---------------------------------------------------------------------
@handler('game.enter', public=True)
async def enter(session: ClientSession, data):
    """客户端进入游戏：验证令牌 -> 加载角色档 -> 创建玩家 actor

    data: {account, role, token}；角色档由 login.createRole 提前建好，
    这里只加载不创建
    """
    # 只认真实客户端连接：集群内 RPC 的参数走 JSON，伪造不出 ClientSession，
    # 不拦的话会在 session.player 赋值处炸掉，还留下绑定过的幽灵玩家 actor
    if not isinstance(session, ClientSession):
        log.warning('game.enter rejected: not a client session (%r)', session)
        return {'ok': False, 'error': 'invalid session'}
    account = str(data.get('account', ''))
    role = str(data.get('role', ''))
    token = str(data.get('token', ''))
    if not account or not role or not token:
        return {'ok': False, 'error': 'account/role/token required'}

    result = await api.call_service(
        'master', 'world.verifyToken', account, role, token, CTX.service_name)
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error', 'verify failed')}

    # 加载角色档，且必须属于本账号（防止拿别人的 role_id 进号）
    doc = await db.get().load(COLL_PLAYER, 'role', role)
    if doc is None or doc.get('account') != account:
        return {'ok': False, 'error': 'role not found'}

    # 已在的（同进程同角色重进）先清掉
    old = _players.get(role)
    if old is not None:
        old.tell('kick', 'reenter')

    doc.pop('role', None)  # role 是主键，不进 DirtyRecord

    # bind 后本进程内可经 api.actor_ref 寻址，跨进程走 sys.tellActor/sys.askActor
    player = Player(account, role, session, doc, False).bind('player:' + role).start()
    _players[role] = player
    session.player = player
    session.account = account
    # 登记在线位置（放在 verifyToken 之外：那时档还没加载，可能失败）
    api.send_service('master', 'world.playerOnline', account, role, CTX.service_name)
    log.notice('player %s role=%s enter %s', account, role, CTX.service_name)
    return {'ok': True, 'player': await player.ask('get_info', {})}


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
    """每 dataSaveInterval 秒把所有在线玩家的脏字段增量写回 Mongo

    限流并发（dataSaveConcurrency，默认 20）：逐个 await 是纯串行削峰，
    玩家规模大了来不及；不限并发则每周期对 Mongo 打一波齐射尖峰。
    安全性不变：每个玩家的脏字段仍只在自己的 actor 邮箱里读取。"""
    interval = CTX.config.get('dataSaveInterval', 30)
    sem = asyncio.Semaphore(CTX.config.get('dataSaveConcurrency', 20))

    async def save_one(player):
        async with sem:
            try:
                await player.ask('save')
            except Exception:
                log.exception(f'save role {player.role} failed')

    while True:
        await asyncio.sleep(interval)
        # gather 等本轮全部落完再进入下一周期，避免周期间叠加
        await asyncio.gather(*(save_one(p) for p in list(_players.values())))


def init():
    asyncio.ensure_future(_save_loop())
    log.notice('%s (player process) inited', CTX.service_name)


async def drain():
    """优雅缩容排干（sys.drain 调用）：把所有在线玩家踢下线

    do_kick 链路: 落库 -> 给客户端推 sys.kick -> 关连接
    -> session 关闭回调给 actor 投 __logout__（再兜底落库 + 通知 world）
    所以踢完后还要等 _players 清空，确保数据全部落库。"""
    players = list(_players.values())
    if not players:
        return
    log.notice(f'{CTX.service_name} draining: kicking {len(players)} players')
    for player in players:
        try:
            await player.ask('kick', 'shrink')
        except Exception:
            log.exception(f'kick role {player.role} failed')
    # 等 __logout__ 处理完（session 关闭是异步的）
    for _ in range(300):
        if not _players:
            break
        await asyncio.sleep(0.1)
    if _players:
        log.warning(f'{CTX.service_name} drain timeout, {len(_players)} players left')
