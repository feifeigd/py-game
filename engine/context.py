"""当前服务进程上下文（每个进程一个单例）"""


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.net.server import TcpServer, WsServer
    from engine.net.web import WebServer
    from engine.rpc.cluster import ClusterNet
    from engine.rpc.internal import InternalNet


class Context:
    def __init__(self):
        self.service_name: str | None = None      # 本服务名，如 game1 / master
        self.config = {}            # 整个 config.json
        self.service_config = {}    # 本服务的配置
        self.internal: InternalNet | None = None          # 组内 RPC 节点 (rpc.internal.InternalNet)
        self.cluster: ClusterNet | None = None           # 组间 RPC 节点 (rpc.cluster.ClusterNet)，仅 master 有
        self.tcp_server: TcpServer | None = None        # 面向客户端的 TCP 服务器
        self.web_server: WebServer | None = None        # HTTP 服务器
        self.ws_server: WsServer | None = None  # websocket 服务器
        self.all_ready_handler = None # 全部服务就绪回调
        self.stop_handler = None      # 停止回调
        self.is_stopping = False

# 单例
CTX = Context()
