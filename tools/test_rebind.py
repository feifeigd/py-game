"""
嵌套对象热更重绑（engine.core.actor.rebind_object / Actor.rebind_fields）验证

在 run/fakepkg 下造临时包并模拟热更（改源码 + importlib.reload），验证:
  1. 普通类: 旧实例换 __class__，实例身份不变、状态保留、跑新逻辑、isinstance 通过
  2. __slots__ 布局变化: 降级重建，新旧共有 slot 的状态迁移到新实例
  3. 类被删除: 原样返回旧对象，不炸
  4. Actor.rebind_fields: 按字段名批量重绑

运行: uv run python tools/test_rebind.py
"""
import importlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'run'))

from engine.core.actor import Actor, rebind_object

PKG_DIR = os.path.join(ROOT, 'run', 'fakepkg')

V1 = '''
class Abc:
    def __init__(self, x):
        self.x = x
    def version(self):
        return 'v1'


class SlotA:
    __slots__ = ('a',)
    def __init__(self, a):
        self.a = a


class ToBeDeleted:
    pass
'''

# 模拟热更后的代码: Abc 改逻辑、SlotA 加 slot 改布局、ToBeDeleted 被删
V2 = '''
class Abc:
    def __init__(self, x):
        self.x = x
    def version(self):
        return 'v2'


class SlotA:
    __slots__ = ('a', 'b')
    def __init__(self, a, b=0):
        self.a = a
        self.b = b
'''


def main():
    os.makedirs(PKG_DIR, exist_ok=True)
    try:
        with open(os.path.join(PKG_DIR, '__init__.py'), 'w') as fp:
            fp.write('')
        with open(os.path.join(PKG_DIR, 'abc_mod.py'), 'w') as fp:
            fp.write(V1)

        import fakepkg.abc_mod as mod
        old_abc_cls = mod.Abc
        abc = mod.Abc(42)
        abc_for_actor = mod.Abc(9)
        slot_obj = mod.SlotA(7)
        gone = mod.ToBeDeleted()

        # 模拟热更: 改写源码 -> reload
        with open(os.path.join(PKG_DIR, 'abc_mod.py'), 'w') as fp:
            fp.write(V2)
        importlib.invalidate_caches()
        mod = importlib.reload(mod)
        assert type(abc) is old_abc_cls  # reload 后旧实例仍挂在旧类上

        # 1. 普通类: 直接换 __class__
        abc2 = rebind_object(abc)
        assert abc2 is abc, '布局兼容时不应重建实例'
        assert type(abc) is mod.Abc and isinstance(abc, mod.Abc)
        assert abc.x == 42 and abc.version() == 'v2', '状态保留且跑新逻辑'
        print('1. 普通类重绑: 实例身份不变，状态保留，新逻辑生效 —— 通过')

        # 2. __slots__ 布局变化: 降级重建
        slot2 = rebind_object(slot_obj)
        assert slot2 is not slot_obj, '布局不兼容应重建实例'
        assert type(slot2) is mod.SlotA and slot2.a == 7
        print('2. __slots__ 不兼容降级重建: 共有 slot 状态已迁移 —— 通过')

        # 3. 类被删除: 原样返回
        assert rebind_object(gone) is gone
        print('3. 类被删: 原样返回旧对象 —— 通过')

        # 4. Actor.rebind_fields 集成
        actor = Actor()
        actor.abc = abc_for_actor
        actor.rebind_fields('abc')
        assert isinstance(actor.abc, mod.Abc) and actor.abc.x == 9
        print('4. Actor.rebind_fields: 按字段名重绑 —— 通过')

        print()
        print('########## 全部通过 ##########')
    finally:
        shutil.rmtree(PKG_DIR, ignore_errors=True)
        sys.modules.pop('fakepkg.abc_mod', None)
        sys.modules.pop('fakepkg', None)


if __name__ == '__main__':
    main()
