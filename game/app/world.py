"""
game 组全局进程（world，运行在 master 服务中）

职责:
  - 登录令牌管理（login 组跨组写入，gameN 进程组内验证；令牌绑 role_id，
    带 TTL——config loginTokenTtl 秒数，默认 300，过期拒验并定期清扫）
  - 玩家位置表（role_id -> 所在 gameN 进程）
  - 角色列表查询（login 组跨组调用，多角色选角用）
  - 充值路由（recharge 组跨组打入 -> 按 role 转发到玩家所在进程）
  - 全服聊天跑马灯（收集玩家聊天 -> 广播到所有 gameN 进程）
  - 定期向 center 组上报在线人数

多角色模型: 一个账号在一个服可有多个角色（合服后常见），身份一律用
role_id（player 档主键），account 只用于展示与按号查角色列表。
单账号单服角色数有上限（config maxRolesPerAccount，默认 4），防止无限创角。
顶号策略由 config singleRoleOnline 决定（默认 true）:
  true  = 单角色在线（微信登录这类平台账号），进新角色踢掉本账号其它在线角色
  false = 同账号多个角色可同时在线，顶号只踢同 role 的重登

全局逻辑收敛在一个全局 Actor（WorldActor）里，handler 只是入口包装，
跨进程消息在 actor 邮箱中串行处理，无需加锁。
"""
import asyncio
import time

from bson import ObjectId

from app.const import (
    COLL_PLAYER,
    DEFAULT_DATA,
    MAX_ROLES_PER_ACCOUNT,
    ROLE_NAME_MAX,
    SEX_MALE,
    SEXES,
)
from engine import api, db
from engine.context import CTX
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.db.mongo import DuplicateKeyError
from engine.util import log


