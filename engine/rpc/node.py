# coding:utf-8
"""
RPC 节点基类

组内 RPC(internal) 与组间 RPC(cluster) 共用同一套连接机制:
  - 本节点监听一个端口，同时对配置中的每个对端发起拨号（断线自动重连）
  - 连接建立后互发 hello 包声明自己的名字，之后按名字路由消息

消息类型:
  {"kind": "call",  "seq": n, "key": k, "args": [...], "kwargs": {...}}  # 请求-响应
  {"kind": "send",  "key": k, "args": [...], "kwargs": {...}}            # 单向
  {"kind": "reply", "seq": n, "data": r} / {"kind": "reply", "seq": n, "error": e}

子类需实现 on_request(key, args, kwargs) 把请求分发到本进程的处理函数。
"""
import asyncio

from engine.net import protocol
from engine.util import log


class RpcNode(object):
    #: hello 包中标识名字的字段，子类覆盖（区分 internal / cluster 连接）
    hello_field = 'name'
    #: 对端掉线后的重连间隔
    reconnect_interval = 1.0

    def __init__(self, name, listen_addr):
        self.name = name
        host, port = listen_addr.split(':')
        self.host = host.strip('*') or '0.0.0.0'
        self.port = int(port)
        self._server = None
        self._links = {}          # peer_name -> _Link
        self._pending = {}        # seq -> Future
        self._seq = 0
        self._dial_targets = {}   # peer_name -> (host, port)
        self._stopping = False

    # ---------------------------------------------------------------
    async def start(self, dial_targets=None):
        """dial_targets: {对端名: (host, port)}"""
        self._server = await asyncio.start_server(
            self._on_accept, self.host, self.port)
        log.notice('%s node %s listening on %s:%s',
                   type(self).__name__, self.name, self.host, self.port)
        for peer_name, addr in (dial_targets or {}).items():
            self._dial_targets[peer_name] = addr
            asyncio.ensure_future(self._dial_loop(peer_name, addr))

    async def stop(self):
        self._stopping = True
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for link in list(self._links.values()):
            link.close()

    def is_online(self, peer_name):
        return peer_name in self._links

    def online_peers(self):
        return list(self._links.keys())

    # ---------------------------------------------------------------
    def send(self, peer_name, key, *args, **kwargs):
        """单向消息，不等待结果"""
        link = self._links.get(peer_name)
        if link is None:
            log.warning('%s: send to offline peer %s key %s', self.name, peer_name, key)
            return False
        link.write({'kind': 'send', 'key': key, 'args': list(args), 'kwargs': kwargs})
        return True

    async def call(self, peer_name, key, *args, timeout=30, **kwargs):
        """请求-响应式调用"""
        link = self._links.get(peer_name)
        if link is None:
            raise RuntimeError('%s: peer %s is offline (key %s)' % (self.name, peer_name, key))
        self._seq += 1
        seq = self._seq
        fut = asyncio.get_event_loop().create_future()
        self._pending[seq] = fut
        link.write({'kind': 'call', 'seq': seq, 'key': key,
                    'args': list(args), 'kwargs': kwargs})
        return await asyncio.wait_for(fut, timeout)

    def broadcast(self, key, *args, **kwargs):
        """向所有已连接对端发单向消息"""
        for peer_name in list(self._links.keys()):
            self.send(peer_name, key, *args, **kwargs)

    async def broadcast_call(self, key, *args, **kwargs):
        """向所有已连接对端发 call，返回 {对端名: 结果}"""
        results = {}
        if not self._links:
            return results
        futs = {name: asyncio.ensure_future(self.call(name, key, *args, **kwargs))
                for name in list(self._links.keys())}
        for name, fut in futs.items():
            try:
                results[name] = await fut
            except Exception as exc:
                log.warning('broadcast_call %s to %s failed: %s', key, name, exc)
                results[name] = None
        return results

    # ---------------------------------------------------------------
    async def on_request(self, key, args, kwargs):
        """子类实现：把请求分发到本进程的处理函数"""
        raise NotImplementedError

    async def on_raw_connection(self, link, first_packet):
        """
        非节点连接（未先发 hello 包的连接，如热更新 / GM 管理工具）。
        请求格式 {"key": k, "args": {...}}，回复 {"key": k, "data": r} 后关闭。
        """
        packet = first_packet
        while packet is not None:
            key = packet.get('key')
            try:
                result = await self.on_request(key, [], packet.get('args') or {})
                await protocol.write_packet(link.writer, {'key': key, 'data': result})
            except Exception as exc:
                log.exception('raw request %s error', key)
                await protocol.write_packet(link.writer, {'key': key, 'error': str(exc)})
            packet = await protocol.read_packet(link.reader)

    def on_peer_connected(self, peer_name):
        log.notice('%s: peer %s connected', self.name, peer_name)

    def on_peer_disconnected(self, peer_name):
        log.warning('%s: peer %s disconnected', self.name, peer_name)

    # ---------------------------------------------------------------
    async def _on_accept(self, reader, writer):
        link = _Link(self, reader, writer)
        await link.run()

    async def _dial_loop(self, peer_name, addr):
        host, port = addr
        while not self._stopping:
            if peer_name in self._links:
                await asyncio.sleep(self.reconnect_interval)
                continue
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(self.reconnect_interval)
                continue
            link = _Link(self, reader, writer, hello_name=peer_name)
            await link.run()
            await asyncio.sleep(self.reconnect_interval)

    def _register_link(self, peer_name, link):
        old = self._links.get(peer_name)
        if old is not None and old is not link:
            old.close()
        self._links[peer_name] = link
        self.on_peer_connected(peer_name)

    def _unregister_link(self, peer_name, link):
        if self._links.get(peer_name) is link:
            del self._links[peer_name]
            self.on_peer_disconnected(peer_name)


