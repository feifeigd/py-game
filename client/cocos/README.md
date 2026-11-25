# Cocos Creator 客户端

与服务端（python3 asyncio + actor）配套的 Cocos Creator 3.x 客户端，走 **WebSocket**
（服务端 `wsServer`，见 `engine/net/websocket.py`），实现了与 Unity 客户端相同的登录链路：

```
连接 login(ws) -> login.auth 验证账号 -> 返回大区列表，玩家选区
-> login.enterZone 拿 token 和 gameWsAddr
-> 连接 game 进程(ws) -> game.enter 进入游戏 -> 聊天 / 收推送 / 充值到账
```

## 目录

```
assets/scripts/
  Net/Packet.ts         协议包与业务数据结构（与服务端字段一一对应）
  Net/NetworkClient.ts  WebSocket 客户端：纯 TS 单例，请求-响应配对 + 推送分发
  GameManager.ts        主流程：登录 -> 进入游戏 -> 聊天 / 充值 / 推送处理
  Bootstrap.ts          纯代码搭建 UI（登录面板 + 游戏 HUD），无需编辑场景
```

## 如何运行

1. 先启动服务端（login + game 两个进程组即可，充值测试还需 recharge 组）。
   注意服务端的 login/game 配置里要有 `wsServer` 段（本仓库 config.json 已配好）。

2. 用 Cocos Dashboard「导入」本目录（client/cocos/）作为项目（3.8.x 均可）。

3. 新建场景（保留默认的 Canvas / Main Camera），在 Canvas 下建一个空节点，
   挂上 `Bootstrap` 组件（GameManager / NetworkClient 都是纯 TS 单例，不用挂）。

4. 点预览：输入账号密码（演示账号任意账号 + 密码 `123456`，或 `guest` 开头免密），
   点击「登录」。

5. 登录成功自动进入游戏 HUD：发聊天（全服跑马灯）、刷新信息、
   点「充100钻(测试)」触发 recharge 组的充值链路并收到 `recharge.push` 推送。

## 与服务端的协议约定

- 传输层：WebSocket 文本帧；帧内容与 TCP 端完全一致
- 外层包：`{"key": "...", "seq": n, "data": "<业务JSON字符串>", "error": "..."}`
- `seq > 0` 为请求/响应配对；服务器推送无 seq
- `player.*` 开头的 key 在服务端进入该玩家的 actor 邮箱串行处理

## 注意

- 真机/局域网测试时把 `GameManager.loginAddr` 改成服务器 IP，微信小游戏等
  生产环境要求 `wss://` 且域名要进平台白名单。
- 充值测试按钮走 HTTP（recharge 组的 web 接口），浏览器预览下依赖服务端
  返回 `Access-Control-Allow-Origin: *`（engine/net/web.py 已带）。
