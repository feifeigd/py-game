<!-- bmad:context -->
<!-- Verified 2026-11-23 (no VCS). Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## py-game

Python3 asyncio + actor 模型的分布式游戏服务器框架，配套 Unity 客户端。
纯标准库实现，零第三方依赖。架构细节见 `README.md`，Unity 搭建步骤见 `client/unity/README.md`。

## Where things are

- 引擎（与业务无关）: `engine/` —— actor、组内/组间 RPC、热更新、多进程启动
- 业务进程组: `login/` `game/` `center/` `recharge/`，各自 `config.json` + `start.py` + `app/`
- Unity 客户端: `client/unity/`，入口 `Assets/Scripts/Bootstrap.cs`（TCP）
- Cocos 客户端: `client/cocos/`，入口 `assets/scripts/Bootstrap.ts`（WebSocket，服务端 `wsServer` 见 `engine/net/websocket.py`）
- 联调与热更工具: `tools/`

## Running and verifying

- 依赖用 uv 维护（`pyproject.toml` + `uv.lock`）：先 `uv sync` 建 `.venv`；跑代码用 `uv run python ...`（子目录下会自动找到项目根）或先激活 `.venv`
- 全流程联调: `uv run python tools/test_all.py`（自动起 4 个进程组，验证登录/聊天/充值/热更，结束自动清理）
- 单起一个进程组必须在其目录下运行: `cd game && uv run python start.py`（cwd 决定 config.json 与日志路径）
- 热更新: `uv run python tools/hotfix.py <组名> [服务名|all]`

## Conventions that differ from defaults

- 业务消息用 `@handler('域.动作')` 注册；客户端 `player.*` 消息自动路由进该玩家的 actor 邮箱
- actor 消息方法 camelCase 自动转 snake_case: `player.getInfo` -> `do_get_info`
- 热更新安全: `app` 包模块级状态必须写 `if 'x' not in globals():` 守卫（reload 复用模块 `__dict__`，不守卫会丢状态），参照 `game/app/world.py`
- 只有 master 服务持有 cluster 连接；普通服务跨组调用用 `api.call_cluster`，自动经 master 转发
- 跨进程给 actor 发消息用 `api.actor_ref('player:role_id', service='game1').tell/ask`（actor 侧 `bind('player:'+role)` 注册寻址名，停止自动解绑；跨组加 `cluster=` 参数）；不要再手写 handler + 查表转发

## Known pitfalls

- 停服/清理测试进程必须 `taskkill /F /T /PID <主进程pid>` 杀整棵树——`proc.terminate()` 只杀主进程，multiprocessing 子进程会变孤儿占住端口（实测导致 bind WinError 10048）
- 各组 `start.py` 必须保留 `if __name__ == '__main__':` 守卫——Windows spawn 模式下子进程会重导入主模块

<!-- /bmad:context -->

## 编码约定（项目级，bmad 块外持久保存）

- 字符串格式化一律用 f-string，不用 `%` 或 `str.format`（包括 log 调用——`engine/util/log.py` 的封装本来就是立即格式化，用 `%s` 占不到惰性求值的便宜）。存量代码改动到某行时顺手迁移，不做全量重写

## 多角色模型（2026-11 起，bmad 块外持久保存）

- 一个账号在一个服可有多个角色（合服场景）：身份一律用 `role_id`（player 档主键、actor 寻址名 `player:<role_id>`、world 位置表/离线充值的键），`account` 只用于展示和按号查角色列表（`world.listRoles`）
- `role_id = {real_id}_{objectid}`（出生服前缀，命名空间隔离，合服不改写 ID）；角色现居服由 servers 表的 real_id 重定向解析（合服后老服行保留、real_id 指向迁入服，多次合并成链），recharge 凭 `login.resolveServer` 解析 role_id 前缀定位角色所在服，支付回调不用带 server
- 客户端链路：`login.auth` -> `login.enterServer`（统一返回 roles 角色列表，0/1/多个都进角色面板）-> `login.createRole {server, name, sex}` 创角 或 `login.enterRole {server, role}` 选角 -> `game.enter {account, role, token}`
- 创角在 game 组 `world.createRole` 建档：角色名本服唯一（player 集合 `name` 唯一索引兜底 + count 预检，重名报 `name taken`），性别枚举在 `game/app/const.py`（`SEX_MALE=1` / `SEX_FEMALE=2`），名字长度上限 `ROLE_NAME_MAX=12`；`game.enter` 只按 role_id 加载，不再建档
- 顶号策略按服配置：`game/config_<server>.json` 的 `singleRoleOnline`（默认 true = 单角色在线，进新角色踢同账号其它在线角色；false = 多角色可同时在线，只顶同 role 重登）
- 充值按角色发货：`/pay` 带 `role` 参数；多角色账号缺省 role 会被拒（`role required`）
- 服状态枚举在 `login/app/const.py` 的 `ServerStatus`（0=维护/1=良好/2=拥挤/3=爆满/4=备用），业务代码不要写 status 数字
