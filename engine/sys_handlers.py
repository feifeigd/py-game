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

debug（tools/cli.py）:
    sys.listKeys / sys.listActors   列出本进程的 handler / 具名 actor
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
_expected_total = 0   # 首次 nodeReady（master 自己）时快照，先于任何动态扩容
_stages_done = False  # load/init/allReady 只对初始全员广播一次


@handler('sys.nodeReady')
def node_ready(service_name):
    """仅 master 处理：统计就绪的服务数，齐了之后广播 load/init/allReady"""
    global _expected_total, _stages_done
    if not _expected_total:
        _expected_total = len(CTX.config['services'])
    _ready_nodes.add(service_name)
    log.notice('service %s ready (%d/%d)', service_name,
               len(_ready_nodes), _expected_total)
    # 动态扩容加入的节点不得再次触发全员广播（重复 init 会产生重复后台任务），
    # 它的 load/init/allReady 由 sys.startService 单独下发
    if _stages_done or len(_ready_nodes) < _expected_total:
        return
    _stages_done = True
    asyncio.ensure_future(_broadcast_stages())


async def _broadcast_stages():
    internal = CTX.internal
    if internal is None:
        raise RuntimeError('internal RPC node is not initialized')
    for stage in ('sys.load', 'sys.init', 'sys.allReady'):
        await handler_reg.dispatch(stage)                    # master 自己
        await internal.broadcast_call(stage)                  # 其它服务
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

        module = sys.modules.get(app)
        if not module:
            log.error("module %s not found!", app)
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
# 分布式 actor 寻址（api.actor_ref 的落点）
# ---------------------------------------------------------------------
@handler('sys.tellActor')
def tell_actor(actor_id, key, *args, **kwargs):
    """向本进程的具名 actor 投递消息（fire-and-forget）"""
    from engine.core.actor import find_actor
    actor = find_actor(actor_id)
    if actor is None:
        log.warning('sys.tellActor: %s not found in %s', actor_id, CTX.service_name)
        return False
    actor.tell(key, *args, **kwargs)
    return True


@handler('sys.askActor')
async def ask_actor(actor_id, key, *args, **kwargs):
    """向本进程的具名 actor 投递消息并等待结果"""
    from engine.core.actor import find_actor
    actor = find_actor(actor_id)
    if actor is None:
        raise RuntimeError(f'actor {actor_id} not found in {CTX.service_name}')
    return await actor.ask(key, *args, **kwargs)


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


@handler('sys.serviceUp')
def service_up(group, service, addrs):
    """其它进程组的新节点上线通知（默认忽略，业务可覆盖注册同 key）"""
    log.info(f'service up: {group}.{service} {addrs}')


@handler('sys.serviceDown')
def service_down(group, service):
    """其它进程组的节点下线通知（默认忽略，业务可覆盖注册同 key）"""
    log.info(f'service down: {group}.{service}')


@handler('sys.execCode')
def exec_code(code):
    """远程执行 python 代码（GM 用），返回 'success'"""
    exec(code, globals())
    return 'success'


# ---------------------------------------------------------------------
# debug（tools/cli.py 用）
# ---------------------------------------------------------------------
@handler('sys.listKeys')
def list_keys():
    """列出本进程注册的所有 handler，按 public / internal 分组"""
    keys = handler_reg.all_keys()
    return {
        'service': CTX.service_name,
        'public': sorted(k for k in keys if handler_reg.is_public(k)),
        'internal': sorted(k for k in keys if not handler_reg.is_public(k)),
    }


@handler('sys.listActors')
def list_actors():
    """列出本进程内所有具名 actor 的 id"""
    from engine.core.actor import _named_actors
    return sorted(_named_actors.keys())


# ---------------------------------------------------------------------
# 动态扩容
# ---------------------------------------------------------------------
def _derive_service_config(services, template, name):
    """从模板服务克隆一份配置，各类端口取全组同类型最大值 +1"""
    import copy
    cfg = copy.deepcopy(services[template])
    cfg['port'] = max(s['port'] for s in services.values()) + 1
    for server_key in ('tcpServer', 'wsServer', 'webServer'):
        if server_key not in cfg:
            continue
        ports = [int(s[server_key]['addr'].rsplit(':', 1)[1])
                 for s in services.values() if server_key in s]
        host = cfg[server_key]['addr'].rsplit(':', 1)[0]
        cfg[server_key]['addr'] = f'{host}:{max(ports) + 1}'
    # 标记为动态节点：internal 组网时反向拨号所有存量节点
    cfg['dynamic'] = True
    return cfg