class _Link(object):
    """一条到对端的连接"""

    def __init__(self, node, reader, writer, hello_name=None):
        self.node = node
        self.reader = reader
        self.writer = writer
        self.peer_name = None
        self._hello_name = hello_name  # 主动拨号时已知对端名
        self._closed = False

    def write(self, obj):
        if self._closed:
            return
        try:
            self.writer.write(protocol.encode(obj))
        except (ConnectionResetError, BrokenPipeError):
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.close()
        except Exception:
            pass

    async def run(self):
        node = self.node
        try:
            # 主动方先声明自己的名字
            if self._hello_name is not None:
                self.write({node.hello_field: node.name})
            packet = await protocol.read_packet(self.reader)
            if packet is None:
                return
            if node.hello_field not in packet:
                # 非节点连接（如管理工具），交给节点按普通请求处理
                await node.on_raw_connection(self, packet)
                return
            self.peer_name = packet[node.hello_field]
            if self._hello_name is None:
                # 被动方回声明
                self.write({node.hello_field: node.name})
            elif self._hello_name != self.peer_name:
                log.warning('%s: expect peer %s but got %s',
                            node.name, self._hello_name, self.peer_name)
            node._register_link(self.peer_name, self)
            while True:
                packet = await protocol.read_packet(self.reader)
                if packet is None:
                    break
                await self._handle(packet)
        except protocol.ProtocolError as exc:
            log.warning('rpc protocol error: %s', exc)
        except Exception:
            log.exception('rpc link error')
        finally:
            self.close()
            if self.peer_name is not None:
                node._unregister_link(self.peer_name, self)

    async def _handle(self, packet):
        node = self.node
        kind = packet.get('kind')
        if kind == 'reply':
            fut = node._pending.pop(packet.get('seq'), None)
            if fut is not None and not fut.done():
                if 'error' in packet:
                    fut.set_exception(RuntimeError(packet['error']))
                else:
                    fut.set_result(packet.get('data'))
        elif kind == 'call':
            asyncio.ensure_future(self._handle_call(packet))
        elif kind == 'send':
            try:
                await node.on_request(packet['key'],
                                      packet.get('args') or [],
                                      packet.get('kwargs') or {})
            except Exception:
                log.exception('rpc send %s error', packet.get('key'))

    async def _handle_call(self, packet):
        seq = packet['seq']
        try:
            result = await self.node.on_request(
                packet['key'], packet.get('args') or [], packet.get('kwargs') or {})
            self.write({'kind': 'reply', 'seq': seq, 'data': result})
        except Exception as exc:
            log.exception('rpc call %s error', packet.get('key'))
            self.write({'kind': 'reply', 'seq': seq, 'error': str(exc)})
