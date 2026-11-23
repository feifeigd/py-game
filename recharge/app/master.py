"""recharge 组 master 服务（组内协调 + 跨组转发）"""
from engine.util import log


def init():
    log.notice('recharge master inited')
