"""
center 进程组（中心服务）

跨服 / 全局协调逻辑放在这里，演示:
  - 收集各 game 组上报的在线人数（game world 每 30 秒跨组上报）
  - 在线统计增量落 PG online_stats 表（只更新 online/updated_at 两列）
  - 提供全平台在线统计查询
"""
from datetime import datetime, timezone

from engine import db
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.util import log


class CenterActor(Actor):
    def __init__(self):
        super().__init__()
        self.online_reports = {}  # 组名 -> 在线人数

    async def do_report_online(self, group, count):
        old = self.online_reports.get(group)
        self.online_reports[group] = count
        log.notice('online report: %s = %d', group, count)
        # 数值没变就不写库，变了只增量更新两列
        if old != count:
            await db.get().upsert('online_stats', 'group_name', group, {
                'online': count,
                'updated_at': datetime.now(timezone.utc),
            })
        return True

    async def do_get_online_stats(self):
        return dict(self.online_reports)


if 'center_actor' not in globals():
    center_actor = CenterActor()
else:
    center_actor.__class__ = CenterActor # type: ignore


@handler('center.reportOnline')
def report_online(group, count):
    return center_actor.ask('report_online', group, count)


@handler('center.getOnlineStats')
def get_online_stats():
    return center_actor.ask('get_online_stats')


async def init():
    # DDL 用可移植写法（PG / MySQL 通用），切换 db.type 不用改业务代码
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS online_stats (
            group_name VARCHAR(64) PRIMARY KEY,
            online     INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL
        )''')
    center_actor.start()
    log.notice('center inited')
