"""
recharge 组充值服务

对外暴露 HTTP 接口接收支付平台回调（演示用 GET/POST /pay），
到账后跨组调用 game 组全局进程，由 world 路由到玩家所在进程发货。

测试:
    curl "http://127.0.0.1:41100/pay?account=foo&item=diamond&count=100"
"""
from engine import api
from engine.context import CTX
from engine.core.handler import handler
from engine.util import log

GAME_CLUSTER = 'game'

# 热更新安全：保住充值记录
if '_orders' not in globals():
    _orders = []  # 演示用充值流水


async def pay(query, body):
    """支付回调入口: /pay?account=xx&item=diamond&count=100"""
    account = query.get('account', '')
    item = query.get('item', 'diamond')
    count = int(query.get('count', 0))
    if not account or count <= 0:
        return {'ret': 1, 'msg': 'bad params'}

    _orders.append({'account': account, 'item': item, 'count': count})
    log.notice('recharge order: %s %s x%s', account, item, count)

    # 跨组调用 game 组 -> world 路由到玩家所在进程 -> 玩家 actor 发货
    result = await api.call_cluster(GAME_CLUSTER, 'recharge.onRecharge',
                                    account, item, count)
    return {'ret': 0, 'msg': 'success', 'detail': result}


@handler('recharge.listOrders')
def list_orders():
    return list(_orders)


def init():
    # 把 HTTP 路由挂到本服务的 web server 上
    assert CTX.web_server is not None
    CTX.web_server.route('/pay')(pay)
    log.notice('recharge service inited, /pay ready')
