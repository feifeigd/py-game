"""
应用层统一调用接口

组内调用（同一进程组的两个服务进程之间）:
    await api.call_service('game1', 'game.onlineCount')
    api.send_service('master', 'world.playerLogout', account)
    api.broadcast_service('sys.xxx', ...)          # 组内广播

跨组调用（进程组之间，自动经本组 master 转发）:
    await api.call_cluster('game1', 'world.prepareLogin', account, token)
    api.send_cluster('center', 'center.reportOnline', 'game1', 10)

跨组调用对端组内的指定服务:
    await api.call_cluster_service('game1', 'game2', 'player.kick', account)

推送消息给某个账号所在客户端（需要先能定位到进程）:
    业务自行组合 call_service / call_cluster_service
"""
from engine.context import CTX
from engine.util import log

MASTER_SERVICE = 'master'


# ---------------------------------------------------------------------
# 组内
# ---------------------------------------------------------------------
def send_service(service_name, key, *args, **kwargs):
    return CTX.internal.send(service_name, key, *args, **kwargs)


async def call_service(service_name, key, *args, timeout=30, **kwargs):
    return await CTX.internal.call(service_name, key, *args, timeout=timeout, **kwargs)


def broadcast_service(key, *args, **kwargs):
    CTX.internal.broadcast(key, *args, **kwargs)


async def broadcast_service_call(key, *args, **kwargs):
    return await CTX.internal.broadcast_call(key, *args, **kwargs)


# ---------------------------------------------------------------------
# 跨组
# ---------------------------------------------------------------------
def _cluster_or_forward_send(cluster_name, key, args, kwargs):
    if CTX.cluster is not None:
        return CTX.cluster.send(cluster_name, key, *args, **kwargs)
    # 非 master 进程经本组 master 转发
    return CTX.internal.send(MASTER_SERVICE, 'sys.clusterSend',
                             cluster_name, key, *args, **kwargs)


async def _cluster_or_forward_call(cluster_name, key, args, kwargs, timeout):
    if CTX.cluster is not None:
        return await CTX.cluster.call(cluster_name, key, *args, timeout=timeout, **kwargs)
    return await CTX.internal.call(MASTER_SERVICE, 'sys.clusterCall',
                                   cluster_name, key, *args, timeout=timeout, **kwargs)


def send_cluster(cluster_name, key, *args, **kwargs):
    return _cluster_or_forward_send(cluster_name, key, args, kwargs)


async def call_cluster(cluster_name, key, *args, timeout=30, **kwargs):
    return await _cluster_or_forward_call(cluster_name, key, args, kwargs, timeout)


def send_cluster_service(cluster_name, service_name, key, *args, **kwargs):
    """跨组向对端组内指定服务发单向消息"""
    return send_cluster(cluster_name, 'sys.forwardService',
                        False, service_name, key, *args, **kwargs)


async def call_cluster_service(cluster_name, service_name, key, *args, timeout=30, **kwargs):
    """跨组调用对端组内指定服务的处理函数"""
    return await call_cluster(cluster_name, 'sys.forwardService',
                              True, service_name, key, *args, timeout=timeout, **kwargs)


# ---------------------------------------------------------------------
# 分布式 actor 通信（位置透明）
# ---------------------------------------------------------------------
class ActorRef:
    """位置透明的 actor 引用：与本地 Actor 相同的 tell/ask 接口。

    目标 actor 用 actor.bind('类型:ID') 绑定名字后，从任何进程寻址:
        ref = api.actor_ref('player:tester', service='game1')            # 组内某进程
        ref = api.actor_ref('player:tester', service='game1', cluster='game')  # 跨组
        ref = api.actor_ref('world:main')                                # 本进程
        ref.tell('kick')
        result = await ref.ask('get_info')
    """

    def __init__(self, actor_id, service_name=None, cluster_name=None):
        self.actor_id = actor_id
        self.service_name = service_name
        self.cluster_name = cluster_name

    def tell(self, key, *args, **kwargs):
        """异步投递，不等待结果"""
        if self.cluster_name:
            return send_cluster_service(self.cluster_name, self.service_name,
                                        'sys.tellActor', self.actor_id, key,
                                        *args, **kwargs)
        if self.service_name:
            return send_service(self.service_name, 'sys.tellActor',
                                self.actor_id, key, *args, **kwargs)
        actor = self._local()
        if actor is None:
            log.warning('ActorRef.tell: %s not found in local process', self.actor_id)
            return False
        actor.tell(key, *args, **kwargs)
        return True

    async def ask(self, key, *args, timeout=30, **kwargs):
        """异步投递并等待结果"""
        if self.cluster_name:
            return await call_cluster_service(self.cluster_name, self.service_name,
                                              'sys.askActor', self.actor_id, key,
                                              *args, timeout=timeout, **kwargs)
        if self.service_name:
            return await call_service(self.service_name, 'sys.askActor',
                                      self.actor_id, key, *args,
                                      timeout=timeout, **kwargs)
        actor = self._local()
        if actor is None:
            raise RuntimeError(f'actor {self.actor_id} not found in local process')
        return await actor.ask(key, *args, **kwargs)

    def _local(self):
        from engine.core.actor import find_actor
        return find_actor(self.actor_id)


def actor_ref(actor_id, service=None, cluster=None):
    """创建一个指向具名 actor 的位置透明引用（见 ActorRef docstring）"""
    return ActorRef(actor_id, service_name=service, cluster_name=cluster)
