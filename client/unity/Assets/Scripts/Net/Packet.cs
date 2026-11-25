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
public class ZoneInfo
{
    public string zone;    // 大区号，如 "1001"
    public string name;    // 展示名，如 "一区·风起云涌"
}

[Serializable]
public class LoginAuthRsp
{
    public bool ok;
    public string error;
    public string account;
    public List<ZoneInfo> zones;   // 可选大区列表，玩家选区后调 login.enterZone
}

// ------------------------- 选区 -------------------------
[Serializable]
public class EnterZoneReq
{
    public string zone;
}

[Serializable]
public class EnterZoneRsp
{
    public bool ok;
    public string error;
    public string account;
    public string zone;
    public string token;
    public string gameAddr;   // 该大区分配到的 game 进程地址 "host:port"
}

// ------------------------- 进入游戏 -------------------------
[Serializable]
public class GameEnterReq
{
    public string account;
    public string token;
}

[Serializable]
public class PlayerInfo
{
    public string account;
    public string name;
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
    public string account;
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
