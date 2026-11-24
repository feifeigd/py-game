using UnityEngine;

/// <summary>
/// 游戏主流程：登录(login 组) -> 进入游戏(game 组) -> 聊天/充值推送。
/// 对应服务端的完整登录链路:
///   login.auth(LoginReq) -> LoginAuthRsp{token, gameAddr}
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
    public PlayerInfo Player { get; private set; }
    public bool InGame { get; private set; }

    private void Awake()
    {
        if (I != null && I != this) { Destroy(gameObject); return; }
        I = this;
    }

    private void Start()
    {
        NetworkClient.I.OnPush += HandlePush;
        NetworkClient.I.OnDisconnected += HandleDisconnect;
    }

    // ------------------------------------------------------------------
    /// <summary>第一步：连 login 服登录，成功后自动连 game 服进入游戏</summary>
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
                Token = rsp.token;
                Debug.Log($"[Login] ok, gameAddr={rsp.gameAddr}");
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
                    $"进入游戏成功 [{Player.service}] {Player.name} Lv.{Player.level}");
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
        string url = $"http://127.0.0.1:41100/pay?account={Account}&item=diamond&count={count}";
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
    private void HandlePush(string key, string json)
    {
        switch (key)
        {
            case "chat.push":
                var chat = JsonUtility.FromJson<ChatPush>(json);
                Bootstrap.I.AddChatLine($"{chat.account}: {chat.text}");
                break;
            case "recharge.push":
                var rc = JsonUtility.FromJson<RechargePush>(json);
                Bootstrap.I.AddChatLine($"[充值到账] {rc.item} x{rc.count}，当前钻石 {rc.diamond}");
                RefreshInfo();
                break;
            case "player.levelUp":
                Bootstrap.I.AddChatLine("[升级] " + json);
                RefreshInfo();
                break;
            case "sys.kick":
                var kick = JsonUtility.FromJson<KickPush>(json);
                Bootstrap.I.SetStatus("被踢下线: " + (kick != null ? kick.reason : ""));
                InGame = false;
                break;
            default:
                Debug.Log($"[Push] 未处理的推送 {key}: {json}");
                break;
        }
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
