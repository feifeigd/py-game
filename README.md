# py-game 游戏服务器框架

Python3 asyncio + actor 模型的分布式游戏服务器框架（配套 Unity 客户端）。
将原 Python2/gevent 方案重写为 **Python3 asyncio** 实现；引擎零第三方依赖，
仅数据层按组引入异步驱动（game 组 MongoDB / motor，login、recharge 组 PostgreSQL / asyncpg，
center 组 MySQL / aiomysql）。
依赖用 [uv](https://docs.astral.sh/uv/) 维护（`pyproject.toml` + `uv.lock`）。

## 总体架构

```
                        ┌─────────────┐
   Unity 客户端          │   center    │  中心组：跨服协调/全服统计
   (login/auth)         │   master    │  clusterPort 31000
        │               └──────▲──────┘
        ▼                      │ cluster RPC（每 30s 上报在线）
┌─────────────┐        ┌──────┴──────────────┐
│   login     │ cluster│       game          │
│   master    │◄──────►│  master(world 全局进程)│──┐
│   login     │        │  game1 (单人/玩家进程) │  │ 组内 RPC
└─────────────┘        │  game2 (单人/玩家进程) │◄─┘
        ▲              └──────▲──────────────┘
        │                     │ cluster RPC（充值发货）
┌──────┴──────┐               │
│  recharge   │───────────────┘
│  master     │
│  recharge(web /pay) │◄── 支付平台 HTTP 回调
└─────────────┘
```

- **进程组（项目）**：login / game / center / recharge，各自一个目录、一份 `config.json`、
  一个 `start.py`；`services` 里每个服务是一个**独立进程**（multiprocessing spawn）。
- **组内 RPC**：组内服务进程按服务名互联（`api.call_service('game1', ...)`），
  确定性单向拨号 + 断线重连。
- **组间 RPC**：各组 master 暴露 `clusterPort`，组间按组名互联
  （`api.call_cluster('game', ...)`）；非 master 进程的跨组调用自动经本组 master 转发；
  `api.call_cluster_service` 可直达对端组内指定服务进程。
- **actor 模型**：`engine.core.actor.Actor`，邮箱 + 串行消息循环。
  全局进程收敛一个 `WorldActor`；每个在线玩家在 gameN 进程是一个 `Player` actor，
  客户端 `player.*` 消息进入对应玩家邮箱串行处理。
- **热更新**：`python tools/hotfix.py game all` —— 进程内 `importlib.reload` app 包全部模块，
  handler 注册表自动换新，存活 actor 的 `__class__` 重绑定到新类，状态不丢。

## 目录结构

```
py-game/
  engine/                  引擎（与业务无关，可直接复用）
    core/actor.py          actor 模型（tell/ask/邮箱/类热重绑定）
    core/handler.py        @handler('key') 消息注册表
    net/protocol.py        4字节长度前缀 + JSON 协议
    net/server.py          客户端 TCP 服务器（ClientSession / player.* 路由）
    net/web.py             轻量 HTTP 服务器（充值回调）
    rpc/node.py            RPC 节点基类（监听/拨号/重连/call/send/broadcast）
    rpc/internal.py        组内 RPC
    rpc/cluster.py         组间 RPC
    api.py                 应用层统一调用接口（组内/跨组/跨组到指定服务）
    db/                    数据层: DirtyRecord 脏字段追踪 + mongo/postgres/mysql 三驱动
    hotfix.py              热更新
    sys_handlers.py        sys.* 系统消息（启动握手/转发/热更/停服）
    start/master.py        进程组启动器（多进程）
    start/service.py       单服务进程启动流程
  login/    config.json + start.py + app/   登录组（master + login）
  game/     config.json + start.py + app/   游戏组（master=world 全局进程，game1/game2 单人进程）
  center/   config.json + start.py + app/   中心组
  recharge/ config.json + start.py + app/   充值组（master + recharge web）
  tools/hotfix.py          热更新工具
  tools/test_client.py     模拟客户端（与 Unity 同协议）
  tools/test_all.py        一键联调：起四个组->登录->聊天->充值->热更->再登录
  client/unity/            Unity 客户端工程（见 client/unity/README.md）
  client/cocos/            Cocos Creator 客户端工程，走 WebSocket（见 client/cocos/README.md）
```

## 快速开始

```bash
# 0. 起数据库（MongoDB + PostgreSQL + MySQL）并安装依赖（自动建 .venv）
docker compose up -d
uv sync

# 1. 启动四个进程组（各开一个终端；uv run 会自动向上找到项目根）
cd game     && uv run python start.py
cd login    && uv run python start.py
cd center   && uv run python start.py
cd recharge && uv run python start.py
# 也可以先激活虚拟环境再用裸 python: .venv\Scripts\activate

# 2. 一键联调测试（自动起全部组 + 全流程验证 + 热更新演示）
uv run python tools/test_all.py

# 3. 热更新（改了 game/app/ 下的代码后，不重启进程生效）
python tools/hotfix.py game          # game 组所有服务
python tools/hotfix.py game game1    # 只热更 game1

# 4. 模拟充值
curl "http://127.0.0.1:41100/pay?account=tester&item=diamond&count=100"
```

## 数据层（增量更新）

- **game 组 → MongoDB**：玩家角色档（`player` 集合）。进入游戏时加载、新号建档；
  字段经 `DirtyRecord` 追踪，每 `dataSaveInterval` 秒只把**脏字段 $set** 回库，
  充值到账 / 下线 / 被踢时立即落库。
- **login 组 → PostgreSQL**：`accounts` 表。老号登录只 UPDATE `login_count` /
  `last_login` 两列。
- **recharge 组 → PostgreSQL**：`orders` 表。回调先落单（pending），
  发货成功后只 UPDATE `status` / `delivered_at` 两列。
- **center 组 → MySQL**：`online_stats` 表。在线人数变化才 upsert，
  只更新 `online` / `updated_at` 两列。
- 三种关系/文档库的驱动接口一致（`execute/fetchrow/fetch/insert/update_columns/upsert`），
  业务组切换库只需改 config.json 的 `db` 段；注意 DDL 用可移植写法
  （`VARCHAR(n) PRIMARY KEY`、`TIMESTAMP`），aiomysql 是 %s 占位、反引号标识符。
- 嵌套结构（list/dict 字段）原地修改后需 `data.mark_dirty('字段')` 手动标脏。

## 端口分配

| 组 | 服务 | 组内端口 | 对外 |
|---|---|---|---|
| login | master | 11001 | clusterPort 11000 |
| login | login | 11002 | 客户端 TCP 11100 |
| game | master(world) | 21001 | clusterPort 21000 |
| game | game1 | 21002 | 客户端 TCP 21101 |
| game | game2 | 21003 | 客户端 TCP 21102 |
| center | master | 31001 | clusterPort 31000 |
| recharge | master | 41001 | clusterPort 41000 |
| recharge | recharge | 41002 | HTTP 41100 |

## 登录链路（端到端）

1. 客户端 TCP 连 login 服务（11100），发 `login.auth {account, password}`
2. login 生成 token，**跨组调用** game 组全局进程 `world.prepareLogin` 登记令牌，
   按账号哈希分配 gameN 进程，返回 `{token, gameAddr}`
3. 客户端连 gameAddr，发 `game.enter {account, token}`
4. gameN **组内调用** master 的 `world.verifyToken` 验证（支持顶号踢人），
   验证通过创建 Player actor 绑定会话
5. 之后客户端的 `player.*` 消息全部进入该玩家 actor 串行处理

## 热更新说明

完整指南见 [docs/hotfix.md](docs/hotfix.md)（原理、包级/模块级/函数级三种粒度、状态保护写法、常见坑）。

- 触发的 reload 只针对 `app` 包（业务代码），engine 不参与热更。
- 模块级状态用 `if 'x' not in globals():` 守卫保住（`importlib.reload` 复用模块 `__dict__`），
  参考 `game/app/world.py`、`game/app/game.py` 的写法。
- 新增 handler：reload 后装饰器重新执行即注册；改逻辑：注册表按 key 覆盖。
- actor 实例（在线玩家）通过 `__class__` 重绑定获得新逻辑，并回调 `on_hotfix()`。
- `sys.execCode` 保留了参考项目的在线执行任意代码能力（GM 兜底手段）。

## 已知简化（生产化 TODO）

- ~~账号/角色数据在内存中~~ 已接 MongoDB/PostgreSQL/MySQL（增量落库）；玩家离线期间的
  补发状态仍在内存（订单以 recharge 组 PG 表为准，可再加离线补拉逻辑）
- 客户端与 login/game 之间是明文 JSON，未做加密与签名校验
- 没有网关层（参考项目的 gate），客户端直连 gameN
- HTTP 服务不支持 HTTPS / 长连接
