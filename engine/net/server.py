"""
面向客户端的 TCP 服务器

每个客户端连接对应一个 ClientSession。消息分发规则:
  - key 以 "player." 开头且会话已登录 -> 路由到该会话绑定的玩家 Actor
  - 其它 key -> 查 handler 注册表，处理函数签名为 func(session, data)

请求:  {"key": "game.enter", "seq": 1, "data": {...}}
响应:  {"key": "game.enter", "seq": 1, "data": {...}} 或带 "error" 字段
推送:  {"key": "chat.push", "data": {...}}（无 seq）
"""
import asyncio
import json

from engine.core import handler as handler_reg
from engine.net import protocol
from engine.util import log

PLAYER_KEY_PREFIX = 'player.'


class ClientSession:
    _next_id = 1

    def __init__(self, server, reader, writer):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.session_id = ClientSession._next_id
        ClientSession._next_id += 1
        self.player = None      # 登录后绑定的玩家 Actor
        self.account = None
        self.data = {}          # 业务自定义数据
        # 发包实现：TCP 会话用 protocol.write_packet，WS 会话由 WsServer 覆盖
        self.write_packet_fn = protocol.write_packet

        self._closed = False

    @property
    def peer(self):
        addr = self.writer.get_extra_info('peername')
        return '{}:{}'.format(*addr) if addr else '?'

    async def reply(self, key, seq, data):
        await self._send({'key': key, 'seq': seq, 'data': json.dumps(data)})

    async def reply_error(self, key, seq, error):
        await self._send({'key': key, 'seq': seq, 'error': str(error)})

    async def push(self, key, data):
        """服务器主动推送（与回包一致: data 为业务 JSON 字符串）"""
        await self._send({'key': key, 'data': json.dumps(data)})

    async def _send(self, obj):
        if self._closed:
            return
        try:
            log.debug("obj=%s", obj)
            await self.write_packet_fn(self.writer, obj)
        except (TimeoutError, ConnectionResetError, BrokenPipeError):
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.close()
        except (AttributeError, OSError, RuntimeError) as exc:
            log.warning('failed to close session %s: %s', self.peer, exc)
        self.server.on_session_closed(self)


class TcpServer:
    def __init__(self, name, addr):
        self.name = name
        host, port = addr.split(':')
        self.host = host.strip('*') or '0.0.0.0'
        self.port = int(port)
        self.sessions = {}
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._on_client, self.host, self.port)
        log.notice('tcp server %s listening on %s:%s', self.name, self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for session in list(self.sessions.values()):
            session.close()

    def on_session_closed(self, session):
        self.sessions.pop(session.session_id, None)
        # from engine.context import CTX
        player = session.player
        if player is not None and not player.stopped:
            player.tell('__logout__')
        log.info('session closed: %s account=%s', session.peer, session.account)

    # ---------------------------------------------------------------
    async def _on_client(self, reader, writer):
        session = ClientSession(self, reader, writer)
        await self._serve(session, protocol.read_packet)

    async def _serve(self, session, read_packet_fn):
        """会话主循环：读包 -> 并发分发。read_packet_fn 由子类替换（TCP/WS）"""
        self.sessions[session.session_id] = session
        log.info('new session: %s', session.peer)
        try:
            while True:
                packet = await read_packet_fn(session.reader)
                if packet is None:
                    break
                asyncio.ensure_future(self._dispatch(session, packet))
        except protocol.ProtocolError as exc:
            log.warning('protocol error from %s: %s', session.peer, exc)
        except Exception:
            log.exception('session %s error', session.peer)
        finally:
            session.close()

    async def _dispatch(self, session: ClientSession, packet):
        key = packet.get('key')
        seq = packet.get('seq')
        data = packet.get('data') or '{}'
        try:
            data = json.loads(data)
            if key.startswith(PLAYER_KEY_PREFIX) and session.player is not None:
                # 路由到玩家 actor，串行处理该玩家的消息
                result = await session.player.ask(key[len(PLAYER_KEY_PREFIX):], data)
            else:
                result = await handler_reg.dispatch(key, session, data)
        except Exception as exc:
            log.exception('handle client key %s error', key)
            if seq is not None:
                await session.reply_error(key, seq, exc)
            return
        if seq is not None:
            await session.reply(key, seq, result)


class WsServer(TcpServer):
    """WebSocket 接入（Cocos / H5 / 小程序客户端），包格式与 TCP 完全一致"""

    async def start(self):
        self._server = await asyncio.start_server(
            self._on_client, self.host, self.port)
        log.notice('ws server %s listening on %s:%s', self.name, self.host, self.port)

    async def _on_client(self, reader, writer):
        from engine.net import websocket
        if not await websocket.handshake(reader, writer):
            try:
                writer.close()
            except Exception:
                pass
            return
        session = ClientSession(self, reader, writer)
        session.write_packet_fn = websocket.write_packet
        await self._serve(session, websocket.read_packet)
