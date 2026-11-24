# -*- coding: utf-8 -*-
"""数据层单元测试: 脏字段追踪 + 增量 SQL / $set 生成（不需要真实数据库）"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.db.base import DirtyRecord
from engine.db.mongo import MongoDB
from engine.db.postgres import PostgresDB, _ident


# ---------------------------------------------------------------------
def test_dirty_record():
    r = DirtyRecord({'name': 'foo', 'level': 1, 'items': []})
    assert not r.is_dirty

    r.level = 2
    assert r.is_dirty and r.dirty_keys == {'level'}

    # 嵌套结构原地改不会自动标脏
    r.items.append('sword')
    assert r.dirty_keys == {'level'}
    r.mark_dirty('items')
    assert r.dirty_keys == {'level', 'items'}

    dirty = r.pop_dirty()
    assert dirty == {'level': 2, 'items': ['sword']}
    assert not r.is_dirty

    # 读不标脏
    _ = r.name
    assert not r.is_dirty
    print('DirtyRecord ok')


# ---------------------------------------------------------------------
class FakeColl:
    def __init__(self):
        self.calls = []

    async def find_one(self, query, projection=None):
        self.calls.append(('find_one', query))
        return None

    async def update_one(self, query, update, upsert=False):
        self.calls.append(('update_one', query, update, upsert))

    async def replace_one(self, query, doc, upsert=False):
        self.calls.append(('replace_one', query, doc, upsert))


class FakeMongoSchema:
    def __init__(self, coll):
        self._coll = coll
        self.name = 'test'

    def __getitem__(self, name):
        return self._coll


async def test_mongo_incremental():
    coll = FakeColl()
    mdb = MongoDB.__new__(MongoDB)  # 绕过 motor client 构造
    mdb._db = FakeMongoSchema(coll)

    # 1) 老档只写脏字段
    rec = DirtyRecord({'account': 'a1', 'level': 5, 'exp': 30, 'diamond': 0})
    rec.level = 6  # 只改了 level
    n = await mdb.save('player', 'account', 'a1', rec)
    assert n == 1
    kind, query, update, upsert = coll.calls[-1]
    assert kind == 'update_one'
    assert update == {'$set': {'level': 6}}, update  # 只 $set 脏字段
    assert query == {'account': 'a1'} and upsert
    assert not rec.is_dirty  # 落库后脏标记清空

    # 2) 无脏不写库
    n = await mdb.save('player', 'account', 'a1', rec)
    assert n == 0
    assert len(coll.calls) == 1

    # 3) 新档整文档 upsert
    rec2 = DirtyRecord({'level': 1, 'exp': 0})
    n = await mdb.save('player', 'account', 'a2', rec2, is_new=True)
    kind, query, doc, upsert = coll.calls[-1]
    assert kind == 'replace_one'
    assert doc == {'level': 1, 'exp': 0, 'account': 'a2'}, doc
    assert upsert
    print('MongoDB 增量更新 ok')


# ---------------------------------------------------------------------
async def test_postgres_incremental():
    pg = PostgresDB.__new__(PostgresDB)
    captured = []

    async def fake_execute(sql, *args):
        captured.append((sql, args))
        return 'OK'

    pg.execute = fake_execute

    # 1) 增量 UPDATE 只带脏列
    n = await pg.update_columns('accounts', 'account', 'a1',
                                {'login_count': 3, 'last_login': 'NOW'})
    assert n == 2
    sql, args = captured[-1]
    assert sql == ('UPDATE "accounts" SET "login_count" = $2, "last_login" = $3'
                   ' WHERE "account" = $1'), sql
    assert args == ('a1', 3, 'NOW')

    # 2) 空脏不执行
    n = await pg.update_columns('accounts', 'account', 'a1', {})
    assert n == 0 and len(captured) == 1

    # 3) insert 参数化
    await pg.insert('orders', {'order_id': 'o1', 'count': 100})
    sql, args = captured[-1]
    assert sql == 'INSERT INTO "orders" ("order_id", "count") VALUES ($1, $2)'
    assert args == ('o1', 100)

    # 4) upsert 冲突时只更新脏列
    await pg.upsert('online_stats', 'group_name', 'game',
                    {'online': 7, 'updated_at': 'T'}, extra=None)
    sql, args = captured[-1]
    assert 'ON CONFLICT ("group_name") DO UPDATE SET' in sql, sql
    assert '"online" = EXCLUDED."online"' in sql
    assert '"updated_at" = EXCLUDED."updated_at"' in sql
    assert 'group_name = EXCLUDED' not in sql  # 主键不参与更新

    # 5) 标识符注入防护
    for bad in ('a; DROP TABLE t', 'a"b', '1col', ''):
        try:
            _ident(bad)
            raise AssertionError('should reject %r' % bad)
        except ValueError:
            pass
    print('PostgreSQL 增量更新 ok')


# ---------------------------------------------------------------------
async def test_mysql_incremental():
    from engine.db.mysql import MySQLDB
    from engine.db.mysql import _ident as mysql_ident
    my = MySQLDB.__new__(MySQLDB)
    captured = []

    async def fake_execute(sql, *args):
        captured.append((sql, args))
        return 1

    my.execute = fake_execute

    # 1) 增量 UPDATE 只带脏列（反引号 + %s 占位）
    n = await my.update_columns('accounts', 'account', 'a1',
                                {'login_count': 3, 'last_login': 'NOW'})
    assert n == 2
    sql, args = captured[-1]
    assert sql == ('UPDATE `accounts` SET `login_count` = %s, `last_login` = %s'
                   ' WHERE `account` = %s'), sql
    assert args == (3, 'NOW', 'a1')

    # 2) 空脏不执行
    n = await my.update_columns('accounts', 'account', 'a1', {})
    assert n == 0 and len(captured) == 1

    # 3) insert 参数化
    await my.insert('orders', {'order_id': 'o1', 'count': 100})
    sql, args = captured[-1]
    assert sql == 'INSERT INTO `orders` (`order_id`, `count`) VALUES (%s, %s)'
    assert args == ('o1', 100)

    # 4) upsert 冲突时只更新脏列
    await my.upsert('online_stats', 'group_name', 'game',
                    {'online': 7, 'updated_at': 'T'}, extra=None)
    sql, args = captured[-1]
    assert 'ON DUPLICATE KEY UPDATE' in sql, sql
    assert '`online` = _new.`online`' in sql
    assert '`updated_at` = _new.`updated_at`' in sql
    assert 'group_name` = _new' not in sql  # 主键不参与更新

    # 5) 标识符注入防护
    for bad in ('a; DROP TABLE t', 'a`b', '1col', ''):
        try:
            mysql_ident(bad)
            raise AssertionError(f'should reject {bad!r}')
        except ValueError:
            pass
    print('MySQL 增量更新 ok')


def main():
    test_dirty_record()
    asyncio.run(test_mongo_incremental())
    asyncio.run(test_postgres_incremental())
    asyncio.run(test_mysql_incremental())
    print('ALL OK')


if __name__ == '__main__':
    main()
