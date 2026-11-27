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
    private GameObject _serverPanel;
    private GameObject _rolePanel;
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
        if (_serverPanel != null) _serverPanel.SetActive(false);
        if (_rolePanel != null) _rolePanel.SetActive(false);
        _hudPanel.SetActive(false);
    }

    /// <summary>登录成功后展示服务器列表（动态构建，服务器来自服务端）</summary>
    public void ShowServerSelect(List<ServerInfo> servers)
    {
        _loginPanel.SetActive(false);
        _hudPanel.SetActive(false);
        if (_serverPanel != null) Destroy(_serverPanel);
        _serverPanel = BuildServerPanel(_loginPanel.transform.parent, servers);
    }

    /// <summary>选服后多角色时展示角色列表（含"新建角色"按钮）</summary>
    public void ShowRoleSelect(List<RoleInfo> roles)
    {
        _loginPanel.SetActive(false);
        if (_serverPanel != null) _serverPanel.SetActive(false);
        _hudPanel.SetActive(false);
        if (_rolePanel != null) Destroy(_rolePanel);
        _rolePanel = BuildRolePanel(_loginPanel.transform.parent, roles);
    }

    public void ShowHud()
    {
        _loginPanel.SetActive(false);
        if (_serverPanel != null) _serverPanel.SetActive(false);
        if (_rolePanel != null) _rolePanel.SetActive(false);
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

    /// <summary>服务器选择面板：每个服一个按钮，点击后调 login.enterServer</summary>
    private GameObject BuildServerPanel(Transform parent, List<ServerInfo> servers)
    {
        int height = 160 + servers.Count * 70;
        var panel = MakePanel(parent, "ServerPanel", new Vector2(0, 40),
            new Vector2(460, height));

        MakeText(panel.transform, "ServerTitle", "选择服务器",
            new Vector2(0, height / 2 - 50), new Vector2(400, 50), 26,
            TextAnchor.MiddleCenter);

        for (int i = 0; i < servers.Count; i++)
        {
            var info = servers[i];
            string tag = info.recommend ? "·推荐"
                : info.status == 2 ? "·拥挤"
                : info.status == 3 ? "·爆满" : "";
            var btn = MakeButton(panel.transform, $"Server{i}",
                $"{info.name}（{info.server}）{tag}",
                new Vector2(0, height / 2 - 130 - i * 70), new Vector2(300, 52));
            btn.onClick.AddListener(() =>
            {
                SetStatus($"进入 {info.name}...");
                GameManager.I.EnterServer(info.server,
                    err => SetStatus("进入服务器失败: " + err));
            });
        }
        return panel;
    }

    /// <summary>角色面板：已有角色一人一个按钮（含性别，点击选中），
    /// 服务端按最近登录倒序，默认选中上次玩的角色，"进入游戏"进选中角色；
    /// 底部是创角表单（名字输入 + 性别切换 + 创建并进入），
    /// 0 个角色时只显示创角表单</summary>
    private GameObject BuildRolePanel(Transform parent, List<RoleInfo> roles)
    {
        int height = 400 + roles.Count * 70;
        var panel = MakePanel(parent, "RolePanel", new Vector2(0, 40),
            new Vector2(460, height));

        MakeText(panel.transform, "RoleTitle", roles.Count > 0 ? "选择角色" : "创建角色",
            new Vector2(0, height / 2 - 50), new Vector2(400, 50), 26,
            TextAnchor.MiddleCenter);

        int y = height / 2 - 130;
        string selected = roles.Count > 0 ? roles[0].role : null;   // 默认选中最近登录的
        Text marker = null;
        if (roles.Count > 0)
            marker = MakeText(panel.transform, "SelectedMarker", "▶",
                new Vector2(-190, y), new Vector2(40, 52), 24, TextAnchor.MiddleCenter);

        for (int i = 0; i < roles.Count; i++)
        {
            var info = roles[i];
            int btnY = y - i * 70;
            string sexTag = info.sex == 2 ? "（女）" : "（男）";
            string recentTag = i == 0 ? " ·最近" : "";
            var btn = MakeButton(panel.transform, $"Role{i}",
                $"{info.name}{sexTag}  Lv.{info.level}{recentTag}",
                new Vector2(0, btnY), new Vector2(300, 52));
            btn.onClick.AddListener(() =>
            {
                selected = info.role;
                marker.rectTransform.anchoredPosition = new Vector2(-190, btnY);
            });
        }
        y -= roles.Count * 70;

        if (roles.Count > 0)
        {
            var enterBtn = MakeButton(panel.transform, "EnterGame", "进入游戏",
                new Vector2(0, y), new Vector2(300, 52));
            enterBtn.onClick.AddListener(() =>
            {
                SetStatus("进入角色...");
                GameManager.I.EnterRole(selected,
                    err => SetStatus("进入角色失败: " + err));
            });
            y -= 80;
            MakeText(panel.transform, "CreateLabel", "—— 新建角色 ——",
                new Vector2(0, y + 15), new Vector2(300, 30), 16,
                TextAnchor.MiddleCenter);
        }

        // 创角表单：角色名 + 性别 + 确认
        var nameInput = MakeInput(panel.transform, "NameInput",
            new Vector2(-60, y - 40), new Vector2(220, 36), "");
        ((Text)nameInput.placeholder).text = "输入角色名（1-12字）";

        int sex = 1;
        var sexBtn = MakeButton(panel.transform, "SexToggle", "性别：男",
            new Vector2(140, y - 40), new Vector2(100, 36));
        sexBtn.onClick.AddListener(() =>
        {
            sex = sex == 1 ? 2 : 1;
            sexBtn.GetComponentInChildren<Text>().text = sex == 1 ? "性别：男" : "性别：女";
        });

        var createBtn = MakeButton(panel.transform, "CreateRole", "创建并进入",
            new Vector2(0, y - 110), new Vector2(300, 52));
        createBtn.onClick.AddListener(() =>
        {
            string name = nameInput.text.Trim();
            if (name.Length == 0) { SetStatus("请输入角色名"); return; }
            SetStatus("创建角色...");
            GameManager.I.CreateRole(name, sex,
                err => SetStatus("创建角色失败: " + err));
        });
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
