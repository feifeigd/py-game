# coding:utf-8
"""
选服状态/注册拦截专项测试:
  - 维护(0) / 备用(4) / 未到开服时间的服: 不出现在 login.auth 的服务器列表里，
    客户端绕过列表直接 login.enterServer 也一律返回 server unavailable
  - 爆满(3)的服: 列表照常展示，新注册 enterServer 返回 server busy，
    已注册老玩家回本服不受影响
  - 所有拦截都发生在注册控制之前，register_count 不会虚增
正常服(status=1)不受影响，enterServer 照常发令牌。

用法（工作区根目录）: uv run python tools/test_server_status.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, ROOT)
sys.path.insert(1, os.path.join(ROOT, 'tools'))

from test_client import GameClient  # noqa: E402
from test_all import GROUPS, WAIT_PORTS, wait_port  # noqa: E402

DSN = 'postgresql://postgres:123456@127.0.0.1:5432/pygame'


def set_server(real_id, **fields):
    """直接改 servers 表模拟 GM 操作"""

    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            sets = ', '.join(f'{k} = ${i + 2}' for i, k in enumerate(fields))
            await conn.execute(
                f'UPDATE servers SET {sets} WHERE real_id = $1',
                real_id, *fields.values())
        finally:
            await conn.close()

    asyncio.run(_run())


def register_count(real_id):
    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetchval(
                'SELECT register_count FROM servers WHERE real_id = $1', real_id)
        finally:
            await conn.close()

    return asyncio.run(_run())


def auth(account):
    c = GameClient().connect('127.0.0.1:11100')
    r = c.call('login.auth', {'account': account, 'password': '123456'})
    assert r['ok'], r
    return c, r


def expect_unavailable(client, server, why):
    r = client.call('login.enterServer', {'server': server})
    assert not r.get('ok'), r
    assert r.get('error') == 'server unavailable', (why, r)
    print(f'  {why}: enterServer {server} -> server unavailable ✓')


def main():
    procs, log_files = [], []
    try:
        os.makedirs(os.path.join(ROOT, 'run'), exist_ok=True)
        for group, config_name, log_name in GROUPS:
            log_fp = open(os.path.join(ROOT, 'run', f'{log_name}.log'), 'w', encoding='utf-8')
            log_files.append(log_fp)
            proc = subprocess.Popen(
                [sys.executable, 'start.py', config_name],
                cwd=os.path.join(ROOT, group),
                stdout=log_fp, stderr=subprocess.STDOUT)
            procs.append(proc)
        for port in WAIT_PORTS:
            if not wait_port(port):
                raise RuntimeError('port %d not ready' % port)
        time.sleep(5)  # 等组网与 app load

        # 预热：enterServer 依赖两服 world 的 prepareLogin 就绪，轮询到通为止
        deadline = time.time() + 60
        while True:
            try:
                wc, wr = auth('warmup')
                wr = wc.call('login.enterServer', {'server': '1001'})
                assert wr['ok'], wr
                wr = wc.call('login.enterServer', {'server': '1002'})
                assert wr['ok'], wr
                wc.close()
                break
            except (AssertionError, RuntimeError, TimeoutError):
                if time.time() > deadline:
                    raise
                time.sleep(1)
        print('  预热完成: 两服 world 就绪')

        print('== 1. 正常状态: 两服都可见 ==')
        c, r = auth('statuser1')
        shown = [s['server'] for s in r['servers']]
        assert sorted(shown) == ['1001', '1002'], shown
        print(f'  auth 列表: {json.dumps(r["servers"], ensure_ascii=False)}')

        print('== 2. 维护(0): 列表隐藏 + 绕过列表强进被拒 ==')
        before = register_count('1002')
        set_server('1002', status=0)
        c2, r2 = auth('statuser2')
        assert [s['server'] for s in r2['servers']] == ['1001'], r2['servers']
        print('  auth 列表只剩 1001 ✓')
        expect_unavailable(c2, '1002', '维护(status=0)')
        assert register_count('1002') == before, 'register_count 不应虚增'
        print('  register_count 未变化 ✓')
        c2.close()

        print('== 3. 备用(4): 列表隐藏 + 强进被拒 ==')
        set_server('1002', status=4)
        c3, r3 = auth('statuser3')
        assert [s['server'] for s in r3['servers']] == ['1001'], r3['servers']
        expect_unavailable(c3, '1002', '备用(status=4)')
        c3.close()

        print('== 4. 未到开服时间: 列表隐藏 + 强进被拒 ==')
        set_server('1002', status=1,
                   start_time=datetime(2999, 1, 1, tzinfo=timezone.utc))
        c4, r4 = auth('statuser4')
        assert [s['server'] for s in r4['servers']] == ['1001'], r4['servers']
        expect_unavailable(c4, '1002', '未开服(start_time 未来)')
        c4.close()

        print('== 5. 恢复正常(1): 重新可见且能进 ==')
        set_server('1002', status=1,
                   start_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
        c5, r5 = auth('statuser5')
        assert [s['server'] for s in r5['servers']] == ['1001', '1002'], r5['servers']
        r5 = c5.call('login.enterServer', {'server': '1002'})
        assert r5['ok'], r5
        print(f'  enterServer 1002 -> ok, gameAddr={r5["gameAddr"]} ✓')

        print('== 6. 爆满(3): 列表可见，新注册拒入(server busy)，老玩家可回 ==')
        set_server('1002', status=3)
        before = register_count('1002')
        c6, r6 = auth('statuser6')
        assert [s['server'] for s in r6['servers']] == ['1001', '1002'], r6['servers']
        print('  auth 列表仍含 1002（可见）✓')
        r6 = c6.call('login.enterServer', {'server': '1002'})
        assert not r6.get('ok'), r6
        assert r6.get('error') == 'server busy', r6
        print('  新注册 enterServer 1002 -> server busy ✓')
        assert register_count('1002') == before, 'register_count 不应虚增'
        print('  register_count 未变化 ✓')
        c6.close()
        # statuser5 在第 5 步已注册进 1002，爆满不影响回服
        r5 = c5.call('login.enterServer', {'server': '1002'})
        assert r5['ok'], r5
        print('  老玩家回服 enterServer 1002 -> ok ✓')
        set_server('1002', status=1)
        c5.close()
        c.close()

        print()
        print('########## 状态拦截测试通过 ##########')
    finally:
        for proc in procs:
            # /T 杀整棵进程树，避免 multiprocessing 子进程变孤儿残留
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True)
        for fp in log_files:
            fp.close()


if __name__ == '__main__':
    main()
