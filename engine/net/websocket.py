"""
极简 WebSocket（RFC6455）实现，纯标准库，供 Cocos / H5 / 小程序客户端接入

只支持服务端视角的文本帧（text frame），消息内容为与 TCP 完全一致的 JSON 包:
  - 握手: HTTP Upgrade + Sec-WebSocket-Accept(SHA1+base64)
  - 收帧: 处理掩码(客户端必带)、分片拼接、ping->pong、close->None
  - 发帧: 文本帧、不带掩码(服务端不允许带)
"""
import asyncio
import base64
import hashlib
import json
import struct

from engine.net.protocol import ProtocolError

_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

OP_CONT = 0x0
OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_MESSAGE_SIZE = 4 * 1024 * 1024  # 与 TCP 协议一致


async def handshake(reader, writer):
    """处理 HTTP Upgrade 握手。成功返回 True，失败返回 False（连接由调用方关闭）"""
    head = b''
    try:
        while b'\r\n\r\n' not in head:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
            if not chunk:
                return False
            head += chunk
            if len(head) > 16 * 1024:
                return False
    except (TimeoutError, ConnectionResetError):
        return False
    headers = {}
    lines = head.decode('latin-1').split('\r\n')
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            headers[k.strip().lower()] = v.strip()
    key = headers.get('sec-websocket-key')
    if not key or 'websocket' not in headers.get('upgrade', '').lower():
        return False
    accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
    writer.write(('HTTP/1.1 101 Switching Protocols\r\n'
                  'Upgrade: websocket\r\n'
                  'Connection: Upgrade\r\n'
                  f'Sec-WebSocket-Accept: {accept}\r\n\r\n').encode('latin-1'))
    await writer.drain()
    return True


async def read_packet(reader):
    """读一条完整文本消息并解成 dict；连接关闭/收到 close 帧返回 None"""
    buf = bytearray()
    while True:
        frame = await _read_frame(reader)
        if frame is None:
            return None
        fin, opcode, payload = frame
        if opcode == OP_CLOSE:
            return None
        if opcode == OP_PING:
            continue  # 由写侧心跳策略处理，这里只收包；调用方会话无需感知
        if opcode == OP_PONG:
            continue
        buf += payload
        if len(buf) > MAX_MESSAGE_SIZE:
            raise ProtocolError(f"ws message too large: {len(buf)}")
        if fin:
            try:
                return json.loads(buf.decode('utf-8'))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ProtocolError(f"bad ws message: {exc}")


async def write_packet(writer, obj):
    """与 TCP 侧 protocol.write_packet 同签名：把 dict 以文本帧发出"""
    payload = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    writer.write(_make_frame(OP_TEXT, payload))
    await writer.drain()


async def write_close(writer):
    try:
        writer.write(_make_frame(OP_CLOSE, b''))
        await writer.drain()
    except Exception:
        pass


# ---------------------------------------------------------------------
async def _read_frame(reader):
    """读一个帧，返回 (fin, opcode, payload)；连接断开返回 None"""
    try:
        head = await reader.readexactly(2)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        return None
    fin = bool(head[0] & 0x80)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack('>H', await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack('>Q', await reader.readexactly(8))[0]
    if length > MAX_MESSAGE_SIZE:
        raise ProtocolError(f"bad ws frame size: {length}")
    mask = await reader.readexactly(4) if masked else None
    payload = await reader.readexactly(length) if length else b''
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


def _make_frame(opcode, payload):
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack('>H', n)
    else:
        head += bytes([127]) + struct.pack('>Q', n)
    return head + payload
