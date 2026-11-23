"""
center 进程组（中心服务）

跨服 / 全局协调逻辑放在这里，演示:
  - 收集各 game 组上报的在线人数（game world 每 30 秒跨组上报）
  - 提供全平台在线统计查询
"""
from engine.core.actor import Actor
from engine.core.handler import handler
from engine.util import log


class CenterActor(Actor):
    def __init__(self):
        super().__init__()
        self.online_reports = {}  # 组名 -> 在线人数

    async def do_report_online(self, group, count):
        self.online_reports[group] = count
        log.notice('online report: %s = %d', group, count)
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


def init():
    center_actor.start()
    log.notice('center inited')
