"""
集群 debug CLI

连到某个进程组 master 的 clusterPort，以集群内部身份收发 RPC——
能调任意 internal key（这正是内外网隔离的另一面：本工具等同于"内网里的人"，
生产环境务必保证 cluster 端口不对公网开放）。

用法:
    python tools/cli.py <组名>                     进入交互模式（REPL）
    python tools/cli.py <组名> <命令> [参数...]    单发模式，执行完退出

命令（json 参数必须是 JSON 对象，handler 调用作为 kwargs 透传；
      tell/ask 的 json 作为 actor 方法的唯一位置参数 data）:
    master  <key> [json]                 在 master 上直接执行（默认命令）
    call    <service> <key> [json]       调用组内指定服务并等结果
    send    <service> <key> [json]       投递到组内指定服务（不等结果）
    cluster <组名> <key> [json]          跨组调用（对端 master 按 key 分发）
    tell    <service> <actor> <key> [json]  向具名 actor 投递
    ask     <service> <actor> <key> [json]  向具名 actor 询问
    keys    <service>                    列出该服务的全部 handler
    actors  <service>                    列出该服务的具名 actor
    hotfix  [service|all] [package]      热更新（默认 all app）
    start   <新服务名> <模板服务名>       动态扩容：按模板拉新进程（端口自动分配）
    help                                 显示帮助
    quit                                 退出交互模式

示例:
    python tools/cli.py game keys game1
    python tools/cli.py game call game1 game.onlineCount
    python tools/cli.py game master world.stats
    python tools/cli.py game ask game1 player:tester get_info '{}'
    python tools/cli.py game start game3 game1   # 动态扩容一个 game3
    python tools/cli.py login master sys.execCode '{"code": "print(1+1)"}'
"""
import asyncio
import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, ROOT)

from engine.net import protocol


class ClusterCli:
    """与一组 master 的 clusterPort 通话的短连接客户端（每条命令一次连接）"""

    def __init__(self, group):
        # group = center|game|login|recharge, 就是ROOT子文件夹的名字
        self.group = group
        config_path = os.path.join(ROOT, group, 'config.json')
        with open(config_path, 'r', encoding='utf-8') as fp:
            config = json.load(fp)
        cluster = config['services']['master']['cluster']
        self.port = cluster['clusterPort']
        self.name = f'{group}.master'

    async def request(self, key, args=(), kwargs=None):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        packet = {'key': key}
        if args:
            packet['args'] = list(args)
            packet['kwargs'] = kwargs or {}
        else:
            packet['args'] = kwargs or {}
        await protocol.write_packet(writer, packet)
        reply = await protocol.read_packet(reader)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return reply

    def call(self, key, *args, **kwargs):
        reply = asyncio.run(self.request(key, args, kwargs))
        if 'error' in reply:
            raise RuntimeError(reply['error'])
        return reply.get('data')


def _parse_json(text):
    if not text:
        return {}
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError('json 参数必须是对象（会作为 kwargs 透传）')
    return obj


def run_command(cli, argv):
    """执行一条命令，返回 False 表示退出"""
    if not argv:
        return True
    cmd, rest = argv[0], argv[1:]

    if cmd in ('quit', 'exit'):
        return False
    if cmd == 'help':
        print(__doc__)
        return True

    if cmd == 'master':
        kwargs = _parse_json(rest[1] if len(rest) > 1 else '')
        return _show(cli.call(rest[0], **kwargs))

    if cmd == 'call':
        service, key = rest[0], rest[1]
        kwargs = _parse_json(rest[2] if len(rest) > 2 else '')
        return _show(cli.call('sys.forwardService', True, service, key, **kwargs))
    if cmd == 'send':
        service, key = rest[0], rest[1]
        kwargs = _parse_json(rest[2] if len(rest) > 2 else '')
        return _show(cli.call('sys.forwardService', False, service, key, **kwargs))
    if cmd == 'cluster':
        group, key = rest[0], rest[1]
        kwargs = _parse_json(rest[2] if len(rest) > 2 else '')
        return _show(cli.call('sys.clusterCall', group, key, **kwargs))
    if cmd == 'tell':
        service, actor_id, key = rest[0], rest[1], rest[2]
        data = json.loads(rest[3]) if len(rest) > 3 else {}
        return _show(cli.call('sys.forwardService', True, service,
                              'sys.tellActor', actor_id, key, data))
    if cmd == 'ask':
        service, actor_id, key = rest[0], rest[1], rest[2]
        data = json.loads(rest[3]) if len(rest) > 3 else {}
        return _show(cli.call('sys.forwardService', True, service,
                              'sys.askActor', actor_id, key, data))
    if cmd == 'keys':
        return _show(cli.call('sys.forwardService', True, rest[0], 'sys.listKeys'))
    if cmd == 'actors':
        return _show(cli.call('sys.forwardService', True, rest[0], 'sys.listActors'))
    if cmd == 'hotfix':
        service = rest[0] if rest else 'all'
        package = rest[1] if len(rest) > 1 else 'app'
        return _show(cli.call('sys.hotfix', service=service, package=package))
    if cmd == 'start':
        if len(rest) < 2:
            raise ValueError('用法: start <新服务名> <模板服务名>')
        return _show(cli.call('sys.startService', name=rest[0], template=rest[1]))

    # 默认：整条当作 master 上的 key 执行
    kwargs = _parse_json(rest[0] if rest else '')
    return _show(cli.call(cmd, **kwargs))


def _show(result):
    print(json.dumps(result, indent=2, ensure_ascii=False, default=repr))
    return True


def repl(cli):
    print(f'已连接 {cli.name} (127.0.0.1:{cli.port})，输入 help 查看命令，quit 退出')
    while True:
        try:
            line = input(f'{cli.group}> ')
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        try:
            if not run_command(cli, shlex.split(line)):
                break
        except Exception as exc:
            print(f'error: {exc}')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cli = ClusterCli(sys.argv[1])
    if len(sys.argv) > 2:
        try:
            run_command(cli, sys.argv[2:])
        except Exception as exc:
            print(f'error: {exc}', file=sys.stderr)
            sys.exit(1)
    else:
        repl(cli)


if __name__ == '__main__':
    main()
