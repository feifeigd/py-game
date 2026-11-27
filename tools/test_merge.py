# coding:utf-8
"""真合并迁移全流程测试（merge_server.py）:

  - 在 1002 服造 2 个角色（含与 1001 撞名的角色），在线充值一笔确认数据落库
  - 跑 tools/merge_server.py 1002 -> 1001
  - 验证: 角色档迁入 1001（撞名角色改名）、role_id 不变、servers 行变重定向、
    account_servers 改写、register_count 重算
  - 验证: 老玩家从 1001 进服看到全部角色；充值不带 server 参数时
    resolveServer 沿重定向链把 1002 前缀的 role_id 解析到 1001 精准发货
  - 结束后还原 mongo/PG，测试可重复跑

用法（工作区根目录）: uv run python tools/test_merge.py
"""
import asyncio
import os
import subprocess
import sys
import time

from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, ROOT)
sys.path.insert(1, os.path.join(ROOT, 'tools'))

from merge_server import load_game_mongo  # noqa: E402
from test_all import GROUPS, WAIT_PORTS, wait_port  # noqa: E402
from test_client import GameClient, http_get, pick_role  # noqa: E402
from test_server_status import DSN  # noqa: E402
from app.const import ServerStatus  # noqa: E402  # login/ 已随 test_server_status 入 path

SRC, DST = '1002', '1001'


