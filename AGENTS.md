<!-- bmad:context -->
<!-- Verified 2026-11-23 (no VCS). Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## py-game

Python3 asyncio + actor 模型的分布式游戏服务器框架，配套 Unity 客户端。
纯标准库实现，零第三方依赖。架构细节见 `README.md`，Unity 搭建步骤见 `client/unity/README.md`。

## Where things are

- 引擎（与业务无关）: `engine/` —— actor、组内/组间 RPC、热更新、多进程启动
- 业务进程组: `login/` `game/` `center/` `recharge/`，各自 `config.json` + `start.py` + `app/`
- Unity 客户端: `client/unity/`，入口 `Assets/Scripts/Bootstrap.cs`
- 联调与热更工具: `tools/`

## Running and verifying

- 全流程联调: `python tools/test_all.py`（自动起 4 个进程组，验证登录/聊天/充值/热更，结束自动清理）
- 单起一个进程组必须在其目录下运行: `cd game && python start.py`（cwd 决定 config.json 与日志路径）
- 热更新: `python tools/hotfix.py <组名> [服务名|all]`

## Conventions that differ from defaults

- 业务消息用 `@handler('域.动作')` 注册；客户端 `player.*` 消息自动路由进该玩家的 actor 邮箱
- actor 消息方法 camelCase 自动转 snake_case: `player.getInfo` -> `do_get_info`
- 热更新安全: `app` 包模块级状态必须写 `if 'x' not in globals():` 守卫（reload 复用模块 `__dict__`，不守卫会丢状态），参照 `game/app/world.py`
- 只有 master 服务持有 cluster 连接；普通服务跨组调用用 `api.call_cluster`，自动经 master 转发

## Known pitfalls

- 停服/清理测试进程必须 `taskkill /F /T /PID <主进程pid>` 杀整棵树——`proc.terminate()` 只杀主进程，multiprocessing 子进程会变孤儿占住端口（实测导致 bind WinError 10048）
- 各组 `start.py` 必须保留 `if __name__ == '__main__':` 守卫——Windows spawn 模式下子进程会重导入主模块

<!-- /bmad:context -->
