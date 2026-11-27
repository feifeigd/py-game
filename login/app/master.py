"""login 组 master 服务（组内协调 + 跨组转发 + 库表结构初始化）"""
from app.const import ServerStatus
from engine import db
from engine.context import CTX
from engine.core.handler import handler
from engine.util import log


async def init():
    # 库表结构由 master 统一初始化（每个进程组各进程都会连 db，
    # 但 DDL 只应由 master 执行一次，worker 服务只负责用）
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            account     TEXT PRIMARY KEY,
            login_count INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL,
            last_login  TIMESTAMPTZ NOT NULL
        )''')
    # 服务器表（滚服模式，每个 game 进程组 = 一个数据独立的服务器）:
    # show_id 展示给玩家看的服号（玩家按它选服，必须唯一）,
    # real_id 真实服号（对应 cluster 名 game1001 -> 1001，同时是 config servers 段的键；
    #   不唯一——合服时多个展示行指向同一个真实服，数据落在同一 game 组）,
    # status 服状态（枚举见 app/const.py 的 ServerStatus，0维护/4备用的不展示，
    #   5已合并仍展示——入口保留给老玩家，进入路由到迁入服）,
    # is_recommend 是否推荐服, start_time 开服时间（未到不展示）,
    # register_count 注册账号数 / role_count 创建角色数（两个口径分开统计，
    # 都是本行的真实新增：注册/创角发生时递增；合服不改写——被合服行保留
    # 历史数字作档案，迁入不算迁入服的新增）,
    # max_register 注册上限（按行统计，max_register=0 不限）
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id             BIGSERIAL PRIMARY KEY,
            show_id        TEXT        NOT NULL,
            name           TEXT        NOT NULL,
            status         INTEGER     NOT NULL DEFAULT 0,
            is_recommend   BOOLEAN     NOT NULL DEFAULT FALSE,
            real_id        TEXT        NOT NULL,
            start_time     TIMESTAMPTZ,
            register_count INTEGER     NOT NULL DEFAULT 0,
            max_register   INTEGER     NOT NULL DEFAULT 0,
            role_count     INTEGER     NOT NULL DEFAULT 0
        )''')
    # 老库迁移: 后加的列补到存量表上（CREATE IF NOT EXISTS 不会加列）
    await db.get().execute(
        'ALTER TABLE servers ADD COLUMN IF NOT EXISTS '
        'role_count INTEGER NOT NULL DEFAULT 0')
    # 老库迁移: real_id 误建的唯一索引去掉（合服需要同 real_id 多行），
    # 唯一性改到 show_id（玩家选服的标识）
    await db.get().execute('DROP INDEX IF EXISTS servers_real_id_uidx')
    await db.get().execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS servers_show_id_uidx ON servers (show_id)')
    # 账号进服登记: 一个账号在一个真实服只算一次注册（register_count 的事实来源；
    # 合服后从任一展示口进来都算同一个服）
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS account_servers (
            account    TEXT        NOT NULL,
            real_id    TEXT        NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (account, real_id)
        )''')
    # 种子: 把 config 静态配置里的服灌进表（该 real_id 已有任何行就跳过，
    # GM 改表 / 加合服行不会被覆盖）
    gdb = db.get()
    for real_id, cfg in sorted(CTX.config.get('servers', {}).items()):
        await gdb.execute('''
            INSERT INTO servers (show_id, name, status, is_recommend, real_id, start_time)
            SELECT $1, $2, $4, FALSE, $3, now()
            WHERE NOT EXISTS (SELECT 1 FROM servers WHERE real_id = $3)''',
            real_id, cfg.get('name', real_id), real_id,
            int(ServerStatus.GOOD))
    log.notice('login master inited')


def _server_of_cluster(group):
    """cluster 名 -> 服号: 'game1001' -> '1001'，'game'（无 serverId）-> 默认 1001"""
    if not group.startswith('game'):
        return None
    return group[len('game'):] or '1001'


@handler('sys.serviceUp')
def on_service_up(group, service, addrs):
    """game 服扩容通知 -> 转发给 login 服务更新对应服的分配池"""
    server = _server_of_cluster(group)
    if server is None:
        return
    log.notice(f'game service up: {group} {service} {addrs}')
    CTX.internal.send('login', 'login.serviceUp', server, service, addrs)


@handler('login.resolveServer')
async def resolve_server(birth_id):
    """recharge 组跨组调用（落在本组 master）：角色出生服 -> 现居服

    role_id 前缀是出生服 real_id；合服后老服行保留在 servers 表里
    （status=已合并(MERGED)，展示但进入路由到迁入服、行不删除），
    real_id 指向迁入服。
    多次合并形成重定向链（1002 -> 1001 -> 1003），逐跳解析到
    real_id 等于自身为止。
    跨组调用由 master 接收转发，handler 必须注册在 master 服务上。
    """
    cur = str(birth_id)
    gdb = db.get()
    for _ in range(10):
        row = await gdb.fetchrow(
            'SELECT real_id FROM servers WHERE show_id = $1', cur)
        if row is None:
            return None
        if row['real_id'] == cur:
            return cur
        cur = row['real_id']
    log.warning(f'resolveServer chain too long or cyclic: birth={birth_id}')
    return None


@handler('sys.serviceDown')
def on_service_down(group, service):
    """game 服缩容通知 -> 转发给 login 服务把节点移出对应服的分配池"""
    server = _server_of_cluster(group)
    if server is None:
        return
    log.notice(f'game service down: {group} {service}')
    CTX.internal.send('login', 'login.serviceDown', server, service)
