# coding:utf-8
"""
热更新工具

用法（在工作区根目录）:
    python tools/hotfix.py game           # 热更 game 组所有服务的 app 包
    python tools/hotfix.py game game1     # 只热更 game1 服务
    python tools/hotfix.py login all app  # 指定包名

原理: 连接目标组 master 的 clusterPort，发送 sys.hotfix 管理消息，
master 经组内 RPC 把 sys.doHotfix 分发到各服务进程执行 importlib.reload。
"""
import asyncio
import json
import os
import sys

sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.net import protocol


async def main():
    project = sys.argv[1] if len(sys.argv) > 1 else 'game'
    service = sys.argv[2] if len(sys.argv) > 2 else 'all'
    package = sys.argv[3] if len(sys.argv) > 3 else 'app'

    config_path = os.path.join(project, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as fp:
        config = json.load(fp)
    cluster_port = config['services']['master']['cluster']['clusterPort']

    reader, writer = await asyncio.open_connection('127.0.0.1', cluster_port)
    await protocol.write_packet(writer, {
        'key': 'sys.hotfix',
        'args': {'service': service, 'package': package},
    })
    reply = await protocol.read_packet(reader)
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    writer.close()


if __name__ == '__main__':
    asyncio.run(main())
