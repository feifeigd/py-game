# coding:utf-8
"""
一键联调测试: 启动全部四个进程组 -> 跑完整登录/聊天/充值流程 -> 演示热更新 -> 关闭

用法（工作区根目录）: python tools/test_all.py
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, ROOT)
sys.path.insert(1, os.path.join(ROOT, 'tools'))

from test_client import full_flow  # noqa: E402

GROUPS = ['center', 'recharge', 'game', 'login']
# 等待这些端口就绪再开始测试
WAIT_PORTS = [11100, 21101, 21102, 41100]


def wait_port(port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.create_connection(('127.0.0.1', port), timeout=1)
            sock.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    procs = []
    log_files = []
    try:
        os.makedirs(os.path.join(ROOT, 'run'), exist_ok=True)
        for group in GROUPS:
            log_fp = open(os.path.join(ROOT, 'run', '%s.log' % group), 'w', encoding='utf-8')
            log_files.append(log_fp)
            proc = subprocess.Popen(
                [sys.executable, 'start.py'],
                cwd=os.path.join(ROOT, group),
                stdout=log_fp, stderr=subprocess.STDOUT)
            procs.append((group, proc))
            print('started group %s (pid=%d)' % (group, proc.pid))

        for port in WAIT_PORTS:
            if not wait_port(port):
                raise RuntimeError('port %d not ready' % port)
        # 等待组内/组间组网与 app load 完成
        time.sleep(5)

        full_flow('tester', '123456')

        print('== 6. 热更新演示: 对 game 组所有服务 reload app 包 ==')
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, 'tools', 'hotfix.py'),
             os.path.join(ROOT, 'game'), 'all'],
            capture_output=True, text=True, timeout=30, cwd=ROOT)
        print(result.stdout or result.stderr)

        print('== 7. 热更新后再次登录验证服务仍正常 ==')
        full_flow('tester2', '123456')

        print()
        print('########## 全部测试通过 ##########')
    finally:
        for group, proc in procs:
            # /T 杀整棵进程树，避免 multiprocessing 子进程变孤儿残留
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True)
        for fp in log_files:
            fp.close()


if __name__ == '__main__':
    main()
