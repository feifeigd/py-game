# coding:utf-8
"""
模拟客户端（与 Unity 客户端使用同一协议），供全流程联调测试

被 tools/test_all.py 调用，也可单独用:
    python tools/test_client.py <account> <password>
"""
import json
import socket
import struct
import sys
import threading
import urllib.request


class GameClient(object):
    """同步 socket 客户端：与服务器同样的 4 字节长度前缀 + JSON 协议"""

    def __init__(self):
        self.sock = None
        self.seq = 0
        self.pushes = []          # 收到的服务器推送
        self._pending = {}        # seq -> 响应
        self._cond = threading.Condition()
        self._closed = False

    def connect(self, addr):
        host, port = addr.split(':')
        self.sock = socket.create_connection((host, int(port)), timeout=10)
        threading.Thread(target=self._read_loop, daemon=True).start()
        return self

    def close(self):
        self._closed = True
        try:
            self.sock.close()
        except Exception:
            pass

    # ---------------------------------------------------------------
    def call(self, key, data=None, timeout=10):
        self.seq += 1
        seq = self.seq
        # 协议约定: data 是业务数据的 JSON 字符串（与 Unity Packet.cs 一致）
        self._send({'key': key, 'seq': seq, 'data': json.dumps(data or {}, ensure_ascii=False)})
        with self._cond:
            if not self._cond.wait_for(lambda: seq in self._pending, timeout):
                raise TimeoutError('call %s timeout' % key)
            reply = self._pending.pop(seq)
        if 'error' in reply:
            raise RuntimeError('%s error: %s' % (key, reply['error']))
        data = reply.get('data')
        return json.loads(data) if isinstance(data, str) else data

    def _send(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.sock.sendall(struct.pack('>I', len(body)) + body)

    def _read_loop(self):
        buf = b''
        while not self._closed:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 4:
                size = struct.unpack('>I', buf[:4])[0]
                if len(buf) < 4 + size:
                    break
                packet = json.loads(buf[4:4 + size].decode('utf-8'))
                buf = buf[4 + size:]
                if 'seq' in packet:
                    with self._cond:
                        self._pending[packet['seq']] = packet
                        self._cond.notify_all()
                else:
                    data = packet.get('data')
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except ValueError:
                            pass
                    self.pushes.append(packet)
                    print('  [push] %s %s' % (packet.get('key'),
                                                json.dumps(data, ensure_ascii=False)))


def http_get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode('utf-8')


def full_flow(account='tester', password='123456', server='1001'):
    """完整流程: 登录（auth）-> 选服（enterServer）-> 进游戏 -> 聊天 -> 查信息 -> 充值"""
    print(f'== 1. 连接 login 服务器登录账号，再选服 {server} ==')
    login_client = GameClient().connect('127.0.0.1:11100')
    result = login_client.call('login.auth', {'account': account, 'password': password})
    print('  login.auth ->', json.dumps(result, ensure_ascii=False))
    assert result['ok'], result
    assert any(s['server'] == server for s in result['servers']), result
    if result.get('token'):
        # 只有一个服：auth 已直接发令牌，免去 login.enterServer
        print(f'  单服直发令牌: server={result["server"]} gameAddr={result["gameAddr"]}')
        assert result['server'] == server, result
    else:
        # 选服：发令牌 + 分配该服的 game 进程
        result = login_client.call('login.enterServer', {'server': server})
        print('  login.enterServer ->', json.dumps(result, ensure_ascii=False))
        assert result['ok'], result
        assert result.get('server') == server, result
    token, game_addr = result['token'], result['gameAddr']
    login_client.close()

    print(f'== 2. 连接 game 进程 {game_addr} 并进入游戏 ==')
    game = GameClient().connect(game_addr)
    result = game.call('game.enter', {'account': account, 'token': token})
    print('  game.enter ->', json.dumps(result, ensure_ascii=False))
    assert result['ok'], result

    print('== 2.1 内外网隔离：内部 key 必须被接入层拒绝 ==')
    for bad_key in ('world.verifyToken', 'sys.hotfix', 'sys.execCode'):
        try:
            game.call(bad_key, {})
            raise AssertionError(f'{bad_key} 不应允许客户端调用')
        except RuntimeError as exc:
            assert 'not allowed' in str(exc), exc
            print(f'  {bad_key} -> 已拒绝: {exc}')
    # 未登录会话同样不得调用内部 key（login 连接已关闭，重连一个裸会话验证）
    raw = GameClient().connect(game_addr)
    for bad_key in ('world.verifyToken', 'player.chat'):
        try:
            raw.call(bad_key, {})
            raise AssertionError(f'未登录会话不应允许调用 {bad_key}')
        except RuntimeError as exc:
            assert 'not allowed' in str(exc), exc
            print(f'  未登录 {bad_key} -> 已拒绝')
    raw.close()

    print('== 3. 聊天（走玩家 actor -> world 全服跑马灯）==')
    result = game.call('player.chat', {'text': f'大家好，我是 {account}'})
    print('  player.chat ->', result)

    print('== 4. 查询角色信息 ==')
    result = game.call('player.getInfo', {})
    print('  player.getInfo ->', json.dumps(result, ensure_ascii=False))

    print('== 5. 充值（HTTP 回调 -> recharge 组 -> 服 world -> 玩家 actor）==')
    before = result['diamond']
    resp = http_get(f'http://127.0.0.1:41100/pay?account={account}&item=diamond&count=100&server={server}')
    print('  /pay ->', resp)

    import time
    time.sleep(1)  # 等推送
    result = game.call('player.getInfo', {})
    print('  充值后 player.getInfo ->', json.dumps(result, ensure_ascii=False))
    assert result['diamond'] == before + 100, (before, result)

    pushes = [p['key'] for p in game.pushes]
    print(f'== 收到的推送: {pushes} ==')
    assert 'recharge.push' in pushes
    assert 'chat.push' in pushes
    game.close()
    print(f'== 服 {server} 全流程测试通过 ==')
    return True


def test_server_isolation(account='zoner'):
    """服数据隔离: 同一账号在两个服是两个独立角色（各自 Mongo 库）"""
    print(f'== 8. 服隔离: 同一账号 {account} 在 1001/1002 数据独立 ==')

    def enter(server):
        c = GameClient().connect('127.0.0.1:11100')
        r = c.call('login.auth', {'account': account, 'password': '123456'})
        assert r['ok'], r
        if not r.get('token'):   # 单服时 auth 已直发令牌，无需选服
            r = c.call('login.enterServer', {'server': server})
            assert r['ok'], r
        c.close()
        g = GameClient().connect(r['gameAddr'])
        r2 = g.call('game.enter', {'account': account, 'token': r['token']})
        assert r2['ok'], r2
        return g, r2['player']

    # 1001：进游戏，在线充 100 钻
    g1, player1 = enter('1001')
    print(f'  服 1001 进入: {player1}')
    d1 = player1['diamond']
    http_get(f'http://127.0.0.1:41100/pay?account={account}&item=diamond&count=100&server=1001')
    import time
    time.sleep(1)
    after1 = g1.call('player.getInfo', {})['diamond']
    assert after1 == d1 + 100, (d1, after1)
    print(f'  服 1001 充值: {d1} -> {after1}')
    g1.close()

    # 1002：同账号是另一个角色，从未充值，应是默认档（0 钻）
    g2, player2 = enter('1002')
    print(f'  服 1002 进入: {player2}')
    assert player2['diamond'] == 0, player2
    assert player2['diamond'] != after1
    g2.close()
    print('== 服隔离验证通过 ==')
    return True


if __name__ == '__main__':
    account = sys.argv[1] if len(sys.argv) > 1 else 'tester'
    password = sys.argv[2] if len(sys.argv) > 2 else '123456'
    full_flow(account, password)
