/**
 * 游戏主流程：登录(login 组) -> 进入游戏(game 组) -> 聊天/充值推送。
 * 对应服务端的完整登录链路:
 *   login.auth{account, password} -> LoginAuthRsp{servers}，玩家选服
 *   login.enterServer{server} -> 统一返回 roles 角色列表（0/1/多个），进角色面板
 *   login.createRole{server, name, sex} -> 自选名字/性别创角，发令牌
 *   login.enterRole{server, role} -> 选定已有角色，发令牌
 *   连接 gameWsAddr -> game.enter{account, role, token} -> GameEnterRsp{player}
 */
import { NetworkClient } from './Net/NetworkClient';
import {
    ChatPush, CreateRoleRsp, EnterRoleRsp, EnterServerRsp, GameEnterRsp, KickPush,
    LevelUpPush, LoginAuthRsp, OkRsp, PlayerInfo, RechargePush,
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
    public server = '';
    public role = '';
    public token = '';
    public player: PlayerInfo | null = null;
    public inGame = false;

    /** 推送处理表: key -> 处理方法。新增推送只需在这里登记一行 */
    private readonly _pushHandlers: Record<string, PushHandler> = {
        'chat.push': push<ChatPush>(p => {
            if (Bootstrap.I) Bootstrap.I.addChatLine(`${p.name}: ${p.text}`);
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

    /** 第一步：连 login 服验证账号，成功后展示服务器列表供玩家选择 */
    public login(account: string, password: string, onError?: (err: string) => void): void {
        NetworkClient.I.connect(this.loginAddr, () => {
            NetworkClient.I.call<LoginAuthRsp>('login.auth',
                { account, password },
                rsp => {
                    if (!rsp.ok) { onError && onError(rsp.error ?? 'login failed'); return; }
                    this.account = rsp.account;
                    console.log(`[Login] auth ok, servers=${rsp.servers.length}`);
                    if (rsp.roles) {
                        // 单服：服务端已顺路完成选服，直接进角色面板（选角/创角）
                        this.server = rsp.server!;
                        if (Bootstrap.I) Bootstrap.I.showRoleSelect(rsp.roles);
                        return;
                    }
                    if (Bootstrap.I) Bootstrap.I.showServerSelect(rsp.servers);
                },
                err => onError && onError(err));
        });
    }

    /** 第二步：玩家选定服务器，返回角色列表（0/1/多个），进角色面板 */
    public enterServer(server: string, onError?: (err: string) => void): void {
        NetworkClient.I.call<EnterServerRsp>('login.enterServer',
            { server },
            rsp => {
                if (!rsp.ok) { onError && onError(rsp.error ?? 'enter server failed'); return; }
                this.server = rsp.server;
                console.log(`[Login] server=${this.server}, roles=${rsp.roles.length}`);
                if (Bootstrap.I) Bootstrap.I.showRoleSelect(rsp.roles);
            },
            err => onError && onError(err));
    }

    /** 第三步（创角）：自选名字/性别创角，发令牌并返回 game 地址 */
    public createRole(name: string, sex: number, onError?: (err: string) => void): void {
        NetworkClient.I.call<CreateRoleRsp>('login.createRole',
            { server: this.server, name, sex },
            rsp => {
                if (!rsp.ok) { onError && onError(rsp.error ?? 'create role failed'); return; }
                this.role = rsp.role;
                this.token = rsp.token;
                console.log(`[Login] create role=${this.role} name=${name}, gameWsAddr=${rsp.gameWsAddr}`);
                this._enterGame(rsp.gameWsAddr, onError);
            },
            err => onError && onError(err));
    }

    /** 第三步（选角）：选定已有角色，发令牌并返回 game 地址 */
    public enterRole(role: string, onError?: (err: string) => void): void {
        NetworkClient.I.call<EnterRoleRsp>('login.enterRole',
            { server: this.server, role },
            rsp => {
                if (!rsp.ok) { onError && onError(rsp.error ?? 'enter role failed'); return; }
                this.role = rsp.role;
                this.token = rsp.token;
                console.log(`[Login] enter role=${this.role}, gameWsAddr=${rsp.gameWsAddr}`);
                this._enterGame(rsp.gameWsAddr, onError);
            },
            err => onError && onError(err));
    }

    /** 拿 token 连 game 进程，进入游戏 */
    private _enterGame(wsAddr: string, onError?: (err: string) => void): void {
        NetworkClient.I.connect(wsAddr, () => {
            NetworkClient.I.call<GameEnterRsp>('game.enter',
                { account: this.account, role: this.role, token: this.token },
                rsp => {
                    if (!rsp.ok) { onError && onError(rsp.error ?? 'enter failed'); return; }
                    this.player = rsp.player;
                    this.role = rsp.player.role;
                    this.inGame = true;
                    if (Bootstrap.I) {
                        Bootstrap.I.showHud();
                        Bootstrap.I.setStatus(
                            `进入游戏成功 [服务器${this.server} ${this.player.service}] ${this.player.name} Lv.${this.player.level}`);
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
        const url = `http://127.0.0.1:41100/pay?account=${this.account}&item=diamond&count=${count}&server=${this.server}&role=${this.role}`;
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
