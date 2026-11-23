"""
单个服务进程的启动流程

每个服务进程:
  1. 初始化日志 / 上下文
  2. 注册系统处理器，启动组内 RPC 节点（监听 + 拨号组网）
  3. 若配置了 cluster（一般是 master）: 启动组间 RPC 节点
  4. 若配置了 tcpServer / webServer: 启动对客户端的监听
  5. 向 master 报告 sys.nodeReady，等待 master 广播 load/init/allReady
"""
import asyncio

from engine.context import CTX
from engine.core import handler as handler_reg
from engine.rpc.internal import InternalNet
from engine.util import log

MASTER_SERVICE = 'master'


async def main(name, config):
    log.init(name, config)
    CTX.service_name = name
    CTX.config = config
    CTX.service_config = config['services'][name]

    import engine.sys_handlers  # noqa: F401 注册 sys.* 处理器

    # 组内 RPC 组网
    CTX.internal = InternalNet(name, config['services'])
    await CTX.internal.start()

    # 组间 RPC（通常只有 master 配置）
    cluster_config = CTX.service_config.get('cluster')
    if cluster_config:
        from engine.rpc.cluster import ClusterNet
        server_id = config.get('serverId')
        cluster_name = config['serverType'] + (str(server_id) if server_id else '')
        CTX.cluster = ClusterNet(
            cluster_name,
            cluster_config['clusterPort'],
            cluster_config.get('remoteAddrs', {}))
        await CTX.cluster.start()

    # 面向客户端的 TCP 监听
    tcp_config = CTX.service_config.get('tcpServer')
    if tcp_config:
        from engine.net.server import TcpServer
        CTX.tcp_server = TcpServer(name, tcp_config['addr'])
        await CTX.tcp_server.start()

    # HTTP 监听（充值回调 / GM）
    web_config = CTX.service_config.get('webServer')
    if web_config:
        from engine.net.web import WebServer
        CTX.web_server = WebServer(
            name, web_config['addr'], web_config.get('maxConnection', 400))
        await CTX.web_server.start()

    # 数据库（本组 config.json 配了 db 段才启用，驱动惰性 import）
    db_config = config.get('db')
    if db_config:
        from engine import db
        await db.init(db_config)

    # 向 master 报告就绪
    if name == MASTER_SERVICE:
        await handler_reg.dispatch('sys.nodeReady', name)
    else:
        while not CTX.internal.is_online(MASTER_SERVICE) and not CTX.is_stopping:
            await asyncio.sleep(0.05)
        await CTX.internal.call(MASTER_SERVICE, 'sys.nodeReady', name)

    # 常驻
    await asyncio.Event().wait()


def run_service(name, config):
    """multiprocessing 子进程入口（必须是可 pickle 的顶层函数）"""
    try:
        asyncio.run(main(name, config))
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception('service %s crashed', name)
        raise
