# coding:utf-8
"""当前服务进程上下文（每个进程一个单例）"""


class Context(object):
    def __init__(self):
        self.service_name = None      # 本服务名，如 game1 / master
        self.config = None            # 整个 config.json
        self.service_config = None    # 本服务的配置
        self.internal = None          # 组内 RPC 节点 (rpc.internal.InternalNet)
        self.cluster = None           # 组间 RPC 节点 (rpc.cluster.ClusterNet)，仅 master 有
        self.tcp_server = None        # 面向客户端的 TCP 服务器
        self.web_server = None        # HTTP 服务器
        self.all_ready_handler = None # 全部服务就绪回调
        self.stop_handler = None      # 停止回调
        self.is_stopping = False


CTX = Context()
