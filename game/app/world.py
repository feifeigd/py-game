"""
game 组全局进程（world，运行在 master 服务中）

职责:
  - 登录令牌管理（login 组跨组写入，gameN 进程组内验证）
  - 玩家位置表（账号 -> 所在 gameN 进程）
  - 充值路由（recharge 组跨组打入 -> 转发到玩家所在进程）
  - 全服聊天跑马灯（收集玩家聊天 -> 广播到所有 gameN 进程）
  - 定期向 center 组上报在线人数

全局逻辑收敛在一个全局 Actor（WorldActor）里，handler 只是入口包装，
跨进程消息在 actor 邮箱中串行处理，无需加锁。
"""
import asyncio

from engine import api
from engine.context import CTX
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.util import log


class WorldActor(Actor):
    """全局进程的主 actor：所有全局状态都在这里"""

    def __init__(self):
        super().__init__()
        self.tokens = {}          # account -> token（待验证的登录令牌）
        self.player_locations = {}  # account -> 所在服务名(game1/game2)
        self.chat_count = 0
        self.offline_recharges = {}  # account -> [(item, count)] 玩家不在线的充值

    # ---------------------------------------------------------------
    async def do_prepare_login(self, account, token):
        """login 组跨组调用：登记登录令牌"""
        self.tokens[account] = token
        log.info('prepareLogin account=%s', account)
        return True

    async def do_verify_token(self, account, token, service_name):
        """gameN 进程组内调用：验证令牌并登记玩家位置"""
        if self.tokens.get(account) != token:
            log.warning('verifyToken failed account=%s', account)
            return {'ok': False, 'error': 'invalid token'}
        del self.tokens[account]

        # 顶号：账号已在其它进程在线，直接寻址那边的玩家 actor 踢掉
        old_service = self.player_locations.get(account)
        if old_service and old_service != service_name:
            log.notice('account %s re-login, kick from %s', account, old_service)
            api.actor_ref('player:' + account, service=old_service).tell('kick')

        self.player_locations[account] = service_name
        log.notice('player %s login at %s (online=%d)',
                   account, service_name, len(self.player_locations))
        return {'ok': True}

    async def do_player_logout(self, account, service_name):
        if self.player_locations.get(account) == service_name:
            del self.player_locations[account]
        log.notice('player %s logout (online=%d)', account, len(self.player_locations))

    async def do_on_recharge(self, account, item, count):
        """recharge 组跨组打入的充值：直接寻址玩家所在进程的玩家 actor"""
        service_name = self.player_locations.get(account)
        if service_name:
            try:
                await api.actor_ref('player:' + account, service=service_name) \
                    .ask('recharge', item, count)
                return {'ret': 0, 'msg': f'recharge delivered to {service_name}'}
            except Exception as exc:
                # actor 刚好掉线等投递失败，落入离线充值兜底
                log.warning('deliver recharge to %s at %s failed: %s',
                            account, service_name, exc)
        # 玩家不在线，挂离线充值
        self.offline_recharges.setdefault(account, []).append((item, count))
        log.notice('offline recharge for %s: %s x%s', account, item, count)
        return {'ret': 0, 'msg': 'player offline, recharge pending'}

    async def do_chat_marquee(self, account, text, from_service):
        """全服聊天跑马灯：广播给所有 gameN 进程"""
        self.chat_count += 1
        api.broadcast_service('game.broadcastPush', 'chat.push', {
            'account': account, 'text': text,
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
@handler('world.prepareLogin')
def prepare_login(account, token):
    return world_actor.ask('prepare_login', account, token)


@handler('world.verifyToken')
def verify_token(account, token, service_name):
    return world_actor.ask('verify_token', account, token, service_name)


@handler('world.playerLogout')
def player_logout(account, service_name):
    return world_actor.ask('player_logout', account, service_name)


@handler('recharge.onRecharge')
def on_recharge(account, item, count):
    return world_actor.ask('on_recharge', account, item, count)


@handler('world.chatMarquee')
def chat_marquee(account, text, from_service):
    return world_actor.ask('chat_marquee', account, text, from_service)


@handler('world.stats')
def get_stats():
    return world_actor.ask('get_stats')


# ---------------------------------------------------------------------
async def _report_loop():
    """每 30 秒向 center 上报一次在线人数"""
    while True:
        await asyncio.sleep(30)
        try:
            await world_actor.ask('report_center')
        except Exception:
            log.exception('report loop error')


def init():
    world_actor.start()
    asyncio.ensure_future(_report_loop())
    log.notice('world (global process) inited')


def on_hotfix():
    """热更新钩子：模块 reload 后调用"""
    log.notice('world hotfixed, online=%d', len(world_actor.player_locations))
