"""
进程组启动器

读取项目的 config.json，为 services 里的每个服务 fork 一个子进程，
master 服务运行在主进程中。用法（每个项目目录下的 start.py）:

    from engine.start.master import Master
    Master('config.json').start()

运行期动态扩容: master 服务里的 sys.startService 经 spawn_service()
拉起新服务进程（如 game3），新进程同样挂进 _children，停服时一起清理。
"""
import json
import multiprocessing

from engine.start.service import MASTER_SERVICE, run_service

_current_master = None


def spawn_service(name, config):
    """运行期动态拉起一个服务进程（供 sys.startService 调用，仅 master 进程有效）"""
    master = _current_master
    if master is None:
        return None
    return master.spawn(name, config)


class Master:
    def __init__(self, config):
        if isinstance(config, str):
            with open(config, 'r', encoding='utf-8') as fp:
                config = json.load(fp)
        self._config = config # dict
        self._children = []

    def spawn(self, name, config):
        """spawn 一个服务子进程并纳入管理"""
        ctx = multiprocessing.get_context('spawn')
        proc = ctx.Process(
            target=run_service,
            args=(name, config),
            name=f'pygame-{name}')
        proc.start()
        self._children.append(proc)
        return proc

    def start(self):
        global _current_master
        _current_master = self
        services = self._config['services']
        for service_name in services:
            if service_name == MASTER_SERVICE:
                continue
            self.spawn(service_name, self._config)

        try:
            # master 服务运行在主进程
            run_service(MASTER_SERVICE, self._config)
        except KeyboardInterrupt:
            pass
        finally:
            for proc in self._children:
                if proc.is_alive():
                    proc.terminate()
            for proc in self._children:
                proc.join(timeout=300)
