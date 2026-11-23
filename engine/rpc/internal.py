"""
进程组内部 RPC（intra-group）

同一进程组内的所有服务进程通过本机 TCP 互联:
  - 每个服务监听自己的 port（config.json 中 services.<name>.port）
  - 配置顺序靠前的服务向靠后的服务拨号（确定性单向，避免重复连接），断线自动重连
  - 按服务名路由: internal.call('game1', 'game.addRecharge', ...)
"""
from engine.core import handler as handler_reg
from engine.rpc.node import RpcNode


class InternalNet(RpcNode):
    hello_field = 'service'

    def __init__(self, name, services_config):
        service_config = services_config[name]
        super().__init__(
            name, "127.0.0.1:{}".format(service_config['port']))
        self._services_config = services_config

    async def start(self):
        # 确定性单向拨号：配置顺序靠后的服务由靠前的服务来连，
        # 避免双方互拨产生重复连接导致连接抖动
        names = list(self._services_config.keys())
        my_index = names.index(self.name)
        targets = {}
        for peer_name in names[my_index + 1:]:
            cfg = self._services_config[peer_name]
            targets[peer_name] = ('127.0.0.1', int(cfg['port']))
        await super().start(targets)

    async def on_request(self, key, args, kwargs):
        return await handler_reg.dispatch(key, *args, **kwargs)
