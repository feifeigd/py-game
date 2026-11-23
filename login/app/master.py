"""login 组 master 服务（组内协调 + 跨组转发，无业务）"""
from engine.util import log


def init():
    log.notice('login master inited')
