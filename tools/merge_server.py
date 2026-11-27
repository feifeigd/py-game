# coding:utf-8
"""真合并迁移: 把 src 服（被合并）数据迁进 dst 服（迁入服），数据库级合并

流程:
  1. mongo: src 服的 player 档逐份复制到 dst 服（role_id 前缀不同天然不冲突；
     角色名撞名时给迁入档改名，老玩家数据不丢）。默认复制后删除 src 档
     （--keep-src 可保留，用于演练）
  2. PG servers: src 行 real_id 改指 dst、status 置已合并(5)——变成重定向行，
     login.resolveServer 沿它把老 role_id 前缀解析到 dst；展示列表保留入口
     （老玩家仍看到自己的服名），进入自动路由到 dst。
     src 行的 register_count/role_count 保留历史数字（档案，不再变化）
  3. PG account_servers: src 服的注册行 real_id 改写到 dst（两边都注册过的
     账号按主键冲突去重）。两个统计列一律不动: 各行只计本行之后真实发生的
     新注册/新创角，迁入不算新增
  4. 合并后: 老玩家从 dst 展示口进服，角色原样可见；即使注册表没跟上，
     login 的合服兜底也会凭角色列表放行并愈合

前提:
  - 合并期间 src 服的 game 进程组应已停（保证数据不再写入）；本脚本不查活，请自觉
  - dst 服在线运行与否均可（玩家档按 role 从 mongo 现读，无缓存）

幂等: 已迁入的 role 跳过不重复复制；PG 部分可重入

用法（工作区根目录）:
    uv run python tools/merge_server.py <src_real_id> <dst_real_id> [--yes] [--keep-src]
"""
import argparse
import asyncio
import json
import os
import sys

from pymongo import MongoClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.join(ROOT, 'login'))

from app.const import ServerStatus  # noqa: E402

ROLE_NAME_MAX = 12  # 与 game/app/const.py 一致，改名截断用


def load_login_dsn():
    with open(os.path.join(ROOT, 'login', 'config.json'), encoding='utf-8') as fp:
        return json.load(fp)['db']['dsn']


def load_game_mongo(real_id):
    """按 serverId 找 game 目录下的配置文件，返回 (url, db_name)"""
    game_dir = os.path.join(ROOT, 'game')
    for fn in sorted(os.listdir(game_dir)):
        if not (fn.startswith('config') and fn.endswith('.json')):
            continue
        with open(os.path.join(game_dir, fn), encoding='utf-8') as fp:
            cfg = json.load(fp)
        if str(cfg.get('serverId')) == str(real_id):
            return cfg['db']['url'], cfg['db']['name'], fn
    raise RuntimeError(f'找不到 serverId={real_id} 的 game 配置文件')


def rename_for_conflict(name, src):
    """角色名撞名时给迁入档改名: 截断到长度上限内加 #服号 后缀"""
    suffix = f'#{src}'
    return name[:ROLE_NAME_MAX - len(suffix)] + suffix


def migrate_mongo(src_url, src_db_name, dst_url, dst_db_name, keep_src, src):
    """把 src 库的 player 档迁进 dst 库 @return (docs, renamed_count)"""
    src_coll = MongoClient(src_url)[src_db_name]['player']
    dst_coll = MongoClient(dst_url)[dst_db_name]['player']
    dst_roles = {d['role'] for d in dst_coll.find({}, {'role': 1})}
    dst_names = {d['name'] for d in dst_coll.find({}, {'name': 1})}
    docs, renamed = [], 0
    for doc in src_coll.find({}):
        doc.pop('_id', None)  # 主键就是 role 字段，_id 由目标库重新生成
        if doc['role'] in dst_roles:
            continue  # 幂等：已迁过
        if doc['name'] in dst_names:
            doc['name'] = rename_for_conflict(doc['name'], src)
            renamed += 1
        docs.append(doc)
    if docs:
        dst_coll.insert_many(docs)
        if not keep_src:
            src_coll.delete_many(
                {'role': {'$in': [d['role'] for d in docs]}})
    return docs, renamed


async def migrate_pg(dsn, src, dst):
    """servers 重定向 + account_servers 改写（一个事务）

    register_count / role_count 是各展示行的真实统计（只计本行之后发生的
    新注册/新创角），合服一律不改写: src 行保留历史数字作档案，
    dst 行也不因迁入而虚增。"""
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            src_row = await conn.fetchrow(
                'SELECT id, real_id, status FROM servers WHERE show_id = $1', src)
            if src_row is None:
                raise RuntimeError(f'servers 表没有 show_id={src} 的服')
            if src_row['real_id'] != src:
                raise RuntimeError(
                    f'show_id={src} 的 real_id 已是 {src_row["real_id"]}，'
                    f'该服已被合并/重定向过')
            if await conn.fetchval(
                    'SELECT 1 FROM servers WHERE real_id = $1 LIMIT 1', dst) is None:
                raise RuntimeError(f'迁入服 {dst} 在 servers 表里不存在')

            # 注册行改写：冲突（两边都注册过的账号）去重，然后删掉老行
            await conn.execute('''
                INSERT INTO account_servers (account, real_id, created_at)
                SELECT account, $2, created_at FROM account_servers
                WHERE real_id = $1 ON CONFLICT DO NOTHING''', src, dst)
            moved = await conn.fetchval(
                'SELECT count(*) FROM account_servers WHERE real_id = $1', src)
            await conn.execute(
                'DELETE FROM account_servers WHERE real_id = $1', src)

            # src 行变重定向行：已合并(MERGED)展示但进入路由到迁入服，
            # real_id 指向迁入服；
            # register_count/role_count 保留原值——历史统计不再变化
            await conn.execute('''
                UPDATE servers SET real_id = $2, status = $3
                WHERE show_id = $1''', src, dst, int(ServerStatus.MERGED))
            return moved
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description='真合并迁移: src 服并入 dst 服')
    parser.add_argument('src', help='被合并服（展示服号=出生服号）')
    parser.add_argument('dst', help='迁入服')
    parser.add_argument('--yes', action='store_true', help='跳过确认直接执行')
    parser.add_argument('--keep-src', action='store_true',
                        help='复制后保留 src 库 player 档（演练用）')
    args = parser.parse_args()
    src, dst = str(args.src), str(args.dst)
    if src == dst:
        raise SystemExit('src 与 dst 不能相同')

    src_url, src_db, src_cfg = load_game_mongo(src)
    dst_url, dst_db, dst_cfg = load_game_mongo(dst)
    print(f'合并计划: {src}({src_cfg}:{src_db}) -> {dst}({dst_cfg}:{dst_db})')
    print('  1) mongo player 档复制到 dst' + ('（保留 src 档）' if args.keep_src else '（复制后删除 src 档）'))
    print('  2) servers: src 行置已合并并指向 dst（重定向），统计列保留原值')
    print('  3) account_servers: 注册行改写到 dst（register/role_count 不动）')
    if not args.yes and input('确认执行？[y/N] ').strip().lower() != 'y':
        print('已取消')
        return

    docs, renamed = migrate_mongo(src_url, src_db, dst_url, dst_db,
                                  args.keep_src, src)
    print(f'mongo: 迁入 {len(docs)} 份角色档，其中改名 {renamed} 份')

    moved = asyncio.run(migrate_pg(load_login_dsn(), src, dst))
    print(f'PG: account_servers 改写 {moved} 行；servers 重定向完成，'
          f'两行 register_count/role_count 均保留原值')
    print(f'合并完成: {src} -> {dst}')


if __name__ == '__main__':
    main()
