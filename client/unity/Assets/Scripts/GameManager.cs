using UnityEngine;

/// <summary>
/// 游戏主流程：登录(login 组) -> 进入游戏(game 组) -> 聊天/充值推送。
/// 对应服务端的完整登录链路:
///   login.auth{account, password} -> LoginAuthRsp{zones}，玩家选区
///   login.enterZone{zone} -> EnterZoneRsp{token, gameAddr}
///   连接 gameAddr -> game.enter{account, token} -> GameEnterRsp{player}
/// </summary>
public class GameManager : MonoBehaviour
{
    public static GameManager I { get; private set; }

    [Header("login 服务器地址")]
    public string loginHost = "127.0.0.1";
    public int loginPort = 11100;

    public string Account { get; private set; }
    public string Token { get; private set; }
    public string Zone { get; private set; }
    public PlayerInfo Player { get; private set; }
    public bool InGame { get; private set; }

    private void Awake()
    {
        if (I != null && I != this) { Destroy(gameObject); return; }
        I = this;
    }

    // 推送处理表: key -> 处理方法。新增推送只需在这里登记一行
    private System.Collections.Generic.Dictionary<string, System.Action<string>> _pushHandlers;

    private void Start()
    {
        _pushHandlers = new System.Collections.Generic.Dictionary<string, System.Action<string>>
        {
            { "chat.push", Push<ChatPush>(p =>
                Bootstrap.I.AddChatLine($"{p.account}: {p.text}")) },
            { "recharge.push", Push<RechargePush>(p =>
            {
                Bootstrap.I.AddChatLine($"[充值到账] {p.item} x{p.count}，当前钻石 {p.diamond}");
                RefreshInfo();
            }) },
            { "player.levelUp", Push<LevelUpPush>(p =>
            {
                Bootstrap.I.AddChatLine($"[升级] 升到 Lv.{p.level}");
                RefreshInfo();
            }) },
            { "sys.kick", Push<KickPush>(p =>
            {
                Bootstrap.I.SetStatus("被踢下线: " + p.reason);
                InGame = false;
            }) },
        };
        NetworkClient.I.OnPush += HandlePush;
        NetworkClient.I.OnDisconnected += HandleDisconnect;
    }

    // ------------------------------------------------------------------
    /// <summary>第一步：连 login 服验证账号，成功后展示大区列表供玩家选择</summary>
    public void Login(string account, string password,
                      System.Action<string> onError = null)
    {
        NetworkClient.I.Connect(loginHost, loginPort);
        NetworkClient.I.Call<LoginAuthRsp>("login.auth",
            new LoginAuthReq { account = account, password = password },
            rsp =>
            {
                if (!rsp.ok) { onError?.Invoke(rsp.error); return; }
                Account = rsp.account;
                Debug.Log($"[Login] auth ok, zones={rsp.zones.Count}");
                Bootstrap.I.ShowZoneSelect(rsp.zones);
            },
            err => onError?.Invoke(err));
    }

    /// <summary>第二步：玩家选定大区，发令牌并返回该大区的 game 地址</summary>
    public void EnterZone(string zone, System.Action<string> onError = null)
    {
        NetworkClient.I.Call<EnterZoneRsp>("login.enterZone",
            new EnterZoneReq { zone = zone },
            rsp =>
            {
                if (!rsp.ok) { onError?.Invoke(rsp.error); return; }
                Zone = rsp.zone;
                Token = rsp.token;
                Debug.Log($"[Login] enter zone={Zone}, gameAddr={rsp.gameAddr}");
                EnterGame(rsp.gameAddr, onError);
            },
            err => onError?.Invoke(err));
    }

    /// <summary>第二步：拿 token 连 game 进程，进入游戏</summary>
    private void EnterGame(string gameAddr, System.Action<string> onError)
    {
        string[] parts = gameAddr.Split(':');
        NetworkClient.I.Connect(parts[0], int.Parse(parts[1]));
        NetworkClient.I.Call<GameEnterRsp>("game.enter",
            new GameEnterReq { account = Account, token = Token },
            rsp =>
            {
                if (!rsp.ok) { onError?.Invoke(rsp.error); return; }
                Player = rsp.player;
                InGame = true;
                Bootstrap.I.ShowHud();
                Bootstrap.I.SetStatus(
                    $"进入游戏成功 [大区{Zone} {Player.service}] {Player.name} Lv.{Player.level}");
                RefreshHud();
            },
            err => onError?.Invoke(err));
    }

    // ------------------------------------------------------------------
    public void SendChat(string text)
    {
        if (!InGame || string.IsNullOrEmpty(text)) return;
        NetworkClient.I.Call<OkRsp>("player.chat", new ChatReq { text = text },
            rsp => { }, err => Bootstrap.I.SetStatus("chat 失败: " + err));
    }

    public void RefreshInfo()
    {
        if (!InGame) return;
        NetworkClient.I.Call<PlayerInfo>("player.getInfo", null,
            rsp => { Player = rsp; RefreshHud(); });
    }

    /// <summary>演示充值：直接调 recharge 组的 HTTP 回调（真实项目由支付平台发起）</summary>
    public void TestRecharge(int count)
    {
        string url = $"http://127.0.0.1:41100/pay?account={Account}&item=diamond&count={count}&zone={Zone}";
        StartCoroutine(GetUrl(url));
    }

    private System.Collections.IEnumerator GetUrl(string url)
    {
        using (var req = UnityEngine.Networking.UnityWebRequest.Get(url))
        {
            yield return req.SendWebRequest();
            Debug.Log("[Recharge] " + req.downloadHandler.text);
        }
    }

    // ------------------------------------------------------------------
    /// <summary>把强类型处理方法包装成推送处理器：统一负责 JSON 解析与容错</summary>
    private static System.Action<string> Push<T>(System.Action<T> handler) where T : class
    {
        return json =>
        {
            T data;
            try
            {
                data = JsonUtility.FromJson<T>(json);
            }
            catch (System.Exception e)
            {
                Debug.LogWarning($"[Push] {typeof(T).Name} 解析失败: {e.Message} json={json}");
                return;
            }
            if (data == null) return;
            handler(data);
        };
    }

    private void HandlePush(string key, string json)
    {
        System.Action<string> handler;
        if (_pushHandlers.TryGetValue(key, out handler))
            handler(json);
        else
            Debug.Log($"[Push] 未处理的推送 {key}: {json}");
    }

    private void HandleDisconnect()
    {
        InGame = false;
        Bootstrap.I.SetStatus("与服务器断开连接");
        Bootstrap.I.ShowLogin();
    }

    private void RefreshHud()
    {
        if (Player != null)
            Bootstrap.I.SetInfo(
                $"{Player.name}  Lv.{Player.level}  EXP {Player.exp}  钻石 {Player.diamond}");
    }
}
