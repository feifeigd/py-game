"""
消息处理器注册表

应用代码用 @handler('key') 注册处理函数，组内 RPC、组间 RPC 按 key 字符串分发。
客户端（公网）连接只能调用 @handler('key', public=True) 显式放行的 key，
其余（world.* / sys.* / center.* 等内部消息）在接入层直接拒绝，实现内外网隔离。

热更新重新加载模块时，装饰器会再次执行并覆盖旧函数，实现逻辑热更。
"""
import inspect

from engine.util import log

_HANDLERS = {}
_PUBLIC = set()  # 允许公网客户端调用的 key


def handler(key, public=False):
    """注册消息处理函数: @handler('world.verifyToken')
    public=True 才允许客户端直连调用，默认仅集群/组内可达。"""
    def deco(func):
        _HANDLERS[key] = func
        if public:
            _PUBLIC.add(key)
        else:
            # 热更场景：同名 key 重新注册时必须清掉旧的 public 标记
            _PUBLIC.discard(key)
        return func
    return deco


def get_handler(key):
    return _HANDLERS.get(key)


def has_handler(key):
    return key in _HANDLERS


def is_public(key):
    return key in _PUBLIC


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
