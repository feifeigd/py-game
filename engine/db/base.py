"""脏字段追踪容器：增量更新的基础

用法:
    data = DirtyRecord({'name': 'foo', 'level': 1})
    data.level += 1            # 顶层字段赋值即标脏
    data.items.append('sword') # 嵌套结构原地改不会被追踪!
    data.mark_dirty('items')   # 嵌套改完要手动标脏
    dirty = data.pop_dirty()   # {'level': 2, 'items': [...]}, 同时清空脏标记

粒度为顶层字段: Mongo 侧对应 $set 顶层路径, PG 侧对应 UPDATE 变更列。
"""


class DirtyRecord:
    """字典的脏追踪包装，用属性方式访问字段"""

    def __init__(self, data=None):
        object.__setattr__(self, '_data', dict(data or {}))
        object.__setattr__(self, '_dirty', set())

    # ---------------------------------------------------------------
    # 字段访问
    # ---------------------------------------------------------------
    def __getattr__(self, key):
        try:
            return object.__getattribute__(self, '_data')[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self._data[key] = value
        self._dirty.add(key)

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return dict(self._data)

    # ---------------------------------------------------------------
    # 脏标记
    # ---------------------------------------------------------------
    @property
    def is_dirty(self):
        return bool(self._dirty)

    @property
    def dirty_keys(self):
        return set(self._dirty)

    def mark_dirty(self, key):
        """嵌套结构（list/dict）原地修改后手动标脏"""
        if key not in self._data:
            raise KeyError(key)
        self._dirty.add(key)

    def pop_dirty(self):
        """取出脏字段 {key: value} 并清空脏标记"""
        dirty = {k: self._data[k] for k in self._dirty}
        self._dirty.clear()
        return dirty

    def clear_dirty(self):
        self._dirty.clear()

    def __repr__(self):
        return 'DirtyRecord(%r, dirty=%r)' % (self._data, sorted(self._dirty))