def pg_run(sql, *args, fetch=False):
    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            if fetch:
                return await conn.fetch(sql, *args)
            return await conn.execute(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(_run())


def snapshot_pg():
    accounts = pg_run(
        'SELECT account, real_id, created_at FROM account_servers', fetch=True)
    servers = pg_run(
        'SELECT show_id, real_id, status, register_count, role_count '
        "FROM servers WHERE show_id IN ('1001', '1002')", fetch=True)
    return accounts, servers


def restore_pg(accounts, servers):
    pg_run('DELETE FROM account_servers')
    for r in accounts:
        pg_run('''
            INSERT INTO account_servers (account, real_id, created_at)
            VALUES ($1, $2, $3)''', r['account'], r['real_id'], r['created_at'])
    for r in servers:
        pg_run('''
            UPDATE servers SET real_id = $2, status = $3,
                register_count = $4, role_count = $5
            WHERE show_id = $1''',
            r['show_id'], r['real_id'], r['status'],
            r['register_count'], r['role_count'])


def mongo_coll(real_id):
    url, db_name, _ = load_game_mongo(real_id)
    return MongoClient(url)[db_name]['player']


def enter_1001(account, role=None):
    """登录 -> 选 1001 -> 选角（默认第一个，可指定 role）-> 进 game"""
    lc = GameClient().connect('127.0.0.1:11100')
    r = lc.call('login.auth', {'account': account, 'password': '123456'})
    assert r['ok'], r
    r = lc.call('login.enterServer', {'server': DST})
    assert r['ok'], r
    if role is None:
        r = pick_role(lc, account, DST, r)
    else:
        r = lc.call('login.enterRole', {'server': DST, 'role': role})
        assert r['ok'], r
    lc.close()
    gc = GameClient().connect(r['gameAddr'])
    r2 = gc.call('game.enter', {
        'account': account, 'role': r['role'], 'token': r['token']})
    assert r2['ok'], r2
    return gc, r['role']


def main():
    procs, log_files = [], []
    pg_snapshot, src_docs = None, None
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
        time.sleep(5)

        # 预热两服 world 就绪
        deadline = time.time() + 60
        while True:
            try:
                wc = GameClient().connect('127.0.0.1:11100')
                wc.call('login.auth', {'account': 'warmup', 'password': '123456'})
                assert wc.call('login.enterServer', {'server': DST})['ok']
                assert wc.call('login.enterServer', {'server': SRC})['ok']
                wc.close()
                break
            except (AssertionError, RuntimeError, TimeoutError):
                if time.time() > deadline:
                    raise
                time.sleep(1)
        print('  预热完成: 两服 world 就绪')

        print('== 1. 造数据: 1002 两个角色（一个与 1001 撞名）+ 在线充值落库 ==')
        # 撞名预置: dstplayer 在 1001 有角色「重名侠」
        lc = GameClient().connect('127.0.0.1:11100')
        lc.call('login.auth', {'account': 'dstplayer', 'password': '123456'})
        r = lc.call('login.enterServer', {'server': DST})
        assert r['ok'], r
        if not any(x['name'] == '重名侠' for x in r['roles']):
            r = lc.call('login.createRole',
                        {'server': DST, 'name': '重名侠', 'sex': 1})
            assert r['ok'], r
        lc.close()
        # srcplayer 在 1002 两个角色，其中一个也叫「重名侠」
        lc = GameClient().connect('127.0.0.1:11100')
        lc.call('login.auth', {'account': 'srcplayer', 'password': '123456'})
        r = lc.call('login.enterServer', {'server': SRC})
        assert r['ok'], r
        names = [x['name'] for x in r['roles']]
        if '重名侠' not in names:
            r = lc.call('login.createRole',
                        {'server': SRC, 'name': '重名侠', 'sex': 1})
            assert r['ok'], r
        if len(names) + ('重名侠' not in names) < 2:
            r = lc.call('login.createRole',
                        {'server': SRC, 'name': '独行客', 'sex': 2})
            assert r['ok'], r
        lc.close()
        # 在线充值 77 钻落 mongo，验证合并后数据原样
        lc = GameClient().connect('127.0.0.1:11100')
        lc.call('login.auth', {'account': 'srcplayer', 'password': '123456'})
        r = lc.call('login.enterServer', {'server': SRC})
        assert r['ok'], r
        src_roles = {x['role'] for x in r['roles']}
        assert len(src_roles) == 2, r['roles']
        role1 = r['roles'][0]['role']
        conflict_role = next(x['role'] for x in r['roles']
                             if x['name'] == '重名侠')
        r = lc.call('login.enterRole', {'server': SRC, 'role': role1})
        assert r['ok'], r
        lc.close()
        gc = GameClient().connect(r['gameAddr'])
        r2 = gc.call('game.enter', {
            'account': 'srcplayer', 'role': role1, 'token': r['token']})
        assert r2['ok'], r2
        before_diamond = r2['player']['diamond']
        resp = http_get('http://127.0.0.1:41100/pay'
                        f'?account=srcplayer&role={role1}&item=diamond'
                        f'&count=77&server={SRC}')
        assert '"ret": 0' in resp, resp
        time.sleep(1)
        info = gc.call('player.getInfo', {})
        assert info['diamond'] == before_diamond + 77, info
        print(f'  充值落库: diamond {before_diamond} -> {info["diamond"]} ✓')
        gc.close()

        print('== 2. 合并前快照，跑 merge_server.py 1002 -> 1001 ==')
        pg_snapshot = snapshot_pg()
        src_docs = list(mongo_coll(SRC).find({}))
        dst_roles_before = {d['role'] for d in mongo_coll(DST).find(
            {}, {'role': 1})}
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, 'tools', 'merge_server.py'),
             SRC, DST, '--yes'],
            capture_output=True, timeout=60, cwd=ROOT)
        # 子进程按系统编码（Windows GBK）输出，按 utf-8 容错解码只为展示
        print((result.stdout or result.stderr).decode('utf-8', errors='replace'))
        assert result.returncode == 0, result.stderr

        print('== 3. 数据面: 角色档迁入，撞名改名，src 库清空 ==')
        dst_docs = {d['role']: d for d in mongo_coll(DST).find({})}
        src_roles_left = {d['role'] for d in mongo_coll(SRC).find(
            {}, {'role': 1})}
        migrated = set(dst_docs) - dst_roles_before
        assert len(migrated) == len(src_docs), (len(migrated), len(src_docs))
        assert not src_roles_left, src_roles_left
        # 我们的撞名种子「重名侠」必须被改名（其它测试遗留的跨服同名档
        # 也可能被改名，按 role 精确断言自己的）
        assert dst_docs[conflict_role]['name'] == '重名侠#1002', \
            dst_docs[conflict_role]
        print(f'  迁入 {len(migrated)} 份，撞名种子改名 '
              f'{dst_docs[conflict_role]["name"]}，src 库已清空 ✓')

        print('== 4. 表结构面: servers 重定向 + account_servers 改写，统计列冻结 ==')
        snap = {r['show_id']: r for r in pg_snapshot[1]}
        row = pg_run('SELECT real_id, status, register_count, role_count '
                     "FROM servers WHERE show_id = '1002'", fetch=True)[0]
        assert row['real_id'] == DST and row['status'] == int(ServerStatus.MERGED), \
            row
        # 被合服统计冻结: 保留历史数字（档案），合服不清零不重算
        assert row['register_count'] == snap['1002']['register_count'], row
        assert row['role_count'] == snap['1002']['role_count'], row
        print('  servers 1002 -> 重定向行，register/role_count 冻结为历史值 ✓')
        assert pg_run('''
            SELECT 1 FROM account_servers
            WHERE account = 'srcplayer' AND real_id = $1''', DST, fetch=True)
        assert not pg_run('''
            SELECT 1 FROM account_servers
            WHERE account = 'srcplayer' AND real_id = $1''', SRC, fetch=True)
        print('  account_servers 已改写 ✓')
        # dst 行统计不因迁入虚增：迁入不算新增
        dst_row = pg_run('SELECT register_count, role_count FROM servers '
                         "WHERE show_id = '1001'", fetch=True)[0]
        assert dst_row['register_count'] == snap['1001']['register_count'], dst_row
        assert dst_row['role_count'] == snap['1001']['role_count'], dst_row
        print('  dst 行 register/role_count 未因迁入虚增 ✓')

        print('== 4.1 合并后新增只计在发生行（dst 行）==')
        new_account = f'mergenew{int(time.time())}'
        lc = GameClient().connect('127.0.0.1:11100')
        lc.call('login.auth', {'account': new_account, 'password': '123456'})
        r = lc.call('login.enterServer', {'server': DST})
        assert r['ok'] and r.get('roles') == [], r
        r = lc.call('login.createRole',
                    {'server': DST, 'name': f'新人{int(time.time())}', 'sex': 1})
        assert r['ok'], r
        lc.close()
        dst_row = pg_run('SELECT register_count, role_count FROM servers '
                         "WHERE show_id = '1001'", fetch=True)[0]
        assert dst_row['register_count'] == snap['1001']['register_count'] + 1, \
            dst_row
        assert dst_row['role_count'] == snap['1001']['role_count'] + 1, dst_row
        row = pg_run('SELECT register_count, role_count FROM servers '
                     "WHERE show_id = '1002'", fetch=True)[0]
        assert row['register_count'] == snap['1002']['register_count'], row
        assert row['role_count'] == snap['1002']['role_count'], row
        print('  新注册/新创角计在 dst 行（各 +1），被合服保持冻结 ✓')

        print('== 5. 玩家面: 1002 入口保留；从老服入口进，自动落到 1001 ==')
        lc = GameClient().connect('127.0.0.1:11100')
        r = lc.call('login.auth', {'account': 'srcplayer', 'password': '123456'})
        assert SRC in [s['server'] for s in r['servers']], r['servers']
        print('  auth 列表仍含 1002（已合并入口保留）✓')
        # 老玩家从老服入口进服：路由到迁入服，角色原样可见
        r = lc.call('login.enterServer', {'server': SRC})
        assert r['ok'], r
        assert len(r['roles']) == 2, r['roles']
        assert {x['role'] for x in r['roles']} == src_roles, r['roles']
        print(f'  从 1002 进服 -> 路由到 1001，'
              f'{len(r["roles"])} 个角色 role_id 不变 ✓')
        # 选角发令牌也走老服入口：game 地址是迁入服 1001 的
        r = lc.call('login.enterRole', {'server': SRC, 'role': role1})
        assert r['ok'], r
        assert r['gameAddr'].startswith('127.0.0.1:211'), r
        print(f'  enterRole 走 1002 -> 发到 1001 的地址 {r["gameAddr"]} ✓')
        lc.close()
        gc, entered = enter_1001('srcplayer', role1)
        info = gc.call('player.getInfo', {})
        assert info['diamond'] == before_diamond + 77, info
        print(f'  迁入角色进游戏，diamond={info["diamond"]} 数据原样 ✓')

        print('== 6. 充值链路: 不带 server，resolveServer 沿重定向链发货 ==')
        resp = http_get('http://127.0.0.1:41100/pay'
                        f'?account=srcplayer&role={entered}'
                        f'&item=diamond&count=50')
        assert '"ret": 0' in resp, resp
        time.sleep(1)
        info = gc.call('player.getInfo', {})
        assert info['diamond'] == before_diamond + 77 + 50, info
        print(f'  role 前缀 1002 -> 解析到 1001 发货，'
              f'diamond={info["diamond"]} ✓')
        gc.close()

        print()
        print('########## 合并迁移测试通过 ##########')
    finally:
        # 还原数据，保证测试可重复跑（其它测试的存量数据也靠它）
        if pg_snapshot is not None and src_docs is not None:
            dst_coll = mongo_coll(DST)
            dst_coll.delete_many(
                {'role': {'$in': [d['role'] for d in src_docs]}})
            src_coll = mongo_coll(SRC)
            src_coll.delete_many(
                {'role': {'$in': [d['role'] for d in src_docs]}})
            src_coll.insert_many(src_docs)
            restore_pg(*pg_snapshot)
            print('  已还原 mongo / PG 到合并前状态')
        for proc in procs:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True)
        for fp in log_files:
            fp.close()


if __name__ == '__main__':
    main()