class WorldActor(Actor):
    """全局进程的主 actor：所有全局状态都在这里"""

    def __init__(self):
        super().__init__()
        self.tokens = {}            # (account, role) -> (token, 过期时间戳)
        self.player_locations = {}  # role -> 所在服务名(game1/game2)
        self.role_accounts = {}     # role -> account（在线角色反查账号）
        self.account_roles = {}     # account -> set(role)（顶号策略只扫本账号角色）
        self.chat_count = 0
        self.offline_recharges = {}  # role -> [(item, count)] 玩家不在线的充值

    # ---------------------------------------------------------------
    async def do_create_role(self, account, name, sex):
        """login 组跨组调用：在本服创建角色（玩家自选角色名与性别）

        角色名本服唯一：name 上有唯一索引兜底，先 count 预检只是为了
        给出友好报错（并发撞名走 DuplicateKeyError）。

        role_id = {real_id}_{objectid}：服号前缀让各服命名空间天然隔离，
        合服搬档后角色 ID 永不冲突；ID 本身不改写，前缀记录出生服，
        现居服由 servers 表的 real_id 重定向解析（见 login.resolveServer）。
        """
        name = str(name).strip()
        if not 1 <= len(name) <= ROLE_NAME_MAX:
            return {'ok': False, 'error': f'name length must be 1..{ROLE_NAME_MAX}'}
        if sex not in SEXES:
            return {'ok': False, 'error': 'invalid sex'}
        # 单账号单服角色数上限（config maxRolesPerAccount 可调）：
        # 防止一个账号无限创角撑爆库
        max_roles = int(CTX.config.get(
            'maxRolesPerAccount', MAX_ROLES_PER_ACCOUNT))
        if await db.get().count(COLL_PLAYER, {'account': account}) >= max_roles:
            return {'ok': False, 'error': f'role limit reached (max {max_roles})'}
        if await db.get().count(COLL_PLAYER, {'name': name}):
            return {'ok': False, 'error': 'name taken'}
        server_id = CTX.config['serverId']
        role = f'{server_id}_{ObjectId()}'
        doc = dict(DEFAULT_DATA)
        doc.update({'role': role, 'account': account, 'name': name, 'sex': sex,
                    'login_time': int(time.time() * 1000)})
        try:
            await db.get().insert(COLL_PLAYER, doc)
        except DuplicateKeyError:
            return {'ok': False, 'error': 'name taken'}
        log.notice(f'role created: account={account} role={role} '
                   f'name={name} sex={sex}')
        return {'ok': True, 'role': role, 'name': name, 'sex': sex}

    async def do_prepare_login(self, account, token, role):
        """login 组跨组调用：登记登录令牌（带 TTL，过期自动失效并被清扫）"""
        if not role:
            # role 是令牌键的一部分，空 role 的令牌永远没有 verify 路径能清掉
            log.warning(f'prepareLogin rejected: empty role account={account}')
            return False
        expire = time.time() + CTX.config.get('loginTokenTtl', 300)
        self.tokens[(account, role)] = (token, expire)
        log.info(f'prepareLogin account={account} role={role}')
        return True

    async def do_verify_token(self, account, role, token, service_name):
        """gameN 进程组内调用：验证令牌并登记玩家位置"""
        entry = self.tokens.get((account, role))
        if entry is None or entry[0] != token:
            log.warning('verifyToken failed account=%s role=%s', account, role)
            return {'ok': False, 'error': 'invalid token'}
        if entry[1] <= time.time():
            # 令牌过期：客户端拖太久才来连 game，回 login 重新走登录流程
            del self.tokens[(account, role)]
            log.notice(f'token expired account={account} role={role}')
            return {'ok': False, 'error': 'token expired'}
        del self.tokens[(account, role)]

        # 顶号：同一角色已在其它进程在线，直接寻址那边的玩家 actor 踢掉
        old_service = self.player_locations.get(role)
        if old_service and old_service != service_name:
            log.notice(f'role {role} re-login, kick from {old_service}')
            api.actor_ref('player:' + role, service=old_service).tell('kick')

        # 单角色在线策略（config singleRoleOnline，默认开）：
        # 微信登录这类平台账号一次只允许玩一个角色，
        # 新角色进游戏时把本账号其它在线角色踢下线
        # （按 account_roles 反查只扫本账号角色，不全表遍历在线玩家）
        if CTX.config.get('singleRoleOnline', True):
            for other_role in list(self.account_roles.get(account, ())):
                if other_role == role:
                    continue
                other_service = self.player_locations.get(other_role)
                if other_service is None:
                    continue
                log.notice(f'account {account} enters role {role}, '
                           f'kick other role {other_role} from {other_service}')
                api.actor_ref('player:' + other_role,
                              service=other_service).tell('kick', 'relogin')

        log.notice('player %s role=%s login at %s (online=%d)',
                   account, role, service_name, len(self.player_locations))
        return {'ok': True}

    async def do_player_online(self, account, role, service_name):
        """角色加载完成后登记在线位置"""
        self.player_locations[role] = service_name
        self.role_accounts[role] = account
        self.account_roles.setdefault(account, set()).add(role)

    async def do_player_logout(self, role, service_name):
        if self.player_locations.get(role) == service_name:
            del self.player_locations[role]
        account = self.role_accounts.pop(role, None)
        if account is not None:
            roles = self.account_roles.get(account)
            if roles is not None:
                roles.discard(role)
                if not roles:
                    del self.account_roles[account]
        log.notice('role %s logout (online=%d)', role, len(self.player_locations))

    async def do_on_recharge(self, account, role, item, count):
        """recharge 组跨组打入的充值：按 role 寻址玩家 actor 发货

        role 缺省时按 account 查：该服只有一个角色就发给它，多个角色必须
        显式指定 role，否则拒绝（避免发错人）。
        """
        if not role:
            rows = await db.get().find(
                COLL_PLAYER, {'account': account}, ['role'])
            if len(rows) != 1:
                msg = 'role not found' if not rows else 'role required'
                log.warning('recharge for %s rejected: %s (%d roles)',
                            account, msg, len(rows))
                return {'ret': 1, 'msg': msg}
            role = rows[0]['role']
        else:
            doc = await db.get().load(COLL_PLAYER, 'role', role)
            if doc is None or doc.get('account') != account:
                log.warning('recharge for %s role=%s rejected: role not found',
                            account, role)
                return {'ret': 1, 'msg': 'role not found'}

        service_name = self.player_locations.get(role)
        if service_name:
            try:
                await api.actor_ref('player:' + role, service=service_name) \
                    .ask('recharge', item, count)
                return {'ret': 0, 'msg': f'recharge delivered to {service_name}'}
            except Exception as exc:
                # actor 刚好掉线等投递失败，落入离线充值兜底
                log.warning('deliver recharge to %s at %s failed: %s',
                            role, service_name, exc)
        # 玩家不在线，挂离线充值
        self.offline_recharges.setdefault(role, []).append((item, count))
        log.notice('offline recharge for %s role=%s: %s x%s',
                   account, role, item, count)
        return {'ret': 0, 'msg': 'player offline, recharge pending'}

    async def do_chat_marquee(self, name, text, from_service):
        """全服聊天跑马灯：广播给所有 gameN 进程（展示用角色名）"""
        self.chat_count += 1
        api.broadcast_service('game.broadcastPush', 'chat.push', {
            'name': name, 'text': text,
        })
        return self.chat_count

    async def do_report_center(self):
        """向 center 组上报在线人数（按 cluster 名区分服，如 game1001）"""
        online = len(self.player_locations)
        try:
            await api.call_cluster('center', 'center.reportOnline',
                                   CTX.cluster.name, online, timeout=5)
        except Exception as exc:
            log.warning('report to center failed: %s', exc)
        return online

    async def do_sweep_tokens(self):
        """清扫过期令牌：客户端拿了令牌没来连 game 的滞留项（周期任务调用）"""
        now = time.time()
        expired = [k for k, (_, expire) in self.tokens.items() if expire <= now]
        for k in expired:
            del self.tokens[k]
        if expired:
            log.notice(f'swept expired tokens: {len(expired)}')
        return len(expired)

    async def do_get_stats(self):
        return {
            'online': len(self.player_locations),
            'pendingTokens': len(self.tokens),
            'chatCount': self.chat_count,
        }


