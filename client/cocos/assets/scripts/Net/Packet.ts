/**
 * 通信协议：与服务端 engine/net 一致。
 * 传输层为 WebSocket 文本帧，帧内容是一个 JSON 序列化的 Packet；
 * 业务数据单独 JSON.stringify 成字符串放在 data 字段里。
 */

export interface Packet {
    key: string;
    seq?: number;      // >0 = 请求/响应配对；无 seq = 服务器推送
    data?: string;     // 业务数据 JSON 字符串
    error?: string;    // 出错时非空
}

// ------------------------- 登录 -------------------------
export interface LoginAuthReq {
    account: string;
    password: string;
}

export interface ZoneInfo {
    zone: string;    // 大区号，如 "1001"
    name: string;    // 展示名，如 "一区·风起云涌"
}

export interface LoginAuthRsp {
    ok: boolean;
    error?: string;
    account: string;
    zones: ZoneInfo[];   // 可选大区列表，玩家选区后调 login.enterZone
    // 只有一个大区时服务端直接发令牌，客户端免去 login.enterZone
    zone?: string;
    token?: string;
    gameAddr?: string;
    gameWsAddr?: string;
}

// ------------------------- 选区 -------------------------
export interface EnterZoneReq {
    zone: string;
}

export interface EnterZoneRsp {
    ok: boolean;
    error?: string;
    account: string;
    zone: string;
    token: string;
    gameAddr: string;    // game 进程 TCP 地址（Unity 等原生客户端用）
    gameWsAddr: string;  // game 进程 WebSocket 地址（本客户端用）
}

// ------------------------- 进入游戏 -------------------------
export interface GameEnterReq {
    account: string;
    token: string;
}

export interface PlayerInfo {
    account: string;
    name: string;
    level: number;
    exp: number;
    diamond: number;
    service: string;
}

export interface GameEnterRsp {
    ok: boolean;
    error?: string;
    player: PlayerInfo;
}

// ------------------------- 聊天 -------------------------
export interface ChatReq {
    text: string;
}

export interface OkRsp {
    ok: boolean;
    error?: string;
}

export interface ChatPush {
    account: string;
    text: string;
}

// ------------------------- 推送 -------------------------
export interface RechargePush {
    item: string;
    count: number;
    diamond: number;
}

export interface KickPush {
    reason: string;
}

export interface LevelUpPush {
    level: number;
}
