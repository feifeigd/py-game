"""
系统级消息处理器（每个服务进程启动时自动注册）

启动握手:
    sys.nodeReady   各服务进程 -> master: 我启动好了
    sys.load        master -> 所有服务: 加载 app 模块
    sys.init        master -> 所有服务: 调用 app.init()
    sys.allReady    master -> 所有服务: 全部就绪

跨组转发（非 master 进程跨组调用时经 master 中转）:
    sys.clusterCall / sys.clusterSend / sys.forwardService

运维:
    sys.hotfix / sys.doHotfix   代码热更新
    sys.execCode                远程执行代码（GM）
    sys.stop                    优雅停服
"""
import asyncio
import sys

from engine import hotfix
from engine.api import MASTER_SERVICE
from engine.context import CTX
from engine.core import handler as handler_reg
from engine.core.handler import handler
from engine.util import log

# ---------------------------------------------------------------------
# 启动握手
# ---------------------------------------------------------------------
_ready_nodes = set()


@handler('sys.nodeReady')
def node_ready(service_name):
    """仅 master 处理：统计就绪的服务数，齐了之后广播 load/init/allReady"""
    _ready_nodes.add(service_name)
    total = len(CTX.config['services'])
    log.notice('service %s ready (%d/%d)', service_name, len(_ready_nodes), total)
    if len(_ready_nodes) < total:
        return
    asyncio.ensure_future(_broadcast_stages())


async def _broadcast_stages():
    for stage in ('sys.load', 'sys.init', 'sys.allReady'):
        await handler_reg.dispatch(stage)                    # master 自己
        await CTX.internal.broadcast_call(stage)             # 其它服务
    log.notice('==== all services started ====')


@handler('sys.load')
async def load():
    """加载本服务的 app 模块（import 即完成 handler 注册）"""
    app = CTX.service_config.get('app')
    if app:
        __import__(app)
        log.notice('app %s loaded', app)


@handler('sys.init')
async def init():
    """调用 app.init()"""
    app = CTX.service_config.get('app')
    if app:
        module = sys.modules[app]
        init_func = getattr(module, 'init', None)
        if init_func:
            result = init_func()
            if asyncio.iscoroutine(result):
                await result
        log.notice('app %s inited', app)


@handler('sys.allReady')
async def all_ready():
    if CTX.all_ready_handler:
        result = CTX.all_ready_handler()
        if asyncio.iscoroutine(result):
            await result
    log.notice('%s service started', CTX.service_name)


# ---------------------------------------------------------------------
# 跨组转发（在 master 上执行）
# ---------------------------------------------------------------------
@handler('sys.clusterCall')
async def cluster_call(cluster_name, key, *args, **kwargs):
    return await CTX.cluster.call(cluster_name, key, *args, **kwargs)


@handler('sys.clusterSend')
def cluster_send(cluster_name, key, *args, **kwargs):
    return CTX.cluster.send(cluster_name, key, *args, **kwargs)


@handler('sys.forwardService')
async def forward_service(is_call, service_name, key, *args, **kwargs):
    """跨组调用本组内指定服务（对端 master 收到后经 internal 转发）"""
    if is_call:
        return await CTX.internal.call(service_name, key, *args, **kwargs)
    return CTX.internal.send(service_name, key, *args, **kwargs)


# ---------------------------------------------------------------------
# 热更新 / 运维
# ---------------------------------------------------------------------
@handler('sys.hotfix')
async def hotfix_dispatch(service='all', package='app'):
    """
    master 入口（由 tools/hotfix.py 经 cluster 端口调用）:
        service='all'  -> 组内所有服务都热更
        service='game1'-> 只热更指定服务
    """
    if service == 'all':
        result = {CTX.service_name: do_hotfix(package)}
        others = await CTX.internal.broadcast_call('sys.doHotfix', package)
        result.update(others)
        return result
    if service == CTX.service_name:
        return {service: do_hotfix(package)}
    return {service: await CTX.internal.call(service, 'sys.doHotfix', package)}


@handler('sys.doHotfix')
def do_hotfix(package='app'):
    """本进程执行热更新"""
    log.notice('%s hotfix package=%s', CTX.service_name, package)
    return hotfix.reload_package(package)


@handler('sys.execCode')
def exec_code(code):
    """远程执行 python 代码（GM 用），返回 'success'"""
    exec(code, globals())
    return 'success'


@handler('sys.stop')
async def stop():
    """优雅停服：关监听 -> 业务 stop 钩子 -> 退出"""
    if CTX.is_stopping:
        return
    CTX.is_stopping = True
    log.notice('%s is stopping...', CTX.service_name)
    try:
        if CTX.tcp_server:
            await CTX.tcp_server.stop()
        if CTX.web_server:
            await CTX.web_server.stop()
        if CTX.stop_handler:
            result = CTX.stop_handler()
            if asyncio.iscoroutine(result):
                await result
        from engine import db
        if db.has_db():
            await db.get().close()
    except Exception:
        log.exception('stop error')
    log.notice('%s stopped, bye', CTX.service_name)
    asyncio.get_event_loop().call_later(1, sys.exit, 0)
