"""login 组业务常量: servers 表 status 枚举

选服展示与进入控制的唯一口径，业务代码不要直接写 status 数字。
"""
from enum import IntEnum


class ServerStatus(IntEnum):
    MAINTAIN = 0   # 维护：不展示，不可进
    GOOD = 1       # 良好：正常
    BUSY = 2       # 拥挤：正常
    FULL = 3       # 爆满：正常展示；新注册拒入（server busy），老玩家可回
    RESERVE = 4    # 备用：不展示，不可进


# 对玩家可见、可进入的状态（login.auth 选服列表过滤）
VISIBLE_STATUSES = [int(ServerStatus.GOOD), int(ServerStatus.BUSY),
                    int(ServerStatus.FULL)]

# 隐藏且拒绝进入的状态（enterServer 直接传 show_id 也拦住）
BLOCKED_STATUSES = (ServerStatus.MAINTAIN, ServerStatus.RESERVE)
