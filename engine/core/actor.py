# coding:utf-8
"""
Actor 模型

每个 Actor 拥有一个 asyncio.Queue 邮箱和独立的消息循环协程:
  - tell():  投递消息，不等待结果（fire-and-forget）
  - ask():   投递消息并等待结果（返回 future）
  - 消息按到达顺序串行处理，actor 内部状态无需加锁

业务 Actor 只需继承本类，并定义 ``async def do_<消息名>(self, ...)`` 方法，
消息名中的 ``.`` 用 ``_`` 代替，例如消息 "chat" -> do_chat，"battle.start" -> do_battle_start。
"""
import asyncio
import importlib
import inspect
import re
import weakref

from engine.util import log

# 所有存活 actor，热更新时用于类重绑定
_live_actors = weakref.WeakSet()


class Actor(object):
    _STOP = object()

    def __init__(self):
        self._mailbox = asyncio.Queue()
        self._task = None
        self._stopped = False

    # ---------------------------------------------------------------
    def start(self):
        """启动 actor 消息循环"""
        _live_actors.add(self)
        self._task = asyncio.ensure_future(self._run())
        return self

    def stop(self):
        """停止 actor（排空前不再接受新消息语义从简：投递停止标记）"""
        if not self._stopped:
            self._stopped = True
            self._mailbox.put_nowait(self._STOP)

    @property
    def stopped(self):
        return self._stopped

    # ---------------------------------------------------------------
    def tell(self, key, *args, **kwargs):
        """异步投递，不等待结果"""
        if self._stopped:
            log.warning('tell to stopped actor: %s', key)
            return
        self._mailbox.put_nowait((key, args, kwargs, None))

    def ask(self, key, *args, **kwargs):
        """异步投递，返回可 await 的结果"""
        if self._stopped:
            raise RuntimeError('ask to stopped actor: %s' % key)
        fut = asyncio.get_event_loop().create_future()
        self._mailbox.put_nowait((key, args, kwargs, fut))
        return fut

    # ---------------------------------------------------------------
    async def _run(self):
        while True:
            item = await self._mailbox.get()
            if item is self._STOP:
                break
            key, args, kwargs, fut = item
            try:
                result = await self.on_message(key, *args, **kwargs)
            except Exception as exc:
                log.exception('actor %r handle %s error', self, key)
                if fut is not None and not fut.done():
                    fut.set_exception(exc)
            else:
                if fut is not None and not fut.done():
                    fut.set_result(result)
        await self.on_stop()

    async def on_message(self, key, *args, **kwargs):
        """按消息名分发到 do_<key> 方法，camelCase 自动转 snake_case"""
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
        meth = getattr(self, 'do_' + snake.replace('.', '_'), None)
        if meth is None:
            raise KeyError('%r has no handler for message %s' % (self, key))
        result = meth(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def on_stop(self):
        """actor 停止时的钩子，子类可覆盖"""

    def on_hotfix(self):
        """热更新完成后的钩子，子类可覆盖"""


# ---------------------------------------------------------------------
def rebind_actor_classes():
    """热更新后，把存活 actor 的 __class__ 重绑定到新加载的同名类"""
    count = 0
    for actor in list(_live_actors):
        cls = actor.__class__
        try:
            module = importlib.import_module(cls.__module__)
            new_cls = getattr(module, cls.__name__, None)
        except Exception:
            continue
        if new_cls is not None and new_cls is not cls and isinstance(new_cls, type):
            try:
                actor.__class__ = new_cls
                count += 1
            except TypeError:
                # __slots__ 布局不兼容等情况，跳过
                pass
        hook = getattr(actor, 'on_hotfix', None)
        if hook:
            try:
                hook()
            except Exception:
                log.exception('actor on_hotfix error')
    return count
