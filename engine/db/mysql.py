"""MySQL 驱动封装（aiomysql），接口与 PostgresDB 一致

增量更新语义: update_columns() 只 UPDATE 脏列; upsert() 冲突时同样只更新脏列。
表名 / 列名一律走 _ident 白名单校验（反引号），值全部 %s 参数化。

注意: aiomysql 是 format 参数风格，SQL 里的字面量 % 要写成 %%。
"""
import re
import urllib.parse

from engine.util import log

_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _ident(name):
    if not _IDENT.match(name):
        raise ValueError(f'illegal identifier: {name!r}')
    return f'`{name}`'


class MySQLDB:
    def __init__(self, dsn):
        """dsn: mysql://user:password@host:port/dbname"""
        self._dsn = dsn
        self._pool = None

    async def start(self):
        import aiomysql
        u = urllib.parse.urlparse(self._dsn)
        self._pool = await aiomysql.create_pool(
            host=u.hostname or '127.0.0.1',
            port=u.port or 3306,
            user=urllib.parse.unquote(u.username or 'root'),
            password=urllib.parse.unquote(u.password or ''),
            db=u.path.lstrip('/') or 'pygame',
            minsize=1, maxsize=5, autocommit=True)
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute('SELECT 1')
        log.notice('mysql connected: %s', u.path.lstrip('/'))

    async def close(self):
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    # ---------------------------------------------------------------
    # 通用
    # ---------------------------------------------------------------
    async def execute(self, sql, *args):
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql, args)
            return cur.rowcount

    async def fetchrow(self, sql, *args):
        """查一行，返回 dict 或 None"""
        import aiomysql
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return await cur.fetchone()

    async def fetch(self, sql, *args):
        """查多行，返回 [dict, ...]"""
        import aiomysql
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return await cur.fetchall()

    # ---------------------------------------------------------------
    # 结构化写
    # ---------------------------------------------------------------
    async def insert(self, table, data):
        """整行插入, data: {列名: 值}"""
        cols = list(data)
        sql = (f"INSERT INTO {_ident(table)} "
               f"({', '.join(_ident(c) for c in cols)}) "
               f"VALUES ({', '.join('%s' for _ in cols)})")
        await self.execute(sql, *[data[c] for c in cols])

    async def update_columns(self, table, pk_col, pk_val, dirty):
        """增量更新: 只 UPDATE dirty 里变了的列

        @return 更新的列数
        """
        if not dirty:
            return 0
        cols = list(dirty)
        set_clause = ', '.join(f'{_ident(c)} = %s' for c in cols)
        sql = f'UPDATE {_ident(table)} SET {set_clause} WHERE {_ident(pk_col)} = %s'
        await self.execute(sql, *[dirty[c] for c in cols], pk_val)
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
        # MySQL 8.0.19+ 的别名语法（VALUES(col) 已于 8.0.20 废弃）
        set_clause = ', '.join(
            f'{_ident(c)} = _new.{_ident(c)}' for c in update_cols)
        sql = (f"INSERT INTO {_ident(table)} "
               f"({', '.join(_ident(c) for c in cols)}) "
               f"VALUES ({', '.join('%s' for _ in cols)}) AS _new "
               f"ON DUPLICATE KEY UPDATE {set_clause}")
        await self.execute(sql, *[row[c] for c in cols])
        return len(update_cols)
