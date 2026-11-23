"""
进程组启动器

读取项目的 config.json，为 services 里的每个服务 fork 一个子进程，
master 服务运行在主进程中。用法（每个项目目录下的 start.py）:

    from engine.start.master import Master
    Master('config.json').start()
"""
import json
import multiprocessing
import os
import sys

from engine.start.service import run_service, MASTER_SERVICE


class Master(object):
    def __init__(self, config):
        if isinstance(config, str):
            with open(config, 'r', encoding='utf-8') as fp:
                config = json.load(fp)
        self._config = config
        self._children = []

    def start(self):
        services = self._config['services']
        ctx = multiprocessing.get_context('spawn')
        for service_name in services:
            if service_name == MASTER_SERVICE:
                continue
            proc = ctx.Process(
                target=run_service,
                args=(service_name, self._config),
                name='pygame-%s' % service_name)
            proc.start()
            self._children.append(proc)

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
                proc.join(timeout=3)
