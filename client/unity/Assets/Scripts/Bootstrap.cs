using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using System.Collections.Generic;

/// <summary>
/// 启动器：纯代码搭建 UI（无需手动编辑场景）。
/// 场景里只需一个空 GameObject 挂上本脚本即可运行。
///
/// 界面:
///   登录面板: 账号 / 密码输入框 + 登录按钮 + 状态栏
///   游戏面板: 角色信息 + 聊天区 + 聊天输入 + 发送/刷新/充值测试按钮
/// </summary>
public class Bootstrap : MonoBehaviour
{
    public static Bootstrap I { get; private set; }

    private Font _font;
    private GameObject _loginPanel;
    private GameObject _zonePanel;
    private GameObject _hudPanel;
    private InputField _accountInput;
    private InputField _passwordInput;
    private InputField _chatInput;
    private Text _statusText;
    private Text _infoText;
    private Text _chatText;

    private void Awake()
    {
        if (I != null && I != this) { Destroy(gameObject); return; }
        I = this;

        // 网络组件与主流程组件
        gameObject.AddComponent<NetworkClient>();
        gameObject.AddComponent<GameManager>();

        _font = GetFont();
        BuildUi();
        ShowLogin();
    }

    private static Font GetFont()
    {
        // 兼容不同 Unity 版本的内置字体
        try { return Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); }
        catch { return Resources.GetBuiltinResource<Font>("Arial.ttf"); }
    }

    // ------------------------------------------------------------------
    public void ShowLogin()
    {
        _loginPanel.SetActive(true);
        if (_zonePanel != null) _zonePanel.SetActive(false);
        _hudPanel.SetActive(false);
    }

    /// <summary>登录成功后展示大区列表（动态构建，大区来自服务端）</summary>
    public void ShowZoneSelect(List<ZoneInfo> zones)
    {
        _loginPanel.SetActive(false);
        _hudPanel.SetActive(false);
        if (_zonePanel != null) Destroy(_zonePanel);
        _zonePanel = BuildZonePanel(_loginPanel.transform.parent, zones);
    }

    public void ShowHud()
    {
        _loginPanel.SetActive(false);
        if (_zonePanel != null) _zonePanel.SetActive(false);
        _hudPanel.SetActive(true);
    }

    public void SetStatus(string msg)
    {
        _statusText.text = msg;
        Debug.Log("[UI] " + msg);
    }

    public void SetInfo(string msg) => _infoText.text = msg;

    public void AddChatLine(string line)
    {
        _chatText.text += line + "\n";
    }

    // ------------------------------------------------------------------
    private void BuildUi()
    {
        // EventSystem
        if (FindObjectOfType<EventSystem>() == null)
        {
            var es = new GameObject("EventSystem");
            es.AddComponent<EventSystem>();
            es.AddComponent<StandaloneInputModule>();
        }

        // Canvas
        var canvasGo = new GameObject("Canvas");
        var canvas = canvasGo.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        var scaler = canvasGo.AddComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1280, 720);
        canvasGo.AddComponent<GraphicRaycaster>();

        _loginPanel = BuildLoginPanel(canvasGo.transform);
        _hudPanel = BuildHudPanel(canvasGo.transform);

        // 全局状态栏
        _statusText = MakeText(canvasGo.transform, "StatusBar", "",
            new Vector2(0, 40), new Vector2(1200, 40), 16, TextAnchor.MiddleCenter);
    }

    private GameObject BuildLoginPanel(Transform parent)
    {
        var panel = MakePanel(parent, "LoginPanel", new Vector2(0, 40), new Vector2(420, 320));

        MakeText(panel.transform, "Title", "登  录",
            new Vector2(0, 110), new Vector2(400, 50), 28, TextAnchor.MiddleCenter);

        MakeText(panel.transform, "AccountLabel", "账号",
            new Vector2(-140, 50), new Vector2(60, 30), 18, TextAnchor.MiddleRight);
        _accountInput = MakeInput(panel.transform, "AccountInput",
            new Vector2(30, 50), new Vector2(220, 36), "tester");

        MakeText(panel.transform, "PasswordLabel", "密码",
            new Vector2(-140, 0), new Vector2(60, 30), 18, TextAnchor.MiddleRight);
        _passwordInput = MakeInput(panel.transform, "PasswordInput",
            new Vector2(30, 0), new Vector2(220, 36), "123456");
        _passwordInput.contentType = InputField.ContentType.Password;

        var btn = MakeButton(panel.transform, "LoginButton", "登 录",
            new Vector2(0, -80), new Vector2(180, 44));
        btn.onClick.AddListener(() =>
        {
            SetStatus("登录中...");
            GameManager.I.Login(_accountInput.text, _passwordInput.text,
                err => SetStatus("登录失败: " + err));
        });
        return panel;
    }

    /// <summary>大区选择面板：每个大区一个按钮，点击后调 login.enterZone</summary>
    private GameObject BuildZonePanel(Transform parent, List<ZoneInfo> zones)
    {
        int height = 160 + zones.Count * 70;
        var panel = MakePanel(parent, "ZonePanel", new Vector2(0, 40),
            new Vector2(460, height));

        MakeText(panel.transform, "ZoneTitle", "选择大区",
            new Vector2(0, height / 2 - 50), new Vector2(400, 50), 26,
            TextAnchor.MiddleCenter);

        for (int i = 0; i < zones.Count; i++)
        {
            var info = zones[i];
            var btn = MakeButton(panel.transform, $"Zone{i}",
                $"{info.name}（{info.zone}）",
                new Vector2(0, height / 2 - 130 - i * 70), new Vector2(300, 52));
            btn.onClick.AddListener(() =>
            {
                SetStatus($"进入 {info.name}...");
                GameManager.I.EnterZone(info.zone,
                    err => SetStatus("进入大区失败: " + err));
            });
        }
        return panel;
    }

    private GameObject BuildHudPanel(Transform parent)
    {
        var panel = MakePanel(parent, "HudPanel", new Vector2(0, 40), new Vector2(900, 480));

        _infoText = MakeText(panel.transform, "Info", "",
            new Vector2(0, 200), new Vector2(860, 40), 20, TextAnchor.MiddleCenter);

        _chatText = MakeText(panel.transform, "ChatLog", "",
            new Vector2(0, 10), new Vector2(860, 300), 16, TextAnchor.UpperLeft);
        _chatText.verticalOverflow = VerticalWrapMode.Overflow;

        _chatInput = MakeInput(panel.transform, "ChatInput",
            new Vector2(-160, -200), new Vector2(480, 36), "");

        var sendBtn = MakeButton(panel.transform, "SendButton", "发送",
            new Vector2(180, -200), new Vector2(120, 40));
        sendBtn.onClick.AddListener(() =>
        {
            GameManager.I.SendChat(_chatInput.text);
            _chatInput.text = "";
        });

        var refreshBtn = MakeButton(panel.transform, "RefreshButton", "刷新信息",
            new Vector2(320, -200), new Vector2(110, 40));
        refreshBtn.onClick.AddListener(() => GameManager.I.RefreshInfo());

        var rechargeBtn = MakeButton(panel.transform, "RechargeButton", "充100钻(测试)",
            new Vector2(320, -120), new Vector2(130, 36));
        rechargeBtn.onClick.AddListener(() => GameManager.I.TestRecharge(100));

        return panel;
    }

    // ------------------------------------------------------------------
    // UI 构件
    // ------------------------------------------------------------------
    private GameObject MakePanel(Transform parent, string name, Vector2 pos, Vector2 size)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(Image));
        go.transform.SetParent(parent, false);
        var rt = (RectTransform)go.transform;
        rt.anchoredPosition = pos;
        rt.sizeDelta = size;
        go.GetComponent<Image>().color = new Color(0.15f, 0.15f, 0.18f, 0.9f);
        return go;
    }

    private Text MakeText(Transform parent, string name, string content,
                          Vector2 pos, Vector2 size, int fontSize, TextAnchor anchor)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(Text));
        go.transform.SetParent(parent, false);
        var rt = (RectTransform)go.transform;
        rt.anchoredPosition = pos;
        rt.sizeDelta = size;
        var text = go.GetComponent<Text>();
        text.font = _font;
        text.text = content;
        text.fontSize = fontSize;
        text.alignment = anchor;
        text.color = Color.white;
        return text;
    }

    private InputField MakeInput(Transform parent, string name,
                                 Vector2 pos, Vector2 size, string defaultText)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(InputField));
        go.transform.SetParent(parent, false);
        var rt = (RectTransform)go.transform;
        rt.anchoredPosition = pos;
        rt.sizeDelta = size;
        go.GetComponent<Image>().color = Color.white;

        var text = MakeText(go.transform, "Text", defaultText,
            Vector2.zero, size - new Vector2(10, 0), 16, TextAnchor.MiddleLeft);
        text.color = Color.black;
        var placeholder = MakeText(go.transform, "Placeholder", "",
            Vector2.zero, size - new Vector2(10, 0), 16, TextAnchor.MiddleLeft);
        placeholder.color = Color.gray;

        var input = go.GetComponent<InputField>();
        input.textComponent = text;
        input.placeholder = placeholder;
        input.text = defaultText;
        return input;
    }

    private Button MakeButton(Transform parent, string name, string label,
                              Vector2 pos, Vector2 size)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
        go.transform.SetParent(parent, false);
        var rt = (RectTransform)go.transform;
        rt.anchoredPosition = pos;
        rt.sizeDelta = size;
        go.GetComponent<Image>().color = new Color(0.25f, 0.5f, 0.8f);
        var text = MakeText(go.transform, "Text", label,
            Vector2.zero, size, 18, TextAnchor.MiddleCenter);
        return go.GetComponent<Button>();
    }
}
