"""
py-game 服务端引擎
基于 python3 asyncio + actor 模型的分布式游戏服务器框架

模块说明:
    core.actor    actor 模型（邮箱 + 消息循环）
    core.handler  消息处理器注册表
    net.protocol  长度前缀 + JSON 的通信协议（客户端 / RPC 通用）
    net.server    面向客户端的 TCP 服务器（会话管理）
    net.web       轻量 HTTP 服务器（充值回调等）
    rpc.node      RPC 节点基类（监听 / 重连拨号 / call / send / broadcast）
    rpc.internal  进程组内部 RPC（组内各服务进程网状互联）
    rpc.cluster   进程组之间 RPC（各组 master 之间互联）
    api           对应用层暴露的统一调用接口
    hotfix        代码热更新
    start.master  进程组启动器（多进程）
    start.service 单个服务进程的启动流程
    context       当前进程上下文（单例）
    util.log      日志
"""
