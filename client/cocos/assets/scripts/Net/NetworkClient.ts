/**
 * 网络客户端：WebSocket 传输 + 与 Unity 端相同的包协议。
 * 纯 TS 实现（不依赖 cc），方便测试与复用。
 *
 * 用法:
 *   NetworkClient.I.connect('127.0.0.1:11200', () => {
 *       NetworkClient.I.call<LoginAuthRsp>('login.auth', {...}, rsp => {...});
 *   });
 *   NetworkClient.I.onPush = (key, json) => { ... };
 */
import { Packet } from './Packet';

interface PendingCall {
    onOk: (dataJson: string) => void;
    onErr?: (err: string) => void;
}

export class NetworkClient {
    private static _instance: NetworkClient = null as unknown as NetworkClient;
    public static get I(): NetworkClient {
        if (!NetworkClient._instance) NetworkClient._instance = new NetworkClient();
        return NetworkClient._instance;
    }

    /** 服务器推送回调: (key, dataJson) */
    public onPush: ((key: string, dataJson: string) => void) | null = null;
    /** 连接断开回调 */
    public onDisconnected: (() => void) | null = null;

    private _ws: WebSocket | null = null;
    private _seq = 0;
    private _pending = new Map<number, PendingCall>();

    public get isConnected(): boolean {
        return !!this._ws && this._ws.readyState === WebSocket.OPEN;
    }

    /**
     * 连接服务器。WebSocket 握手是异步的，必须在 onOpen 回调后再发请求。
     * addr 可写 '127.0.0.1:11200' 或完整 'ws://127.0.0.1:11200'
     */
    public connect(addr: string, onOpen?: () => void): void {
        this.close();
        const url = addr.startsWith('ws') ? addr : `ws://${addr}`;
        const ws = new WebSocket(url);
        this._ws = ws;

        ws.onopen = () => {
            if (ws !== this._ws) return;
            console.log(`[Net] connected ${url}`);
            onOpen && onOpen();
        };
        ws.onmessage = (ev: MessageEvent) => {
            if (ws !== this._ws) return;
            try {
                this._dispatch(JSON.parse(ev.data as string) as Packet);
            } catch (e) {
                console.warn('[Net] bad packet:', e, ev.data);
            }
        };
        ws.onclose = () => {
            // 旧连接的关闭事件不影响新连接（登录成功 -> 连 game 的重连场景）
            if (ws !== this._ws) return;
            this._ws = null;
            this._failAll('disconnected');
            this.onDisconnected && this.onDisconnected();
        };
        ws.onerror = (ev: Event) => {
            if (ws === this._ws) console.warn('[Net] error:', ev);
        };
    }

    public close(): void {
        const ws = this._ws;
        this._ws = null;
        if (ws) {
            ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
            try { ws.close(); } catch (e) { /* ignore */ }
        }
        this._failAll('closed');
    }

    /** 请求-响应调用 */
    public call<T>(key: string, data: object | null,
                   onOk: (rsp: T) => void, onErr?: (err: string) => void): void {
        if (!this.isConnected) {
            onErr && onErr('not connected');
            return;
        }
        const seq = ++this._seq;
        this._pending.set(seq, {
            onOk: json => {
                try {
                    onOk(JSON.parse(json) as T);
                } catch (e) {
                    console.warn(`[Net] decode ${key} rsp failed:`, e);
                    onErr && onErr('decode error');
                }
            },
            onErr,
        });
        const packet: Packet = { key, seq, data: JSON.stringify(data ?? {}) };
        this._ws!.send(JSON.stringify(packet));
    }

    private _dispatch(packet: Packet): void {
        if (packet.seq) {
            const call = this._pending.get(packet.seq);
            if (!call) return;
            this._pending.delete(packet.seq);
            if (packet.error) {
                call.onErr && call.onErr(packet.error);
            } else {
                call.onOk(packet.data ?? '{}');
            }
        } else {
            this.onPush && this.onPush(packet.key, packet.data ?? '{}');
        }
    }

    private _failAll(err: string): void {
        const pending = Array.from(this._pending.values());
        this._pending.clear();
        pending.forEach(c => c.onErr && c.onErr(err));
    }
}
