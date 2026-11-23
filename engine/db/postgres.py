"""PostgreSQL 驱动封装（asyncpg），login / recharge / center 进程组使用

增量更新语义: update_columns() 只 UPDATE 脏列; upsert() 冲突时同样只更新脏列。
表名 / 列名一律走 _ident 白名单校验，值全部参数化。
"""
import re

from engine.util import log

_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _ident(name):
    if not _IDENT.match(name):
        raise ValueError('illegal identifier: %r' % name)
    return '"%s"' % name


class PostgresDB:
    def __init__(self, dsn):
        self._dsn = dsn
        self._pool = None

    async def start(self):
        import asyncpg
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.fetchval('SELECT 1')
        log.notice('postgres connected')

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ---------------------------------------------------------------
    # 通用
    # ---------------------------------------------------------------
    async def execute(self, sql, *args):
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchrow(self, sql, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        async with self._pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    # ---------------------------------------------------------------
    # 结构化写
    # ---------------------------------------------------------------
    async def insert(self, table, data):
        """整行插入, data: {列名: 值}"""
        cols = list(data)
        sql = 'INSERT INTO %s (%s) VALUES (%s)' % (
            _ident(table),
            ', '.join(_ident(c) for c in cols),
            ', '.join('$%d' % (i + 1) for i in range(len(cols))))
        await self.execute(sql, *[data[c] for c in cols])

    async def update_columns(self, table, pk_col, pk_val, dirty):
        """增量更新: 只 UPDATE dirty 里变了的列

        @return 更新的列数
        """
        if not dirty:
            return 0
        cols = list(dirty)
        sql = 'UPDATE %s SET %s WHERE %s = $1' % (
            _ident(table),
            ', '.join('%s = $%d' % (_ident(c), i + 2) for i, c in enumerate(cols)),
            _ident(pk_col))
        await self.execute(sql, pk_val, *[dirty[c] for c in cols])
        return len(cols)

    async def upsert(self, table, pk_col, pk_val, dirty, extra=None):
        """按主键 upsert: 不存在插入整行, 存在只更新脏列

        extra: 插入时需要、但不属于脏数据的列（如 created_at）
        """
        row = dict(extra or {})
        row[pk_col] = pk_val
        row.update(dirty)
        cols = list(row)
        update_cols = list(dirty)
        sql = 'INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (%s) DO UPDATE SET %s' % (
            _ident(table),
            ', '.join(_ident(c) for c in cols),
            ', '.join('$%d' % (i + 1) for i in range(len(cols))),
            _ident(pk_col),
            ', '.join('%s = EXCLUDED.%s' % (_ident(c), _ident(c)) for c in update_cols))
        await self.execute(sql, *[row[c] for c in cols])
        return len(update_cols)
