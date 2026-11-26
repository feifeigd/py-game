"""
recharge 组充值服务

对外暴露 HTTP 接口接收支付平台回调（演示用 GET/POST /pay），
到账后跨组调用 game 组全局进程，由 world 路由到玩家所在进程发货。

订单持久化（PostgreSQL，增量更新）:
  - 收到回调先落 orders 表（status=pending），订单是充值的事实来源
  - 发货成功后只更新 status / delivered_at 两列
  - 玩家不在线时订单保持 pending（由 game 组 world 挂离线补发）

测试:
    curl "http://127.0.0.1:41100/pay?account=foo&item=diamond&count=100"
"""
import uuid
from datetime import UTC, datetime

from engine import api, db
from engine.context import CTX
from engine.core.handler import handler
from engine.net.web import WebServer
from engine.util import log

GAME_CLUSTER_PREFIX = 'game'  # 服 cluster 名 = game<server>，如 game1001

DEFAULT_SERVER = '1001'


# 装饰器在模块顶层注册路由（此刻 CTX.web_server 已就绪，见启动顺序），
# 热更 reload 会重跑这里，新 pay 自动覆盖路由表里的旧函数
assert isinstance(CTX.web_server, WebServer)
@CTX.web_server.route('/pay')
async def pay(query, body):
    """支付回调入口: /pay?account=xx&item=diamond&count=100&server=1001"""
    account = query.get('account', '')
    item = query.get('item', 'diamond')
    count = int(query.get('count', 0))
    server = str(query.get('server') or DEFAULT_SERVER)
    if not account or count <= 0:
        return {'ret': 1, 'msg': 'bad params'}

    gdb = db.get()
    order_id = uuid.uuid4().hex
    await gdb.insert('orders', {
        'order_id': order_id,
        'account': account,
        'server': server,
        'item': item,
        'count': count,
        'status': 'pending',
        'created_at': datetime.now(UTC),
        'delivered_at': None,
    })
    log.notice('recharge order %s: %s server=%s %s x%s',
               order_id, account, server, item, count)

    # 跨组调用对应服的 world -> 路由到玩家所在进程 -> 玩家 actor 发货
    result = await api.call_cluster(f'{GAME_CLUSTER_PREFIX}{server}',
                                    'recharge.onRecharge',
                                    account, item, count)
    # 发货成功: 增量更新订单状态（玩家不在线则保持 pending 等补发）
    if result.get('ret') == 0 and 'delivered' in result.get('msg', ''):
        await gdb.update_columns('orders', 'order_id', order_id, {
            'status': 'delivered',
            'delivered_at': datetime.now(UTC),
        })
    return {'ret': 0, 'msg': 'success', 'orderId': order_id, 'detail': result}


@handler('recharge.listOrders')
async def list_orders():
    rows = await db.get().fetch(
        'SELECT order_id, account, server, item, count, status, created_at, delivered_at'
        ' FROM orders ORDER BY created_at DESC LIMIT 100')
    return [dict(r) for r in rows]


async def init():
    # 建表（路由 /pay 已在模块顶层用装饰器注册）
    await db.get().execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id     TEXT PRIMARY KEY,
            account      TEXT NOT NULL,
            item         TEXT NOT NULL,
            count        INTEGER NOT NULL,
            status       TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL,
            delivered_at TIMESTAMPTZ
        )''')
    # 老表迁移: zone 列改名为 server（CREATE TABLE IF NOT EXISTS 不会修改已有表）
    await db.get().execute('''
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'orders' AND column_name = 'zone') THEN
                ALTER TABLE orders RENAME COLUMN zone TO server;
            END IF;
        END $$''')
    # 老表补 server 列
    await db.get().execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS server TEXT NOT NULL DEFAULT '1001'")
    log.notice('recharge service inited, /pay ready')
