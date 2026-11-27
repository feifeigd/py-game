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

from test_client import GameClient, full_flow, pick_role, test_server_isolation  # noqa: E402

# (目录, 配置文件, 日志名)；game 组多开两个服
GROUPS = [
    ('center', 'config.json', 'center'),
    ('recharge', 'config.json', 'recharge'),
    ('game', 'config_1001.json', 'game_1001'),
    ('game', 'config_1002.json', 'game_1002'),
    ('login', 'config.json', 'login'),
]
# 等待这些端口就绪再开始测试
WAIT_PORTS = [11100, 21101, 21102, 22101, 22102, 41100]


def cluster_call(group, rpc_key, **kwargs):
    """直连目标组 master 的 clusterPort 发一条原始 RPC（仿 tools/hotfix.py）"""
    import asyncio

    from engine.net import protocol
    config_path = os.path.join(ROOT, group, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as fp:
        config = json.load(fp)
    port = config['services']['master']['cluster']['clusterPort']

    async def _call():
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await protocol.write_packet(writer, {'key': rpc_key, 'args': kwargs})
        reply = await protocol.read_packet(reader)
        writer.close()
        return reply

    return asyncio.run(_call())


def wait_handler_ready(service, key, timeout=30):
    """轮询目标服务直到指定 handler 注册完成（即该服务 app load 完成）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            reply = cluster_call('game', 'sys.forwardService',
                                 is_call=True, service_name=service,
                                 key='sys.listKeys')
            keys = reply.get('data') or {}
            if key in keys.get('public', []) + keys.get('internal', []):
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f'{service} 的 {key} 就绪超时')


def test_cluster_cannot_fake_enter():
    """内外网隔离：集群内 RPC 伪造 game.enter 必须被拒，且不留幽灵玩家"""
    print('== 0. 集群侧伪造 game.enter / login.auth 必须被拒 ==')
    # app load 完成前 game.enter 尚未注册，启动快慢不一，先等 game1 就绪
    wait_handler_ready('game1', 'game.enter')
    reply = cluster_call('game', 'sys.forwardService',
                         is_call=True, service_name='game1', key='game.enter',
                         session=None, data={'account': 'hacker', 'token': 'x'})
    result = reply.get('data') or {}
    assert not result.get('ok'), reply
    assert result.get('error') == 'invalid session', reply
    print('  cluster game.enter(session=None) -> 已拒绝:', result)

    # 拒绝发生在任何副作用之前：game1 不应多出在线玩家
    reply = cluster_call('game', 'sys.forwardService',
                         is_call=True, service_name='game1', key='game.onlineCount')
    assert reply.get('data') == 0, reply
    print('  game.onlineCount -> 0（无幽灵玩家）')

    reply = cluster_call('login', 'sys.forwardService',
                         is_call=True, service_name='login', key='login.auth',
                         session=None, data={'account': 'hacker', 'password': '123456'})
    result = reply.get('data') or {}
    assert not result.get('ok'), reply
    assert result.get('error') == 'invalid session', reply
    print('  cluster login.auth(session=None) -> 已拒绝:', result)


def test_token_expiry(account='tokexp', server='1001'):
    """令牌 TTL: 拿了令牌不来连 game，过期后 game.enter 必须被拒（token expired）"""
    print(f'== 5.6 令牌过期: login 发令牌后等 TTL 再进 game，必须被拒 ==')
    login_client = GameClient().connect('127.0.0.1:11100')
    result = login_client.call('login.auth',
                               {'account': account, 'password': '123456'})
    assert result['ok'], result
    if not result.get('token'):
        result = login_client.call('login.enterServer', {'server': server})
        assert result['ok'], result
        result = pick_role(login_client, account, server, result)
    token, game_addr = result['token'], result['gameAddr']
    role = result.get('role', '')
    login_client.close()

    # config_1001.json loginTokenTtl=5，等 6 秒让令牌过期
    time.sleep(6)
    game = GameClient().connect(game_addr)
    result = game.call('game.enter',
                       {'account': account, 'role': role, 'token': token})
    assert not result.get('ok'), result
    assert result.get('error') == 'token expired', result
    print('  过期令牌 game.enter -> 已拒绝: token expired ✓')
    game.close()


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
        for group, config_name, log_name in GROUPS:
            log_fp = open(os.path.join(ROOT, 'run', f'{log_name}.log'), 'w', encoding='utf-8')
            log_files.append(log_fp)
            proc = subprocess.Popen(
                [sys.executable, 'start.py', config_name],
                cwd=os.path.join(ROOT, group),
                stdout=log_fp, stderr=subprocess.STDOUT)
            procs.append((log_name, proc))
            print(f'started group {log_name} (pid={proc.pid})')

        for port in WAIT_PORTS:
            if not wait_port(port):
                raise RuntimeError('port %d not ready' % port)
        # 等待组内/组间组网与 app load 完成
        time.sleep(5)

        test_cluster_cannot_fake_enter()
        full_flow('tester', '123456', server='1001')
        test_token_expiry()

        print('== 6. 热更新演示: 对 game 服 1001 所有服务 reload app 包 ==')
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, 'tools', 'hotfix.py'),
             os.path.join(ROOT, 'game'), 'all', 'app', 'config_1001.json'],
            capture_output=True, text=True, timeout=30, cwd=ROOT)
        print(result.stdout or result.stderr)

        print('== 7. 热更新后再次登录验证服务仍正常 ==')
        full_flow('tester2', '123456', server='1001')

        # 服 1002 全流程 + 双服数据隔离
        full_flow('tester3', '123456', server='1002')
        test_server_isolation('zoner')

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
