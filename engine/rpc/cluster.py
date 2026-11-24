"""
进程组之间 RPC（inter-group cluster）

每个进程组由 master 服务暴露一个 clusterPort:
  - master 监听 clusterPort，接受其它进程组的连接
  - master 按 cluster.remoteAddrs 拨号到其它进程组，断线自动重连
  - 对端组名 = serverType + serverId（如 game1、center、recharge）

组内普通服务进程没有 cluster 连接，跨组调用经 master 转发:
  api.call_cluster('game1', 'world.prepareLogin', ...)
    -> 本组 master -> game 组 master -> 该组内的处理函数
跨组调用对端组内指定服务:
  api.call_cluster_service('game1', 'game2', 'xxx', ...)
    -> 对端 master 收到 sys.forwardService 后经 internal 转发给 game2
"""
from engine.core import handler as handler_reg
from engine.rpc.node import RpcNode


class ClusterNet(RpcNode):
    hello_field = 'cluster'

    def __init__(self, cluster_name, cluster_port, remote_addrs):
        super().__init__(cluster_name, f'0.0.0.0:{cluster_port}')
        self._remote_addrs = remote_addrs  # {对端组名: "host:port"}

    async def start(self):
        targets = {}
        for peer_name, addr in self._remote_addrs.items():
            host, port = addr.split(':')
            targets[peer_name] = (host, int(port))
        await super().start(targets)

    async def on_request(self, key, args, kwargs):
        return await handler_reg.dispatch(key, *args, **kwargs)
