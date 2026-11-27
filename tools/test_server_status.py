# coding:utf-8
"""
选服状态/注册拦截专项测试:
  - 维护(0) / 备用(4) / 未到开服时间的服: 不出现在 login.auth 的服务器列表里，
    客户端绕过列表直接 login.enterServer 也一律返回 server unavailable
  - 爆满(3)的服: 列表照常展示，新注册 enterServer 返回 server busy，
    已注册老玩家回本服不受影响
  - 合服: 多个展示行（show_id 不同）指向同一 real_id，玩家从任一展示口进入
    都路由到同一 game 组；注册按 real_id 去重，计数落在进入的展示行上
  - 合服老玩家兜底: 角色档已迁入目标服但注册行还停在老 real_id 时
    （merge 脚本未跑/漏跑），爆满服也放行并愈合注册行（愈合计一次，不再重复）
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
sys.path.insert(1, os.path.join(ROOT, 'login'))

from app.const import ServerStatus  # noqa: E402
from test_client import GameClient, pick_role  # noqa: E402
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
                real_id, *[int(v) if isinstance(v, ServerStatus) else v
                           for v in fields.values()])
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


def register_count_by_show(show_id):
    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetchval(
                'SELECT register_count FROM servers WHERE show_id = $1', show_id)
        finally:
            await conn.close()

    return asyncio.run(_run())


def add_server(show_id, name, real_id, status=ServerStatus.GOOD):
    """模拟 GM 加服/合服行"""

    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute('''
                INSERT INTO servers (show_id, name, status, is_recommend,
                                     real_id, start_time)
                VALUES ($1, $2, $3, FALSE, $4, now())''',
                show_id, name, int(status), real_id)
        finally:
            await conn.close()

    asyncio.run(_run())


def delete_server(show_id):
    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute('DELETE FROM servers WHERE show_id = $1', show_id)
        finally:
            await conn.close()

    asyncio.run(_run())


def delete_registrations(*accounts):
    """清掉测试账号的进服登记，保证 register_count 断言不受历史数据影响"""

    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                'DELETE FROM account_servers WHERE account = ANY($1)', list(accounts))
        finally:
            await conn.close()

    asyncio.run(_run())


def fake_merge_registration(account, old_real):
    """模拟合服老玩家：注册行停留在老 real_id（目标服查不到）"""

    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                'DELETE FROM account_servers WHERE account = $1', account)
            await conn.execute('''
                INSERT INTO account_servers (account, real_id, created_at)
                VALUES ($1, $2, now())''', account, old_real)
        finally:
            await conn.close()

    asyncio.run(_run())


def has_registration(account, real_id):
    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetchval('''
                SELECT 1 FROM account_servers
                WHERE account = $1 AND real_id = $2''', account, real_id)
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

        # 复位受测服状态（上次中断可能留下脏状态），保证测试幂等
        set_server('1002', status=ServerStatus.GOOD,
                   start_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
        delete_server('9001')
        delete_registrations('warmup', 'merger', 'mergedold',
                             *(f'statuser{i}' for i in range(1, 7)))

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
        set_server('1002', status=ServerStatus.MAINTAIN)
        c2, r2 = auth('statuser2')
        assert [s['server'] for s in r2['servers']] == ['1001'], r2['servers']
        print('  auth 列表只剩 1001 ✓')
        expect_unavailable(c2, '1002', '维护(status=0)')
        assert register_count('1002') == before, 'register_count 不应虚增'
        print('  register_count 未变化 ✓')
        c2.close()

        print('== 3. 备用(4): 列表隐藏 + 强进被拒 ==')
        set_server('1002', status=ServerStatus.RESERVE)
        c3, r3 = auth('statuser3')
        assert [s['server'] for s in r3['servers']] == ['1001'], r3['servers']
        expect_unavailable(c3, '1002', '备用(status=4)')
        c3.close()

        print('== 4. 未到开服时间: 列表隐藏 + 强进被拒 ==')
        set_server('1002', status=ServerStatus.GOOD,
                   start_time=datetime(2999, 1, 1, tzinfo=timezone.utc))
        c4, r4 = auth('statuser4')
        assert [s['server'] for s in r4['servers']] == ['1001'], r4['servers']
        expect_unavailable(c4, '1002', '未开服(start_time 未来)')
        c4.close()

        print('== 5. 恢复正常(1): 重新可见且能进 ==')
        set_server('1002', status=ServerStatus.GOOD,
                   start_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
        c5, r5 = auth('statuser5')
        assert [s['server'] for s in r5['servers']] == ['1001', '1002'], r5['servers']
        r5 = c5.call('login.enterServer', {'server': '1002'})
        assert r5['ok'], r5
        r5 = pick_role(c5, 'statuser5', '1002', r5)   # 0 角色先创角才有令牌
        print(f'  enterServer 1002 -> ok, gameAddr={r5["gameAddr"]} ✓')

        print('== 6. 爆满(3): 列表可见，新注册拒入(server busy)，老玩家可回 ==')
        set_server('1002', status=ServerStatus.FULL)
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
        set_server('1002', status=ServerStatus.GOOD)
        c5.close()

        print('== 7. 合服: 多个展示行指向同一 real_id ==')
        add_server('9001', '合服·风起云涌', real_id='1001')
        c7, r7 = auth('merger')
        shown7 = [s['server'] for s in r7['servers']]
        assert '9001' in shown7, shown7
        print('  auth 列表含合服展示行 9001 ✓')
        cnt_1001 = register_count_by_show('1001')
        cnt_9001 = register_count_by_show('9001')
        # 从展示口 9001 进服: 路由到 real 1001 的 game 地址
        r7 = c7.call('login.enterServer', {'server': '9001'})
        assert r7['ok'], r7
        r7 = pick_role(c7, 'merger', '9001', r7)   # 0 角色先创角才有令牌
        assert r7['gameAddr'].startswith('127.0.0.1:211'), r7
        print(f'  enterServer 9001 -> real 1001 的地址 {r7["gameAddr"]} ✓')
        # 新注册计数落在进入的展示行上，1001 行不受影响
        assert register_count_by_show('9001') == cnt_9001 + 1
        assert register_count_by_show('1001') == cnt_1001
        print('  register_count 只计在 9001 行 ✓')
        # 同账号再从 1001 进: 按 real_id 已注册，直接放行且不再计数
        r7 = c7.call('login.enterServer', {'server': '1001'})
        assert r7['ok'], r7
        assert register_count_by_show('9001') == cnt_9001 + 1
        assert register_count_by_show('1001') == cnt_1001
        print('  同账号换展示口进 1001 -> 已注册放行，不重复计数 ✓')
        delete_server('9001')
        c7.close()

        print('== 8. 合服老玩家兜底: 注册行停在老服，爆满也放行并愈合 ==')
        # mergedold 在 1001 有角色（先正常注册创角），
        # 然后把注册行改成老 real_id 1002，模拟"迁了数据但注册表没跟上"
        c8, r8 = auth('mergedold')
        r8 = c8.call('login.enterServer', {'server': '1001'})
        assert r8['ok'], r8
        r8 = pick_role(c8, 'mergedold', '1001', r8)   # 确保有角色
        c8.close()
        fake_merge_registration('mergedold', '1002')
        assert has_registration('mergedold', '1001') is None
        # 目标服爆满：新注册进不来，但 mergedold 有角色 -> 老玩家放行
        set_server('1001', status=ServerStatus.FULL)
        before = register_count('1001')
        c8, r8 = auth('mergedold')
        r8 = c8.call('login.enterServer', {'server': '1001'})
        assert r8['ok'], r8
        assert r8['roles'], r8
        print('  爆满下合服老玩家 enterServer 1001 -> ok，角色列表带回 ✓')
        assert register_count('1001') == before + 1, '愈合应按一次注册计数'
        print('  register_count 愈合计一次 ✓')
        assert has_registration('mergedold', '1001') == 1
        # 再进一次：注册行已愈合，走快路径不再计数
        r8 = c8.call('login.enterServer', {'server': '1001'})
        assert r8['ok'], r8
        assert register_count('1001') == before + 1
        print('  再次进服走快路径，不重复计数 ✓')
        set_server('1001', status=ServerStatus.GOOD)
        c8.close()
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
