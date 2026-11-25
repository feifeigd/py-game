# 热更新（Hotfix）指南

py-game 服务端支持**不停机热更新**：在不重启进程、不踢在线玩家的前提下替换业务代码。
本文档说明实现原理、三种更新粒度、状态保护写法与常见坑。

> 适用边界：热更新只针对各进程组 `app/` 包下的**业务代码**。
> `engine/` 引擎代码不参与热更（改动引擎请重启进程组）。

---

## 1. 原理

```
tools/hotfix.py ──TCP──► 目标组 master (clusterPort)
                            │ sys.hotfix
                            ▼ 组内 RPC 广播 sys.doHotfix
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
           master        game1          game2   ... 各服务进程
              └─────────────┴─────────────┘
              每个进程内执行 engine/hotfix.py: reload_package()
```

`reload_package()` 做四件事（`engine/hotfix.py`）：

1. **`importlib.reload` 目标模块**——模块代码重新执行，`@handler` 装饰器与
   `@CTX.web_server.route(...)` 路由装饰器随之重新注册，handler 表按 key、
   路由表按 path 覆盖，新逻辑立即生效（覆盖已有注册时输出 WARNING，见常见坑 7）
2. **actor 类重绑定**——遍历所有存活 actor（在线玩家、WorldActor 等），
   把 `__class__` 替换为 reload 后的同名新类，**实例状态原样保留**，逻辑焕然一新
3. 调用每个模块的 `on_hotfix()` 钩子（若定义了）
4. 返回重载明细（reloaded / actorRebound / errors）

由于客户端消息（`player.*`）、组内 RPC、组间 RPC 全部经 handler 注册表和
actor 方法查找**动态分发**，所以 reload 之后所有入口自然走到新代码，
不需要任何额外的转发刷新。

---

## 2. 三种粒度

### 2.1 包级（默认，最稳）

```bash
python tools/hotfix.py game          # game 组所有服务进程，reload 整个 app 包
python tools/hotfix.py game game1    # 只热更 game1 进程
```

改动横跨多个文件时用包级，避免漏更。

### 2.2 模块级（只更新某个文件）

第三个参数直接传模块名：

```bash
python tools/hotfix.py game game1 app.game    # 只 reload app/game.py
```

`reload_package` 的匹配规则是 `name == package or name.startswith(package + '.')`，
所以传 `app.game` 时只命中这一个模块。

实测验证（`run/demo_hotfix.py`）：在线玩家不断线，actor 类重绑定
（`actorRebound: 1`），`player.getInfo` 热更后立即返回新增字段。

### 2.3 函数级（只更新某个函数，不 reload 任何文件）

利用内置的 `sys.execCode` 在线执行代码，直接替换运行中类的方法：

```python
# 通过管理连接把下面代码发给 game1 的 sys.execCode:
import app.game

async def patched_get_info(self, data):
    return {'patchedBy': 'execCode', 'account': self.account}

app.game.Player.do_get_info = patched_get_info
```

Python 方法在调用时按 `实例 -> 类` 查找，替换类属性后**所有存活实例立即生效**，
连 actor 类重绑定都不需要。参考 `run/demo_hotfix.py` 中经
`sys.forwardService` 转发的完整调用方式。

> 这是参考 titan 的 `execCode` 机制保留的应急通道，等同于线上任意代码执行，
> 生产环境应在 master 的 clusterPort 前加网络隔离 / 鉴权。

### 粒度选择

| 粒度 | 适合场景 | 持久性 | 风险 |
|---|---|---|---|
| 函数级 | 应急热修、线上调参、临时加日志 | **仅内存**，reload / 重启后回退 | 最低，但容易和源文件脱节 |
| 模块级 | 日常开发改单个文件逻辑 | 与源文件一致 | 低 |
| 包级 | 多文件联动改动 | 与源文件一致 | 最低，开销略大 |

**铁律：函数级补丁是应急手段，事后必须把改动补进源文件，
再用模块级/包级热更同步一次，否则下次热更或重启即回退。**

---

## 3. 状态保护（必须遵守的写法）

`importlib.reload` **复用模块的 `__dict__` 重新执行代码**——
模块级赋值语句会再次执行。不加保护的话，全局玩家表、actor 单例会被重置成空。

### 3.1 模块级容器：globals() 守卫

```python
# game/app/game.py
if '_players' not in globals():
    _players = {}   # reload 时 globals() 里已有，跳过重新赋值，在线玩家表保住
```

### 3.2 全局 actor 单例：守卫 + 类重绑定

```python
# game/app/world.py
if 'world_actor' not in globals():
    world_actor = WorldActor()      # 首次加载才创建
else:
    world_actor.__class__ = WorldActor  # reload 时保住实例，只换新类
```

### 3.3 哪些状态不用担心

- **actor 实例属性**（`self.diamond`、`self.level` 等）：存在实例上，
  reload 只换类，实例数据不动
- **handler 注册表**：reload 自动按 key 覆盖

### 3.4 可选的 on_hotfix 钩子

```python
def on_hotfix():
    """模块 reload 完成后调用，可做数据迁移、缓存重建"""
    log.notice('world hotfixed, online=%d', len(world_actor.player_locations))
```

