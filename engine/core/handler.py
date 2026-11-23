"""
消息处理器注册表

应用代码用 @handler('key') 注册处理函数，客户端消息、组内 RPC、
组间 RPC 共用同一张注册表，按 key 字符串分发。

热更新重新加载模块时，装饰器会再次执行并覆盖旧函数，实现逻辑热更。
"""
import inspect

from engine.util import log

_HANDLERS = {}


def handler(key):
    """注册消息处理函数: @handler('world.verifyToken')"""
    def deco(func):
        _HANDLERS[key] = func
        return func
    return deco


def get_handler(key):
    return _HANDLERS.get(key)


def has_handler(key):
    return key in _HANDLERS


def all_keys():
    return sorted(_HANDLERS.keys())


async def dispatch(msg_key, *args, **kwargs):
    """分发一条消息到注册的处理函数，返回其结果。
    首参避开 'key' 命名，防止转发类消息里名为 key 的参数与之冲突。"""
    func = _HANDLERS.get(msg_key)
    if func is None:
        raise KeyError(f'no handler for key: {msg_key}')
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result
