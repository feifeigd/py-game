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

export interface ServerInfo {
    server: string;    // 展示服号，如 "1001"
    name: string;      // 展示名，如 "一区·风起云涌"
    status: number;    // 服状态：0=维护 1=良好 2=拥挤 3=爆满 4=备用（0/4 服务端不下发）
    recommend: boolean; // 是否推荐服
}

export interface RoleInfo {
    role: string;    // 角色ID
    name: string;    // 角色名
    sex: number;     // 性别：1=男 2=女
    level: number;   // 等级
}

export interface LoginAuthRsp {
    ok: boolean;
    error?: string;
    account: string;
    servers: ServerInfo[];   // 可选服务器列表，玩家选服后调 login.enterServer
    // 只有一个服时服务端直接顺路完成选服（免去 login.enterServer），
    // 统一返回 roles 角色列表（0/1/多个），客户端进角色面板
    server?: string;
    roles?: RoleInfo[];
}

// ------------------------- 选服 -------------------------
export interface EnterServerReq {
    server: string;
}

export interface EnterServerRsp {
    ok: boolean;
    error?: string;
    account: string;
    server: string;
    roles: RoleInfo[];   // 角色列表（0/1/多个），进角色面板选角或创角
}

// ------------------------- 创角（没有角色时） -------------------------
export interface CreateRoleReq {
    server: string;
    name: string;    // 角色名（本服唯一，1-12 字）
    sex: number;     // 1=男 2=女
}

export interface CreateRoleRsp {
    ok: boolean;
    error?: string;
    account: string;
    server: string;
    role: string;
    token: string;
    gameAddr: string;
    gameWsAddr: string;
}

// ------------------------- 选角（多角色时） -------------------------
export interface EnterRoleReq {
    server: string;
    role: string;    // 已有角色ID
}

export interface EnterRoleRsp {
    ok: boolean;
    error?: string;
    account: string;
    server: string;
    role: string;
    token: string;
    gameAddr: string;
    gameWsAddr: string;
}

// ------------------------- 进入游戏 -------------------------
export interface GameEnterReq {
    account: string;
    role: string;    // 角色ID（创角/选角时由服务端发给客户端）
    token: string;
}

export interface PlayerInfo {
    account: string;
    role: string;
    name: string;
    sex: number;     // 1=男 2=女
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
    name: string;   // 角色名
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
