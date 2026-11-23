"""center 进程组启动入口: python start.py"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(1, os.path.dirname(ROOT))

from engine.start.master import Master

if __name__ == '__main__':
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.json'
    Master(config_path).start()
