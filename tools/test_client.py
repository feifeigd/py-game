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
                    self.pushes.append(packet)
                    print('  [push] %s %s' % (packet.get('key'),
                                                json.dumps(packet.get('data'), ensure_ascii=False)))


def http_get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode('utf-8')


def full_flow(account='tester', password='123456'):
    """完整流程: 登录 -> 进游戏 -> 聊天 -> 查信息 -> 充值"""
    print('== 1. 连接 login 服务器并登录 ==')
    login_client = GameClient().connect('127.0.0.1:11100')
    result = login_client.call('login.auth', {'account': account, 'password': password})
    print('  login.auth ->', json.dumps(result, ensure_ascii=False))
    assert result['ok'], result
    token, game_addr = result['token'], result['gameAddr']
    login_client.close()

    print('== 2. 连接 game 进程 %s 并进入游戏 ==' % game_addr)
    game = GameClient().connect(game_addr)
    result = game.call('game.enter', {'account': account, 'token': token})
    print('  game.enter ->', json.dumps(result, ensure_ascii=False))
    assert result['ok'], result

    print('== 3. 聊天（走玩家 actor -> world 全服跑马灯）==')
    result = game.call('player.chat', {'text': '大家好，我是 %s' % account})
    print('  player.chat ->', result)

    print('== 4. 查询角色信息 ==')
    result = game.call('player.getInfo', {})
    print('  player.getInfo ->', json.dumps(result, ensure_ascii=False))

    print('== 5. 充值（HTTP 回调 -> recharge 组 -> world -> 玩家 actor）==')
    resp = http_get('http://127.0.0.1:41100/pay?account=%s&item=diamond&count=100' % account)
    print('  /pay ->', resp)

    import time
    time.sleep(1)  # 等推送
    result = game.call('player.getInfo', {})
    print('  充值后 player.getInfo ->', json.dumps(result, ensure_ascii=False))
    assert result['diamond'] == 100, result

    pushes = [p['key'] for p in game.pushes]
    print('== 收到的推送: %s ==' % pushes)
    assert 'recharge.push' in pushes
    assert 'chat.push' in pushes
    game.close()
    print('== 全流程测试通过 ==')
    return True


if __name__ == '__main__':
    account = sys.argv[1] if len(sys.argv) > 1 else 'tester'
    password = sys.argv[2] if len(sys.argv) > 2 else '123456'
    full_flow(account, password)
