"""login 组 master 服务（组内协调 + 跨组转发 + 库表结构初始化）"""
from engine import db
from engine.context import CTX
from engine.core.handler import handler
from engine.util import log


async def init():
    # 库表结构由 master 统一初始化（每个进程组各进程都会连 db，
    # 但 DDL 只应由 master 执行一次，worker 服务只负责用）
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            account     TEXT PRIMARY KEY,
            login_count INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL,
            last_login  TIMESTAMPTZ NOT NULL
        )''')
    log.notice('login master inited')


def _zone_of_cluster(group):
    """cluster 名 -> 大区号: 'game1001' -> '1001'，'game'（无 serverId）-> 默认 1001"""
    if not group.startswith('game'):
        return None
    return group[len('game'):] or '1001'


@handler('sys.serviceUp')
def on_service_up(group, service, addrs):
    """game 大区扩容通知 -> 转发给 login 服务更新对应大区的分配池"""
    zone = _zone_of_cluster(group)
    if zone is None:
        return
    log.notice(f'game service up: {group} {service} {addrs}')
    CTX.internal.send('login', 'login.serviceUp', zone, service, addrs)


@handler('sys.serviceDown')
def on_service_down(group, service):
    """game 大区缩容通知 -> 转发给 login 服务把节点移出对应大区的分配池"""
    zone = _zone_of_cluster(group)
    if zone is None:
        return
    log.notice(f'game service down: {group} {service}')
    CTX.internal.send('login', 'login.serviceDown', zone, service)