actor 实例也有 `on_hotfix()` 钩子（`engine/core/actor.py`），
类重绑定后逐个回调，适合在热更后修复实例字段（比如给老玩家补新字段默认值）。

### 3.5 actor 持有的嵌套对象：rebind_fields

引擎只重绑 actor 本人的 `__class__`，actor 持有的**嵌套业务对象**
（如 `self.abc = Abc()`）的类不会自动更新——不处理的话，老实例继续跑旧逻辑，
且 `isinstance(self.abc, Abc)` 在热更后变成 False（旧实例 vs 新类）。

在 actor 的 `on_hotfix()` 里用 `rebind_fields` 手动重绑：

```python
class Player(Actor):
    def on_hotfix(self):
        self.rebind_fields('abc')   # 把 self.abc 重绑到 reload 后的新 Abc
```

`rebind_object`（`engine/core/actor.py`）的行为：

- **布局兼容**（普通 `__dict__` 类，或 `__slots__` 一致）：直接换 `__class__`，实例身份不变
- **布局不兼容**（`__slots__` 变了）：降级为 `__new__` 重建实例，迁移 `__dict__`
  和新旧共有的 slot 值，并输出 NOTICE 日志；新类新增字段的默认值需在新类里给好
- **类被删/改名，或定义在 engine 等未热更模块**：原样返回，不动

注意 `rebind_fields` 按字段名显式指定，不会自动遍历 actor 的所有属性
（session、DirtyRecord 等引擎对象本就不需要重绑）。

---

## 4. 常见坑

1. **跨模块直接引用不会刷新**
   `from app.foo import bar` 之后 `bar` 是本模块里的独立名字，
   reload `app.foo` 不会更新它。框架内消息流（handler / actor 方法查找）
   天然免疫；自己写业务代码时，跨模块调用尽量走 handler 或
   `import app.foo; app.foo.bar()` 的形式。

2. **删除 handler 不会自动注销**
   reload 是"覆盖式"注册，新文件里删掉的 handler，其旧函数仍留在注册表里。
   真要下线某个消息，在函数里返回错误提示，而不是删除函数。

3. **语法错误的文件 reload 会失败，但旧逻辑还在跑**
   reload 失败时该模块保持旧代码（error 会记录在返回结果的 `errors` 字段），
   进程不崩。热更后**务必检查返回结果**，`errors` 非空说明没生效：
   ```json
   {"game1": {"reloaded": ["app.game"], "actorRebound": 1, "errors": []}}
   ```

4. **reload 顺序**
   包级热更按导入顺序的**逆序** reload（后导入的先刷新）。
   如果有模块 A 在 import 时就调用模块 B 的函数并缓存结果，
   A 需要排在 B 之后 reload 才能拿到新值——出现这类诡异问题时，
   直接再执行一次包级热更即可收敛。

5. **sys.execCode 的参数转发**
   函数级补丁经 `sys.forwardService` 转发时，补丁代码放在 `code` 参数里。
   不要给转发参数起名 `key` 以外的保留字冲突——框架 `dispatch` 首参已改名
   `msg_key` 规避此问题（见 commit `db6d4e9`）。

6. **HTTP 路由不要把注册写进 init()**
   `WebServer._routes` 持有的是函数对象引用。在 `init()` 里手动
   `CTX.web_server.route('/pay')(pay)`，热更后路由表仍指向**旧函数**，
   改 `pay` 的逻辑不生效且不报错（改模块级常量却生效——reload 复用
   `__dict__`，旧函数的全局查找能看到新值，问题极具隐蔽性）。
   正确写法是把装饰器写在函数定义处（参考 `recharge/app/recharge.py` 的 `pay`），
   reload 重跑模块顶层代码时自动覆盖路由表。

7. **handler / 路由覆盖注册会输出 WARNING**
   热更 reload 时的覆盖是预期行为；**启动期**出现 `handler ... overwritten` /
   `web route ... overwritten` 警告说明有重复注册（两个模块注册了同名 key/path），
   属于 bug，应立即排查。

8. **init() 里启动的长驻协程不热更**
   `_save_loop`、`_report_loop` 这类在 `init()` 里 `ensure_future` 的循环，
   热更后跑的还是旧代码体（它们引用的模块全局能看到新值，但循环逻辑本身不变）。
   改循环逻辑需重启进程；只调参数的话把参数放模块级常量/配置里、
   循环每次迭代时读取，即可热更。

---

## 5. 验证工具

```bash
# 全流程联调（含热更后再登录验证）
python tools/test_all.py

# 热更粒度专项演示（模块级 + 函数级，自动起停 game 组）
python run/demo_hotfix.py

# 嵌套对象热更重绑（rebind_fields）验证
python tools/test_rebind.py
```

---

## 6. 快速命令表

```bash
python tools/hotfix.py game                 # game 组全部进程，包级
python tools/hotfix.py game game1           # 只 game1 进程，包级
python tools/hotfix.py game game1 app.game  # 只 game1 进程，模块级
python tools/hotfix.py login all app.login  # login 组，模块级
# 函数级：通过管理连接调用 sys.execCode（见 2.3 与 run/demo_hotfix.py）
```
