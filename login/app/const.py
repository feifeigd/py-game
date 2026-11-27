"""login 组业务常量: servers 表 status 枚举

选服展示与进入控制的唯一口径，业务代码不要直接写 status 数字。
"""
from enum import IntEnum


class ServerStatus(IntEnum):
    MAINTAIN = 0   # 维护：不展示，不可进
    GOOD = 1       # 良好：正常
    BUSY = 2       # 拥挤：正常
    FULL = 3       # 爆满：正常展示；新注册拒入（server busy），老玩家可回
    RESERVE = 4    # 备用：不展示，不可进。开服流程的待验收服，
                   # 验收通过后才转正（GOOD）对外放数据
    MERGED = 5     # 已合并：展示（老玩家仍能看到老服入口），可进——
                   # 进入自动路由到迁入服；同时是 resolveServer 的重定向行


# 对玩家可见、可进入的状态（login.auth 选服列表过滤）
VISIBLE_STATUSES = [int(ServerStatus.GOOD), int(ServerStatus.BUSY),
                    int(ServerStatus.FULL), int(ServerStatus.MERGED)]

# 隐藏且拒绝进入的状态（enterServer 直接传 show_id 也拦住）
BLOCKED_STATUSES = (ServerStatus.MAINTAIN, ServerStatus.RESERVE)
