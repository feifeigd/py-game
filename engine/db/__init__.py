"""数据层统一入口

每个服务进程启动时按本组 config.json 的 db 段初始化:

    "db": {"type": "mongo",    "url": "mongodb://root:123456@127.0.0.1:27017", "name": "pygame"}
    "db": {"type": "postgres", "dsn": "postgresql://postgres:123456@127.0.0.1:5432/pygame"}

业务用法:
    from engine import db
    await db.get().load('player', 'account', account)     # mongo
    await db.get().update_columns('accounts', ...)        # postgres
驱动惰性 import: 只配了一种库的进程组不需要装另一种驱动。
"""
import asyncio

from engine.db.base import DirtyRecord  # noqa: F401 业务侧统一从这里拿
from engine.util import log

_db = None


async def init(db_config, retries=30, retry_interval=2):
    """按配置建连接; 容器刚启动时可能还没就绪, 重试若干次后放弃(致命)"""
    global _db
    db_type = db_config.get('type')
    if db_type == 'mongo':
        from engine.db.mongo import MongoDB
        _db = MongoDB(db_config['url'], db_config.get('name', 'pygame'))
    elif db_type == 'postgres':
        from engine.db.postgres import PostgresDB
        _db = PostgresDB(db_config['dsn'])
    else:
        raise ValueError('unknown db type: %r' % db_type)

    last_exc = None
    for i in range(retries):
        try:
            await _db.start()
            return
        except Exception as exc:
            last_exc = exc
            if i < retries - 1:
                log.warning('db connect failed (%s), retry %d/%d',
                            exc, i + 1, retries)
                await asyncio.sleep(retry_interval)
    raise RuntimeError('db connect failed after %d retries' % retries) from last_exc


def get():
    assert _db is not None, 'db not initialized (config.json missing db section?)'
    return _db


def has_db():
    return _db is not None
