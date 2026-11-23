# coding:utf-8
"""
通信协议: 4 字节大端长度前缀 + UTF-8 JSON

客户端消息、组内 RPC、组间 RPC 均使用本协议，
Unity 客户端使用同样的帧格式（见 client/Assets/Scripts/Net/Packet.cs）。
"""
import asyncio
import json
import struct

MAX_PACKET_SIZE = 4 * 1024 * 1024  # 4MB


class ProtocolError(Exception):
    pass


def encode(obj):
    """对象 -> 带长度前缀的字节串"""
    body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    return struct.pack('>I', len(body)) + body


def decode(body):
    return json.loads(body.decode('utf-8'))


async def read_packet(reader):
    """从 asyncio.StreamReader 读取一个包，连接关闭返回 None"""
    try:
        head = await reader.readexactly(4)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        return None
    size = struct.unpack('>I', head)[0]
    if size <= 0 or size > MAX_PACKET_SIZE:
        raise ProtocolError('bad packet size: %d' % size)
    try:
        body = await reader.readexactly(size)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        return None
    return decode(body)


async def write_packet(writer, obj):
    writer.write(encode(obj))
    await writer.drain()
