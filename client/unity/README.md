# Unity 客户端

与服务端（python3 asyncio）配套的 Unity 客户端，实现了完整登录链路：

```
连接 login 服 -> login.auth 验证账号 -> 返回大区列表，玩家选区
（只有一个大区时 auth 直接返回 token 和 gameAddr，免选区）
-> login.enterZone 拿 token 和 gameAddr
-> 连接 game 进程 -> game.enter 进入游戏 -> 聊天 / 收推送 / 充值到账
```

## 目录

```
Assets/Scripts/
  Net/Packet.cs         协议包与业务数据结构（JsonUtility 序列化）
  Net/NetworkClient.cs  TCP 客户端：4 字节大端长度前缀 + JSON，与服务端 protocol.py 一致
  GameManager.cs        主流程：登录 -> 进入游戏 -> 聊天 / 充值 / 推送处理
  Bootstrap.cs          纯代码搭建 UI（登录面板 + 游戏 HUD），无需编辑场景
```

## 如何运行

1. 先启动服务端（至少 login + game 两个进程组，充值测试还需 recharge 组）：

   ```
   cd py-game/login    && python start.py
   cd py-game/game     && python start.py
   cd py-game/recharge && python start.py
   ```

2. 用 Unity Hub「打开」本目录（client/unity/）作为项目（2021 LTS 及以上均可）。

3. 新建一个空场景，创建一个空 GameObject 命名为 `Bootstrap`，
   挂上 `Bootstrap` 脚本，保存场景为 `Assets/Scenes/Main.unity`。

4. 点 Play：输入账号密码（演示账号任意账号 + 密码 `123456`，或 `guest` 开头免密），
   点击「登录」。

5. 登录成功自动进入游戏 HUD：可以发聊天（全服跑马灯）、刷新信息、
   点「充100钻(测试)」触发 recharge 组的充值链路并收到 `recharge.push` 推送。

## 与服务端的协议约定

- 帧格式：4 字节大端长度 + UTF-8 JSON
- 外层包：`{"key": "...", "seq": n, "data": "<业务JSON字符串>", "error": "..."}`
- `seq > 0` 为请求/响应配对；服务器推送无 seq（C# 端反序列化为 0）
- `player.*` 开头的 key 在服务端进入该玩家的 actor 邮箱串行处理

## 注意

- 真机/局域网测试时把 `GameManager.loginHost` 改成服务器 IP，
  并确认服务端 config.json 里 tcpServer 的监听地址放行对应网卡。
- `NetworkClient` 收包在后台线程、分发在主线程 `Update`，UI 操作安全。
