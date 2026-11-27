using System;
using System.Collections.Generic;

/// <summary>
/// 通信协议：与服务端 engine/net/protocol.py 一致。
/// 线上帧 = 4 字节大端长度 + UTF8 JSON。
///
/// 外层包固定为 Packet；业务数据单独序列化成 JSON 字符串放在 data 字段里
/// （兼容 Unity 内置 JsonUtility，无需引入第三方 JSON 库）。
/// </summary>
[Serializable]
public class Packet
{
    public string key;
    public int seq;        // 0 = 服务器主动推送
    public string data;    // 业务数据 JSON 字符串
    public string error;   // 出错时非空
}

// ------------------------- 登录 -------------------------
[Serializable]
public class LoginAuthReq
{
    public string account;
    public string password;
}

[Serializable]
public class ServerInfo
{
    public string server;  // 展示服号，如 "1001"
    public string name;    // 展示名，如 "一区·风起云涌"
    public int status;     // 服状态：0=维护 1=良好 2=拥挤 3=爆满 4=备用（0/4 服务端不下发）
    public bool recommend; // 是否推荐服
}

[Serializable]
public class RoleInfo
{
    public string role;    // 角色ID
    public string name;    // 角色名
    public int sex;        // 性别：1=男 2=女
    public int level;      // 等级
}

[Serializable]
public class LoginAuthRsp
{
    public bool ok;
    public string error;
    public string account;
    public List<ServerInfo> servers;   // 可选服务器列表，玩家选服后调 login.enterServer
    // 只有一个服时服务端直接顺路完成选服（免去 login.enterServer），
    // 统一返回 roles 角色列表（0/1/多个），客户端进角色面板
    public string server;
    public List<RoleInfo> roles;
}

// ------------------------- 选服 -------------------------
[Serializable]
public class EnterServerReq
{
    public string server;
}

[Serializable]
public class EnterServerRsp
{
    public bool ok;
    public string error;
    public string account;
    public string server;
    public List<RoleInfo> roles;   // 角色列表（0/1/多个），进角色面板选角或创角
}

// ------------------------- 创角（没有角色时） -------------------------
[Serializable]
public class CreateRoleReq
{
    public string server;
    public string name;   // 角色名（本服唯一，1-12 字）
    public int sex;       // 1=男 2=女
}

[Serializable]
public class CreateRoleRsp
{
    public bool ok;
    public string error;
    public string account;
    public string server;
    public string role;
    public string token;
    public string gameAddr;
}

// ------------------------- 选角（多角色时） -------------------------
[Serializable]
public class EnterRoleReq
{
    public string server;
    public string role;   // 已有角色ID
}

[Serializable]
public class EnterRoleRsp
{
    public bool ok;
    public string error;
    public string account;
    public string server;
    public string role;
    public string token;
    public string gameAddr;
}

// ------------------------- 进入游戏 -------------------------
[Serializable]
public class GameEnterReq
{
    public string account;
    public string role;   // 角色ID（创角/选角时由服务端发给客户端）
    public string token;
}

[Serializable]
public class PlayerInfo
{
    public string account;
    public string role;
    public string name;
    public int sex;   // 1=男 2=女
    public int level;
    public int exp;
    public int diamond;
    public string service;
}

[Serializable]
public class GameEnterRsp
{
    public bool ok;
    public string error;
    public PlayerInfo player;
}

// ------------------------- 聊天 -------------------------
[Serializable]
public class ChatReq
{
    public string text;
}

[Serializable]
public class OkRsp
{
    public bool ok;
    public string error;
}

[Serializable]
public class ChatPush
{
    public string name;   // 角色名
    public string text;
}

// ------------------------- 充值推送 -------------------------
[Serializable]
public class RechargePush
{
    public string item;
    public int count;
    public int diamond;
}

[Serializable]
public class KickPush
{
    public string reason;
}

[Serializable]
public class LevelUpPush
{
    public int level;
}