# 热更新安全：importlib.reload 会复用模块 __dict__，
# 用 globals() 守卫保住运行中的 actor 实例与状态，只换新类
if 'world_actor' not in globals():
    world_actor = WorldActor()
else:
    world_actor.__class__ = WorldActor


# ---------------------------------------------------------------------
# handler 入口（跨组 / 组内消息先到这里，再进 actor 邮箱）
# ---------------------------------------------------------------------
@handler('world.createRole')
def create_role(account, name, sex):
    return world_actor.ask('create_role', account, name, sex)


@handler('world.prepareLogin')
def prepare_login(account, token, role):
    return world_actor.ask('prepare_login', account, token, role)


@handler('world.verifyToken')
def verify_token(account, role, token, service_name):
    return world_actor.ask('verify_token', account, role, token, service_name)


@handler('world.playerOnline')
def player_online(account, role, service_name):
    return world_actor.ask('player_online', account, role, service_name)


@handler('world.playerLogout')
def player_logout(role, service_name):
    return world_actor.ask('player_logout', role, service_name)


@handler('world.listRoles')
async def list_roles(account):
    """login 组跨组调用：该账号在本服的角色列表（选角界面用）

    按最近登录时间倒序：第一个就是上次玩的角色，客户端默认选中它"""
    rows = await db.get().find(
        COLL_PLAYER, {'account': account},
        ['role', 'name', 'sex', 'level', 'login_time'])
    rows.sort(key=lambda r: r.get('login_time', 0), reverse=True)
    return rows


@handler('recharge.onRecharge')
def on_recharge(account, role, item, count):
    return world_actor.ask('on_recharge', account, role, item, count)


@handler('world.chatMarquee')
def chat_marquee(name, text, from_service):
    return world_actor.ask('chat_marquee', name, text, from_service)


@handler('world.stats')
def get_stats():
    return world_actor.ask('get_stats')


# ---------------------------------------------------------------------
async def _report_loop():
    """每 30 秒向 center 上报一次在线人数，顺带清扫过期令牌"""
    while True:
        await asyncio.sleep(30)
        try:
            await world_actor.ask('report_center')
            await world_actor.ask('sweep_tokens')
        except Exception:
            log.exception('report loop error')


async def _migrate_roles():
    """老玩家档迁移 + 索引:
    - 没有 role 字段的历史档用文档 _id 补上当 role_id
    - 没有 sex / login_time 字段的历史档补默认值
    - account 建索引（按号查角色列表走它）；name 建唯一索引（创角重名兜底）
    """
    gdb = db.get()
    docs = await gdb.find_with_id(COLL_PLAYER, {'role': {'$exists': False}})
    for doc in docs:
        await gdb.update_by_id(COLL_PLAYER, doc['_id'], {'role': str(doc['_id'])})
    if docs:
        log.notice(f'player docs migrated with role_id: {len(docs)}')
    docs = await gdb.find_with_id(COLL_PLAYER, {'sex': {'$exists': False}})
    for doc in docs:
        await gdb.update_by_id(COLL_PLAYER, doc['_id'], {'sex': SEX_MALE})
    if docs:
        log.notice(f'player docs migrated with default sex: {len(docs)}')
    docs = await gdb.find_with_id(
        COLL_PLAYER, {'login_time': {'$exists': False}})
    for doc in docs:
        await gdb.update_by_id(COLL_PLAYER, doc['_id'], {'login_time': 0})
    if docs:
        log.notice(f'player docs migrated with default login_time: {len(docs)}')
    await gdb.create_index(COLL_PLAYER, 'account')
    try:
        await gdb.create_index(COLL_PLAYER, 'name', unique=True)
    except Exception as exc:
        # 存量数据有重名时建不起来：告警但不影响启动（创角仍有 count 预检）
        log.warning(f'create unique index on player.name failed: {exc}')


def init():
    world_actor.start()
    asyncio.ensure_future(_report_loop())
    asyncio.ensure_future(_migrate_roles())
    log.notice('world (global process) inited')


def on_hotfix():
    """热更新钩子：模块 reload 后调用"""
    log.notice('world hotfixed, online=%d', len(world_actor.player_locations))
