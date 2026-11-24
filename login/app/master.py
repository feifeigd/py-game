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


@handler('sys.serviceUp')
def on_service_up(group, service, addrs):
    """game 组扩容通知 -> 转发给 login 服务更新 gameServers 分配池"""
    if group != 'game':
        return
    log.notice(f'game service up: {service} {addrs}')
    CTX.internal.send('login', 'login.serviceUp', service, addrs)


@handler('sys.serviceDown')
def on_service_down(group, service):
    """game 组缩容通知 -> 转发给 login 服务把节点移出分配池"""
    if group != 'game':
        return
    log.notice(f'game service down: {service}')
    CTX.internal.send('login', 'login.serviceDown', service)
