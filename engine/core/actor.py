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

# 具名 actor 注册表：actor_id -> Actor（分布式寻址用，约定 '类型:ID' 如 'player:tester'）
_named_actors = weakref.WeakValueDictionary()


def bind_actor(actor_id, actor):
    """给 actor 绑定全局唯一名字，之后可跨进程经 sys.tellActor/sys.askActor 寻址"""
    _named_actors[actor_id] = actor
    actor._actor_id = actor_id


def unbind_actor(actor_id, actor):
    """解绑（仅当注册表里仍是该实例，防止顶号时新实例被旧实例误删）"""
    if _named_actors.get(actor_id) is actor:
        del _named_actors[actor_id]


def find_actor(actor_id):
    """在本进程查找具名 actor，不存在返回 None"""
    return _named_actors.get(actor_id)


class Actor:
    _STOP = object()

    def __init__(self):
        self._mailbox = asyncio.Queue()
        self._task = None
        self._stopped = False
        self._actor_id = None    # bind() 后的分布式寻址名

    def bind(self, actor_id):
        """绑定寻址名，返回 self 方便链式: Player(...).bind('player:x').start()"""
        bind_actor(actor_id, self)
        return self

    # ---------------------------------------------------------------
    def start(self):
        """启动 actor 消息循环"""
        _live_actors.add(self)

        # asyncio.ensure_future 的作用是：
        # 把一个协程（coroutine）包装成任务并立刻排入事件循环执行，
        # 返回一个 Future/Task 对象。
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
            raise RuntimeError(f'ask to stopped actor: {key}')
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
        if self._actor_id is not None:
            unbind_actor(self._actor_id, self)

    async def on_message(self, key, *args, **kwargs):
        """按消息名分发到 do_<key> 方法，camelCase 自动转 snake_case"""
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
        meth = getattr(self, 'do_' + snake.replace('.', '_'), None)
        if meth is None:
            raise KeyError(f'{self!r} has no handler for message {key}')
        result = meth(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def on_stop(self):
        """actor 停止时的钩子，子类可覆盖"""

    def on_hotfix(self):
        """热更新完成后的钩子，子类可覆盖

        引擎只重绑 actor 本人的 __class__；actor 持有的嵌套业务对象
        （如 self.abc = Abc()）的类不会自动更新，需在这里手动重绑:
            def on_hotfix(self):
                self.rebind_fields('abc')
        """

    def rebind_fields(self, *names):
        """把指定的嵌套字段重绑到热更后的新类（实现见模块级 rebind_object）"""
        for name in names:
            obj = getattr(self, name, None)
            if obj is not None:
                setattr(self, name, rebind_object(obj))


# ---------------------------------------------------------------------
def _find_reloaded_class(cls):
    """从定义模块（可能刚被 reload）里取同名新类；未变化或找不到返回 None"""
    if cls.__module__ == 'builtins':
        return None
    try:
        module = importlib.import_module(cls.__module__)
        new_cls = getattr(module, cls.__name__, None)
    except Exception:
        log.exception(f'hotfix: locate new class for {cls.__module__}.{cls.__name__} failed')
        return None
    if not isinstance(new_cls, type) or new_cls is cls:
        return None
    return new_cls


def _collect_slots(cls):
    """收集类沿 MRO 的全部 __slots__（含私有名改写），排除 __dict__/__weakref__"""
    result = set()
    for c in cls.__mro__:
        slots = c.__dict__.get('__slots__')
        if slots is None:
            continue
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in ('__dict__', '__weakref__'):
                continue
            # 私有名 __x 会被 Python 改写为 _ClassName__x（类名去掉前导下划线）
            if name.startswith('__') and not name.endswith('__'):
                name = f'_{c.__name__.lstrip("_")}{name}'
            result.add(name)
    return result


def rebind_object(obj):
    """把嵌套对象重绑到其定义模块 reload 后的同名新类，返回（可能被重建的）对象。

    - 布局兼容（普通 __dict__ 类，或 __slots__ 一致）: 直接换 __class__，实例身份不变
    - 布局不兼容（__slots__ 变了）: 降级为 __new__ 重建，迁移 __dict__ 和新旧共有的 slot
    - 类被删/改名，或定义在 engine 等未热更的模块: 原样返回
    """
    cls = type(obj)
    new_cls = _find_reloaded_class(cls)
    if new_cls is None:
        return obj
    try:
        obj.__class__ = new_cls
        return obj
    except TypeError:
        pass
    # 降级重建：迁移实例 __dict__ 与新旧共有的 slot 值
    new_obj = new_cls.__new__(new_cls)
    if hasattr(obj, '__dict__') and hasattr(new_obj, '__dict__'):
        new_obj.__dict__.update(obj.__dict__)
    for slot in _collect_slots(cls) & _collect_slots(new_cls):
        if hasattr(obj, slot):
            setattr(new_obj, slot, getattr(obj, slot))
    log.notice(f'hotfix: nested object {cls.__module__}.{cls.__name__} rebuilt '
               f'(slots layout changed)')
    return new_obj


def rebind_actor_classes():
    """热更新后，把存活 actor 的 __class__ 重绑定到新加载的同名类"""
    count = 0
    for actor in list(_live_actors):
        new_cls = _find_reloaded_class(actor.__class__)
        if new_cls is not None:
            try:
                actor.__class__ = new_cls
                count += 1
            except TypeError:
                # __slots__ 布局不兼容：actor 身份被注册表/会话引用，不可降级重建，只能跳过
                log.warning(f'hotfix: actor {actor!r} class rebind failed '
                            f'(slots layout changed), skipped')
        hook = getattr(actor, 'on_hotfix', None)
        if hook:
            try:
                hook()
            except Exception:
                log.exception('actor on_hotfix error')
    return count
