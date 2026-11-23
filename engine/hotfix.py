"""
代码热更新

reload_package('app') 会:
  1. 按导入顺序的逆序 reload 指定包下的所有已加载模块
     （后导入的先 reload，大体保证依赖先更新）
  2. handler 注册表随模块重新执行自动覆盖旧函数
  3. 存活 actor 的 __class__ 重绑定到新类（旧玩家对象直接获得新逻辑）
  4. 调用每个模块的 on_hotfix() 钩子（若存在）

触发方式: python tools/hotfix.py <项目目录> [服务名|all]
"""
import importlib
import sys

from engine.core.actor import rebind_actor_classes
from engine.util import log


def get_package_modules(package: str):
    prefix = package + '.'
    return [(name, m) for name, m in sys.modules.items()
            if name == package or name.startswith(prefix)]


def reload_package(package='app'):
    mods = get_package_modules(package)
    if not mods:
        log.warning('hotfix: no modules under package %s', package)
        return []

    reloaded = []
    errors = []
    # sys.modules 保持插入序，逆序 reload 让"后导入的"(通常是上层模块)先刷新
    for name, module in reversed(mods):
        try:
            importlib.reload(module)
            reloaded.append(name)
        except Exception:
            log.exception('hotfix reload %s failed', name)
            errors.append(name)

    rebound = rebind_actor_classes()

    # on_hotfix 钩子（用 reload 后的新模块对象）
    for name, _ in mods:
        module = sys.modules.get(name)
        hook = getattr(module, 'on_hotfix', None) if module else None
        if hook:
            try:
                hook()
            except Exception:
                log.exception('hotfix hook %s.on_hotfix error', name)

    log.notice('hotfix done: reloaded=%s, actor rebound=%d, errors=%s',
               reloaded, rebound, errors)
    return {'reloaded': reloaded, 'actorRebound': rebound, 'errors': errors}
