"""验证 center 在线统计落库: 起 game + center 两组, 等一个上报周期(30s), 杀进程查 MySQL"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS = ['game', 'center']


def kill_tree(pid):
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                   capture_output=True)


def main():
    procs = []
    try:
        for group in GROUPS:
            log_fp = open(os.path.join(ROOT, 'run', f'{group}_report_test.log'), 'w')
            proc = subprocess.Popen(
                [sys.executable, 'start.py'],
                cwd=os.path.join(ROOT, group),
                stdout=log_fp, stderr=subprocess.STDOUT)
            procs.append(proc)
            print('started', group, proc.pid)

        print('waiting 40s for one report cycle...')
        time.sleep(40)
    finally:
        for proc in procs:
            kill_tree(proc.pid)

    # 查 MySQL
    import asyncio

    import aiomysql

    async def check():
        conn = await aiomysql.connect(
            host='127.0.0.1', port=3306, user='root',
            password='123456', db='pygame')
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute('SELECT * FROM online_stats')
            rows = await cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    rows = asyncio.run(check())
    print('online_stats:', rows)
    assert any(r['group_name'] == 'game' for r in rows), 'game report not persisted!'
    print('OK: center online_stats persisted')


if __name__ == '__main__':
    main()
