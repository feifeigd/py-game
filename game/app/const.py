"""game 组业务常量"""

COLL_PLAYER = 'player'  # mongo 玩家角色档集合名

# 性别（创角时玩家自选，落 player 档 sex 字段）
SEX_MALE = 1
SEX_FEMALE = 2
SEXES = (SEX_MALE, SEX_FEMALE)

ROLE_NAME_MAX = 12  # 角色名长度上限（字符数，本服唯一）

MAX_ROLES_PER_ACCOUNT = 4  # 单账号单服角色数上限默认值（config maxRolesPerAccount 可覆盖）

# 新角色默认档（world.createRole 建档时以此为基础）
DEFAULT_DATA = {
    'name': '',
    'sex': SEX_MALE,
    'level': 1,
    'exp': 0,
    'diamond': 0,
    'login_time': 0,   # 最近登录时间（epoch 毫秒），角色面板按它排序/默认选中
}
