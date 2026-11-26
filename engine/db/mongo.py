"""MongoDB 驱动封装（motor），game 进程组使用

增量更新语义: save() 只 $set 脏字段；is_new=True 时整文档 upsert。
"""
from engine.util import log


class MongoDB:
    def __init__(self, url, name):
        from motor.motor_asyncio import AsyncIOMotorClient
        self._client = AsyncIOMotorClient(url)
        self._db = self._client[name]

    async def start(self):
        # motor 懒连接，ping 一下尽早暴露配置/网络错误
        await self._client.admin.command('ping')
        log.notice('mongodb connected: %s', self._db.name)

    async def close(self):
        self._client.close()

    # ---------------------------------------------------------------
    async def load(self, coll, key_field, key):
        """按主键读文档，不存在返回 None"""
        return await self._db[coll].find_one({key_field: key}, {'_id': 0})

    async def find(self, coll, query, fields=None):
        """多行查询（不含 _id）; fields 给定时只取这些列"""
        projection = {'_id': 0}
        if fields:
            projection.update((f, 1) for f in fields)
        return await self._db[coll].find(query, projection).to_list(None)

    async def find_with_id(self, coll, query):
        """含 _id 的多行查询（数据迁移等需要定位原文档的场景）"""
        return await self._db[coll].find(query).to_list(None)

    async def update_by_id(self, coll, doc_id, data):
        """按 _id $set 更新（数据迁移用）"""
        await self._db[coll].update_one({'_id': doc_id}, {'$set': data})

    async def count(self, coll, query):
        return await self._db[coll].count_documents(query)

    async def create_index(self, coll, *keys):
        await self._db[coll].create_index(list(keys))

    async def save(self, coll, key_field, key, record, is_new=False):
        """增量落库: 只写脏字段; is_new 或脏为空时的整档兜底写全量

        @return 写入的字段数
        """
        if is_new:
            doc = record.to_dict()
            doc[key_field] = key
            await self._db[coll].replace_one({key_field: key}, doc, upsert=True)
            record.clear_dirty()
            return len(doc)
        dirty = record.pop_dirty()
        if not dirty:
            return 0
        await self._db[coll].update_one(
            {key_field: key}, {'$set': dirty}, upsert=True)
        return len(dirty)