@handler('sys.startService')
async def start_service(name, template):
    """master 入口：按模板动态拉起一个新服务进程（如 game3 克隆自 game1）

    流程: 派生配置（端口自动分配）-> 加入配置表 -> spawn 子进程
          -> 等新节点组网就绪 -> 单独下发 load/init/allReady
          -> 向其它进程组广播 sys.serviceUp（携带客户端入口地址）
    """
    if CTX.service_name != MASTER_SERVICE:
        return {'ok': False, 'error': 'only master can start service'}
    services = CTX.config['services']
    if name in services:
        return {'ok': False, 'error': f'service {name} already exists'}
    if template not in services:
        return {'ok': False, 'error': f'template {template} not found'}

    cfg = _derive_service_config(services, template, name)
    services[name] = cfg

    from engine.start.master import spawn_service
    if spawn_service(name, CTX.config) is None:
        del services[name]
        return {'ok': False, 'error': 'spawn failed (not in master process?)'}

    # 等新节点完成组网并报告 nodeReady
    for _ in range(300):
        if name in _ready_nodes:
            break
        await asyncio.sleep(0.1)
    else:
        return {'ok': False, 'error': f'{name} nodeReady timeout', 'config': cfg}

    # 初始全员广播早已结束，对新节点单独走启动流程
    for stage in ('sys.load', 'sys.init', 'sys.allReady'):
        await CTX.internal.call(name, stage)

    # 通知其它进程组（login 等）新节点上线，携带客户端入口地址
    addrs = {}
    for server_key, label in (('tcpServer', 'tcp'), ('wsServer', 'ws'),
                              ('webServer', 'web')):
        if server_key in cfg:
            addrs[label] = cfg[server_key]['addr']
    if CTX.cluster is not None:
        CTX.cluster.broadcast(
            'sys.serviceUp', CTX.cluster.name, name, addrs)

    log.notice('service %s started dynamically: %s', name, addrs)
    return {'ok': True, 'config': cfg, 'addrs': addrs}


@handler('sys.drain')
async def drain():
    """优雅缩容的排干阶段（在被下线服务上执行）:
    置 draining（接入层开始拒收客户端消息）-> 业务 drain 钩子（踢人/落库等，
    app 模块可选实现）-> 关所有监听。先踢人再关监听，保证玩家能收到 sys.kick 推送。"""
    CTX.draining = True
    app = CTX.service_config.get('app')
    module = sys.modules.get(app) if app else None
    drain_func = getattr(module, 'drain', None) if module else None
    if drain_func:
        result = drain_func()
        if asyncio.iscoroutine(result):
            await result
    if CTX.tcp_server:
        await CTX.tcp_server.stop()
    if CTX.ws_server:
        await CTX.ws_server.stop()
    if CTX.web_server:
        await CTX.web_server.stop()
    log.notice('%s drained', CTX.service_name)
    return True


@handler('sys.stopService')
async def stop_service(name):
    """master 入口：优雅缩容——下线一个动态扩容的服务进程

    流程: 广播 sys.serviceDown（login 等先把节点移出分配池，挡住新登录）
          -> 目标服务 sys.drain（踢人落库、关监听）
          -> master 回收进程 -> 清配置
    只允许缩容动态扩容（dynamic 标记）的节点，静态配置的服务请改配置重启。
    """
    if CTX.service_name != MASTER_SERVICE:
        return {'ok': False, 'error': 'only master can stop service'}
    services = CTX.config['services']
    cfg = services.get(name)
    if cfg is None:
        return {'ok': False, 'error': f'service {name} not found'}
    if name == MASTER_SERVICE:
        return {'ok': False, 'error': 'cannot stop master'}
    if not cfg.get('dynamic'):
        return {'ok': False,
                'error': f'{name} is a static service, only dynamic ones can be stopped'}
    if not CTX.internal.is_online(name):
        return {'ok': False, 'error': f'{name} is offline'}

    # 1. 先广播下线，阻止新登录/新请求再路由过去
    if CTX.cluster is not None:
        CTX.cluster.broadcast('sys.serviceDown', CTX.cluster.name, name)

    # 2. 排干：踢人落库、关监听（可能较慢，给足超时）
    await CTX.internal.call(name, 'sys.drain', timeout=120)

    # 3. 回收进程
    from engine.start.master import terminate_service
    terminate_service(name)

    # 4. 清配置与就绪记录
    del services[name]
    _ready_nodes.discard(name)

    log.notice('service %s stopped dynamically', name)
    return {'ok': True, 'service': name}


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
