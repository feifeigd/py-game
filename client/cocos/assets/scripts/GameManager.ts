/**
 * 游戏主流程：登录(login 组) -> 进入游戏(game 组) -> 聊天/充值推送。
 * 对应服务端的完整登录链路:
 *   login.auth{account, password} -> LoginAuthRsp{zones}，玩家选区
 *   login.enterZone{zone} -> EnterZoneRsp{token, gameWsAddr}
 *   连接 gameWsAddr -> game.enter{account, token} -> GameEnterRsp{player}
 */
import { NetworkClient } from './Net/NetworkClient';
import {
    ChatPush, EnterZoneRsp, GameEnterRsp, KickPush, LevelUpPush,
    LoginAuthRsp, OkRsp, PlayerInfo, RechargePush,
} from './Net/Packet';
import { Bootstrap } from './Bootstrap';

type PushHandler = (json: string) => void;

/** 把强类型处理方法包装成推送处理器：统一负责 JSON 解析与容错 */
function push<T>(handler: (p: T) => void): PushHandler {
    return json => {
        let data: T | null = null;
        try {
            data = JSON.parse(json) as T;
        } catch (e) {
            console.warn('[Push] 解析失败:', e, json);
            return;
        }
        if (data) handler(data);
    };
}

export class GameManager {
    private static _instance: GameManager = null as unknown as GameManager;
    public static get I(): GameManager {
        if (!GameManager._instance) GameManager._instance = new GameManager();
        return GameManager._instance;
    }

    /** login 组 WebSocket 地址（真机/局域网改成服务器 IP） */
    public loginAddr = '127.0.0.1:11200';

    public account = '';
    public zone = '';
    public token = '';
    public player: PlayerInfo | null = null;
    public inGame = false;

    /** 推送处理表: key -> 处理方法。新增推送只需在这里登记一行 */
    private readonly _pushHandlers: Record<string, PushHandler> = {
        'chat.push': push<ChatPush>(p => {
            if (Bootstrap.I) Bootstrap.I.addChatLine(`${p.account}: ${p.text}`);
        }),
        'recharge.push': push<RechargePush>(p => {
            if (Bootstrap.I) Bootstrap.I.addChatLine(`[充值到账] ${p.item} x${p.count}，当前钻石 ${p.diamond}`);
            GameManager.I.refreshInfo();
        }),
        'player.levelUp': push<LevelUpPush>(p => {
            if (Bootstrap.I) Bootstrap.I.addChatLine(`[升级] 升到 Lv.${p.level}`);
            GameManager.I.refreshInfo();
        }),
        'sys.kick': push<KickPush>(p => {
            if (Bootstrap.I) Bootstrap.I.setStatus('被踢下线: ' + p.reason);
            GameManager.I.inGame = false;
        }),
    };

    /** 由 Bootstrap.onLoad 调用一次 */
    public init(): void {
        NetworkClient.I.onPush = (key, json) => {
            const handler = this._pushHandlers[key];
            if (handler) handler(json);
            else console.log(`[Push] 未处理的推送 ${key}:`, json);
        };
        NetworkClient.I.onDisconnected = () => {
            this.inGame = false;
            if (Bootstrap.I) {
                Bootstrap.I.setStatus('与服务器断开连接');
                Bootstrap.I.showLogin();
            }
        };
    }

    /** 第一步：连 login 服验证账号，成功后展示大区列表供玩家选择 */
    public login(account: string, password: string, onError?: (err: string) => void): void {
        NetworkClient.I.connect(this.loginAddr, () => {
            NetworkClient.I.call<LoginAuthRsp>('login.auth',
                { account, password },
                rsp => {
                    if (!rsp.ok) { onError && onError(rsp.error ?? 'login failed'); return; }
                    this.account = rsp.account;
                    console.log(`[Login] auth ok, zones=${rsp.zones.length}`);
                    if (rsp.token) {
                        // 单大区：服务端已直发令牌，免选区直接进游戏
                        this.zone = rsp.zone!;
                        this.token = rsp.token;
                        console.log(`[Login] single zone=${this.zone}, gameWsAddr=${rsp.gameWsAddr}`);
                        this._enterGame(rsp.gameWsAddr!, onError);
                        return;
                    }
                    if (Bootstrap.I) Bootstrap.I.showZoneSelect(rsp.zones);
                },
                err => onError && onError(err));
        });
    }

    /** 第二步：玩家选定大区，发令牌并返回该大区的 game 地址 */
    public enterZone(zone: string, onError?: (err: string) => void): void {
        NetworkClient.I.call<EnterZoneRsp>('login.enterZone',
            { zone },
            rsp => {
                if (!rsp.ok) { onError && onError(rsp.error ?? 'enter zone failed'); return; }
                this.zone = rsp.zone;
                this.token = rsp.token;
                console.log(`[Login] enter zone=${this.zone}, gameWsAddr=${rsp.gameWsAddr}`);
                this._enterGame(rsp.gameWsAddr, onError);
            },
            err => onError && onError(err));
    }

    /** 第二步：拿 token 连 game 进程，进入游戏 */
    private _enterGame(wsAddr: string, onError?: (err: string) => void): void {
        NetworkClient.I.connect(wsAddr, () => {
            NetworkClient.I.call<GameEnterRsp>('game.enter',
                { account: this.account, token: this.token },
                rsp => {
                    if (!rsp.ok) { onError && onError(rsp.error ?? 'enter failed'); return; }
                    this.player = rsp.player;
                    this.inGame = true;
                    if (Bootstrap.I) {
                        Bootstrap.I.showHud();
                        Bootstrap.I.setStatus(
                            `进入游戏成功 [大区${this.zone} ${this.player.service}] ${this.player.name} Lv.${this.player.level}`);
                    }
                    this._refreshHud();
                },
                err => onError && onError(err));
        });
    }

    public sendChat(text: string): void {
        if (!this.inGame || !text) return;
        NetworkClient.I.call<OkRsp>('player.chat', { text },
            () => { },
            err => { if (Bootstrap.I) Bootstrap.I.setStatus('chat 失败: ' + err); });
    }

    public refreshInfo(): void {
        if (!this.inGame) return;
        NetworkClient.I.call<PlayerInfo>('player.getInfo', null, rsp => {
            this.player = rsp;
            this._refreshHud();
        });
    }

    /** 演示充值：直接调 recharge 组的 HTTP 回调（真实项目由支付平台发起） */
    public testRecharge(count: number): void {
        const url = `http://127.0.0.1:41100/pay?account=${this.account}&item=diamond&count=${count}&zone=${this.zone}`;
        fetch(url)
            .then(r => r.text())
            .then(t => console.log('[Recharge]', t))
            .catch(e => console.warn('[Recharge] failed:', e));
    }

    private _refreshHud(): void {
        if (this.player && Bootstrap.I) {
            const p = this.player;
            Bootstrap.I.setInfo(`${p.name}  Lv.${p.level}  EXP ${p.exp}  钻石 ${p.diamond}`);
        }
    }
}
