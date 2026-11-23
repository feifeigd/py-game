# coding:utf-8
"""
轻量 HTTP 服务器（仅依赖 asyncio），用于充值回调、GM 接口等

用法:
    web = WebServer('recharge', '127.0.0.1:15100')

    @web.route('/pay')
    async def pay(query, body):
        return {'ret': 0}   # dict -> application/json；str -> text/plain
"""
import asyncio
import json
import urllib.parse

from engine.util import log


class WebServer(object):
    def __init__(self, name, addr, max_connection=400):
        self.name = name
        host, port = addr.split(':')
        self.host = host.strip('*') or '0.0.0.0'
        self.port = int(port)
        self.max_connection = max_connection
        self._routes = {}
        self._server = None

    def route(self, path):
        def deco(func):
            self._routes[path] = func
            return func
        return deco

    async def start(self):
        self._server = await asyncio.start_server(
            self._on_conn, self.host, self.port, limit=64 * 1024)
        log.notice('web server %s listening on %s:%s', self.name, self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ---------------------------------------------------------------
    async def _on_conn(self, reader, writer):
        try:
            request = await asyncio.wait_for(self._read_request(reader), timeout=30)
            if request is None:
                writer.close()
                return
            method, path, query, body = request
            func = self._routes.get(path)
            if func is None:
                await self._respond(writer, 404, 'not found: %s' % path)
                return
            try:
                result = func(query, body)
                if asyncio.iscoroutine(result):
                    result = await result
                await self._respond(writer, 200, result if result is not None else 'ok')
            except Exception as exc:
                log.exception('web handle %s error', path)
                await self._respond(writer, 500, 'error: %s' % exc)
        except Exception:
            log.exception('web conn error')
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _read_request(self, reader):
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = await reader.read(4096)
            if not chunk:
                return None
            head += chunk
            if len(head) > 64 * 1024:
                return None
        head_text, _, rest = head.partition(b'\r\n\r\n')
        lines = head_text.decode('latin-1').split('\r\n')
        method, target, _ = lines[0].split(' ', 2)
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()
        content_length = int(headers.get('content-length', 0))
        body_bytes = rest
        while len(body_bytes) < content_length:
            chunk = await reader.read(content_length - len(body_bytes))
            if not chunk:
                break
            body_bytes += chunk

        parsed = urllib.parse.urlparse(target)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if method == 'POST' and headers.get('content-type', '').startswith(
                'application/x-www-form-urlencoded'):
            query.update({k: v[0] for k, v in
                          urllib.parse.parse_qs(body_bytes.decode('utf-8')).items()})
            body = ''
        else:
            body = body_bytes.decode('utf-8', errors='replace')
        return method, parsed.path, query, body

    async def _respond(self, writer, status, data):
        reason = {200: 'OK', 404: 'Not Found', 500: 'Internal Server Error'}.get(status, 'OK')
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        else:
            body = str(data).encode('utf-8')
            content_type = 'text/plain; charset=utf-8'
        head = ('HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
                'Connection: close\r\n\r\n' % (status, reason, content_type, len(body)))
        writer.write(head.encode('latin-1') + body)
        await writer.drain()
