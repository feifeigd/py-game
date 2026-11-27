# coding:utf-8
"""
多角色专项测试（合服后的账号多角色共存模型）:
  1. 新账号进服: 0 角色，enterServer 返回空角色列表（不直发令牌）；
     login.createRole 自选名字/性别创角后拿令牌进游戏
  2. 创角校验: 重名(name taken) / 非法性别(invalid sex) / 超长名字被拒；
     再建第二个角色成功
  3. 再次 enterServer: 返回两个角色的列表，不再直发令牌
  4. 两个角色同时在线互不顶号（同账号不同角色是独立的"两个人"）
  5. 聊天跑马灯展示角色名
  6. 充值: 多角色账号不带 role 被拒（role required）；带 role 精准发货，
     另一个角色不受影响
  7. enterRole 盗用别人 role_id 被拒（role not found）；
     别的账号创角也不能用已被占用的名字（名字本服唯一，与账号无关）
  8. 顶号策略可配: 1002 配置 singleRoleOnline（单角色在线），
     同账号进新角色时旧角色被踢下线（sys.kick）；1001 不配则双开合法（第 4 步）
  9. 角色数上限: 单账号单服最多 maxRolesPerAccount（默认 4）个角色，超限被拒
  10. 老玩家档迁移: 无 role/sex 字段的历史档自动补默认值，老账号正常进游戏（预热步）

用法（工作区根目录）: uv run python tools/test_multi_role.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, ROOT)
sys.path.insert(1, os.path.join(ROOT, 'tools'))

from test_client import GameClient, http_get, pick_role  # noqa: E402
from test_all import GROUPS, WAIT_PORTS, wait_port  # noqa: E402
from test_server_status import DSN  # noqa: E402


def role_count(show_id):
    async def _run():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        try:
            return await conn.fetchval(
                'SELECT role_count FROM servers WHERE show_id = $1', show_id)
        finally:
            await conn.close()

    return asyncio.run(_run())

TS = int(time.time())
ACCOUNT = f'rolemaster{TS}'   # 每跑一次用新账号，保证幂等
THIEF = f'rolethief{TS}'
NAME_A = f'狂刀{TS}'          # 角色名本服唯一，带时间戳保证可重跑
NAME_B = f'寒剑{TS}'


def auth(account):
    c = GameClient().connect('127.0.0.1:11100')
    r = c.call('login.auth', {'account': account, 'password': '123456'})
    assert r['ok'], r
    return c, r


def enter_server(client, server):
    r = client.call('login.enterServer', {'server': server})
    assert r['ok'], r
    return r


def create_role(client, server, name, sex):
    return client.call('login.createRole',
                       {'server': server, 'name': name, 'sex': sex})


def enter_role(client, server, role):
    return client.call('login.enterRole', {'server': server, 'role': role})


def enter_game(account, rsp):
    """拿 createRole/enterRole 的令牌进游戏，返回 (连接, 角色信息)"""
    g = GameClient().connect(rsp['gameAddr'])
    r = g.call('game.enter', {'account': account,
                              'role': rsp.get('role', ''),
                              'token': rsp['token']})
    assert r['ok'], r
    return g, r['player']


def pay(account, count=100, role='', server=None):
    """模拟支付回调: 默认不带 server（带 role 时走 login 重定向解析现居服）"""
    url = (f'http://127.0.0.1:41100/pay?account={account}'
           f'&item=diamond&count={count}&role={role}')
    if server:
        url += f'&server={server}'
    return json.loads(http_get(url))


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
        time.sleep(5)  # 等组网、app load 与老档迁移完成

        # 预热: 等两服 world 就绪（顺带验证老档迁移后 tester 能正常进）
        deadline = time.time() + 60
        while True:
            try:
                wc, _ = auth('tester')
                wr = wc.call('login.enterServer', {'server': '1001'})
                assert wr['ok'], wr
                wr = pick_role(wc, 'tester', '1001', wr)
                wg, wplayer = enter_game('tester', wr)
                assert wplayer['role'], wplayer   # 迁移后老档必有 role
                assert wplayer['sex'] in (1, 2), wplayer   # 迁移后老档补了默认性别
                print(f'  预热完成: 老账号 tester 迁移后正常进服 role={wplayer["role"]} '
                      f'sex={wplayer["sex"]} diamond={wplayer["diamond"]}（老档迁移 ✓）')
                wc.close()
                wg.close()
                break
            except (AssertionError, RuntimeError, TimeoutError):
                if time.time() > deadline:
                    raise
                time.sleep(1)

        lc, _ = auth(ACCOUNT)
        rc_before = role_count('1001')   # role_count 增量断言基线

        print('== 1. 新账号进服: 0 角色返回空列表，createRole 自选名字/性别创角 ==')
        r = enter_server(lc, '1001')
        assert r.get('roles') == [], r   # 0 角色不再直发令牌
        assert not r.get('token'), r
        r = create_role(lc, '1001', NAME_A, 1)
        assert r['ok'] and r.get('token'), r
        g1, p1 = enter_game(ACCOUNT, r)
        role_a = p1['role']
        assert role_a.startswith('1001_'), p1   # role_id = {real_id}_{objectid}
        assert p1['name'] == NAME_A, p1
        assert p1['sex'] == 1, p1
        assert p1['diamond'] == 0, p1
        print(f'  角色A创角: {p1["name"]} sex=男 role={role_a} ✓')

        print('== 2. 创角校验: 重名/非法性别/超长名字被拒；再建第二角色 ==')
        r = create_role(lc, '1001', NAME_A, 2)
        assert not r.get('ok') and r.get('error') == 'name taken', r
        print('  重名 -> name taken ✓')
        r = create_role(lc, '1001', NAME_B, 0)
        assert not r.get('ok') and r.get('error') == 'invalid sex', r
        print('  非法性别 -> invalid sex ✓')
        r = create_role(lc, '1001', '一二三四五六七八九十百千万', 1)
        assert not r.get('ok'), r
        print(f'  超长名字 -> {r.get("error")} ✓')
        r = create_role(lc, '1001', NAME_B, 2)
        assert r['ok'] and r.get('token'), r
        g2, p2 = enter_game(ACCOUNT, r)
        role_b = p2['role']
        assert role_b != role_a
        assert p2['name'] == NAME_B and p2['sex'] == 2, p2
        print(f'  角色B创角: {p2["name"]} sex=女 role={role_b} ✓')

        print('== 3. 再次 enterServer: 返回角色列表，不再直发令牌 ==')
        r = enter_server(lc, '1001')
        assert r.get('roles'), r
        roles = {x['role']: x for x in r['roles']}
        assert set(roles) == {role_a, role_b}, roles
        assert roles[role_a]['sex'] == 1 and roles[role_b]['sex'] == 2, roles
        # 按最近登录倒序：B 最后进游戏排第一（角色面板默认选中上次玩的）
        assert r['roles'][0]['role'] == role_b, r['roles']
        assert r['roles'][0]['login_time'] >= r['roles'][1]['login_time']
        print(f'  roles -> {[(x["name"], x["sex"], x["level"]) for x in r["roles"]]} '
              f'（最近登录 {roles[role_b]["name"]} 排第一 ✓）')

        print('== 4. 两个角色同时在线互不顶号 ==')
        assert g1.call('player.getInfo', {})['role'] == role_a
        assert g2.call('player.getInfo', {})['role'] == role_b
        print('  角色A/B 同时在线 getInfo 正常 ✓')

        print('== 5. 聊天跑马灯展示角色名 ==')
        g1.call('player.chat', {'text': '大家好'})
        time.sleep(1)
        pushes = [p for p in g2.pushes if p.get('key') == 'chat.push']
        assert pushes, g2.pushes
        data = pushes[-1]['data']
        assert data['name'] == NAME_A, data
        print(f'  角色B收到跑马灯: {data["name"]}: {data["text"]} ✓')

        print('== 6. 多角色账号充值: 不带 role 拒收，带 role 精准发货 ==')
        r = pay(ACCOUNT)
        assert r['ret'] == 1 and r['msg'] == 'role required', r
        print('  不带 role -> role required ✓')
        d_a = g1.call('player.getInfo', {})['diamond']
        # 不带 server：recharge 解析 role 前缀，走 login 重定向解析现居服
        r = pay(ACCOUNT, role=role_a)
        assert r['ret'] == 0, r
        time.sleep(1)
        assert g1.call('player.getInfo', {})['diamond'] == d_a + 100
        assert g2.call('player.getInfo', {})['diamond'] == 0
        print('  带 role=A（不带 server，走重定向解析）-> A +100，B 不变 ✓')
        # 显式带 server 的老调用方式兼容
        r = pay(ACCOUNT, role=role_a, server='1001')
        assert r['ret'] == 0, r
        print('  显式带 server=1001 -> 兼容 ✓')

        print('== 7. 盗用 role_id / 占用别人角色名被拒 ==')
        tc, _ = auth(THIEF)
        enter_server(tc, '1001')   # 先注册进服
        r = enter_role(tc, '1001', role_a)
        assert not r.get('ok'), r
        assert r.get('error') == 'role not found', r
        print('  盗用 role -> role not found ✓')
        r = create_role(tc, '1001', NAME_A, 1)
        assert not r.get('ok') and r.get('error') == 'name taken', r
        print('  占用别人角色名 -> name taken ✓')
        tc.close()

        print('== 8. 顶号策略: 1002 配置 singleRoleOnline，进新角色顶掉旧角色 ==')
        POLICY = f'rolepolicy{TS}'
        pc, _ = auth(POLICY)
        enter_server(pc, '1002')
        r = create_role(pc, '1002', f'独尊{TS}', 1)
        assert r['ok'], r
        g3, p3 = enter_game(POLICY, r)
        assert p3['role'].startswith('1002_'), p3   # 服号前缀 = 出生服
        print(f'  角色A进 1002: {p3["name"]} role={p3["role"]}')
        r = create_role(pc, '1002', f'无双{TS}', 2)
        assert r['ok'], r
        g4, p4 = enter_game(POLICY, r)
        time.sleep(1)
        kicks = [p for p in g3.pushes if p.get('key') == 'sys.kick']
        assert kicks, g3.pushes
        print(f'  B 进游戏后 A 收到 sys.kick: {kicks[-1]["data"]} ✓（一次只能玩一个角色）')
        # B 不受影响正常在线
        assert g4.call('player.getInfo', {})['role'] == p4['role']
        print('  B 在线不受影响 ✓')
        g3.close()
        g4.close()
        pc.close()

        print('== 9. 角色数上限: 单账号单服最多 4 个角色 ==')
        r = create_role(lc, '1001', f'第三{TS}', 1)
        assert r['ok'], r
        r = create_role(lc, '1001', f'第四{TS}', 2)
        assert r['ok'], r
        print('  第 3/4 个角色创角成功（A/B + 2 = 4 个到顶）✓')
        r = create_role(lc, '1001', f'多余{TS}', 1)
        assert not r.get('ok'), r
        assert 'role limit' in r.get('error', ''), r
        print(f'  第 5 个角色 -> {r.get("error")} ✓')
        # role_count 创建角色数: 本账号建了 A/B/第三/第四 4 个，被拒的不计
        assert role_count('1001') == rc_before + 4, role_count('1001')
        print(f'  role_count 增量 +4（被拒的未计入）✓')

        g1.close()
        g2.close()
        lc.close()
        print()
        print('########## 多角色测试通过 ##########')
    finally:
        for proc in procs:
            # /T 杀整棵进程树，避免 multiprocessing 子进程变孤儿残留
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True)
        for fp in log_files:
            fp.close()


if __name__ == '__main__':
    main()
