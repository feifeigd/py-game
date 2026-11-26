/**
 * 启动器：纯代码搭建 UI（无需手动编辑场景）。
 * 场景里保留默认的 Canvas / Main Camera，把本组件挂到任意一个节点即可运行。
 *
 * 界面:
 *   登录面板: 账号 / 密码输入框 + 登录按钮 + 状态栏
 *   游戏面板: 角色信息 + 聊天区 + 聊天输入 + 发送/刷新/充值测试按钮
 *
 * 实现说明: 所有背景用 Graphics 画纯色圆角矩形，不依赖任何图片资源。
 */
import {
    _decorator, Button, Canvas, Color, Component, director,
    EditBox, Graphics, Label, Layers, Node, UITransform,
} from 'cc';
import { GameManager } from './GameManager';
import { ServerInfo } from './Net/Packet';

const { ccclass } = _decorator;

const COLOR_PANEL = new Color(38, 38, 46, 230);
const COLOR_BUTTON = new Color(64, 128, 204, 255);
const COLOR_INPUT_BG = Color.WHITE.clone();

@ccclass('Bootstrap')
export class Bootstrap extends Component {
    public static I: Bootstrap | null = null;

    private _loginPanel: Node = null as unknown as Node;
    private _hudPanel: Node = null as unknown as Node;
    private _accountInput: EditBox = null as unknown as EditBox;
    private _passwordInput: EditBox = null as unknown as EditBox;
    private _chatInput: EditBox = null as unknown as EditBox;
    private _serverPanel: Node | null = null;
    private _statusLabel: Label = null as unknown as Label;
    private _infoLabel: Label = null as unknown as Label;
    private _chatLabel: Label = null as unknown as Label;

    onLoad(): void {
        if (Bootstrap.I && Bootstrap.I !== this) { this.node.destroy(); return; }
        Bootstrap.I = this;
        GameManager.I.init();
        this._buildUI();
        this.showLogin();
    }

    onDestroy(): void {
        if (Bootstrap.I === this) Bootstrap.I = null;
    }

    // ------------------------------------------------------------------
    public showLogin(): void {
        this._loginPanel.active = true;
        this._hudPanel.active = false;
        if (this._serverPanel) this._serverPanel.active = false;
    }

    public showHud(): void {
        this._loginPanel.active = false;
        this._hudPanel.active = true;
        if (this._serverPanel) this._serverPanel.active = false;
    }

    /** 账号验证通过后展示服务器列表，玩家选服后调 login.enterServer */
    public showServerSelect(servers: ServerInfo[]): void {
        this._loginPanel.active = false;
        if (this._serverPanel) this._serverPanel.destroy();
        const panel = this._makePanel(this._loginPanel.parent!, 'ServerPanel',
            0, 40, 460, 160 + servers.length * 70);
        this._serverPanel = panel;
        this._makeLabel(panel, 'Title', '选择服务器', 0, 60 + servers.length * 35, 400, 40, 24,
            Label.HorizontalAlign.CENTER, Color.WHITE);
        servers.forEach((info, i) => {
            const tag = info.recommend ? '·推荐'
                : info.status === 2 ? '·拥挤'
                : info.status === 3 ? '·爆满' : '';
            const btn = this._makeButton(panel, `Server${info.server}`,
                `${info.name}（${info.server}）${tag}`, 0, 30 + (servers.length - 1 - i) * 70, 300, 52);
            btn.node.on(Button.EventType.CLICK, () => {
                this.setStatus(`进入服务器 ${info.server}...`);
                GameManager.I.enterServer(info.server,
                    err => this.setStatus(`进入服务器失败: ${err}`));
            });
        });
    }

    public setStatus(msg: string): void {
        this._statusLabel.string = msg;
        console.log('[UI] ' + msg);
    }

    public setInfo(msg: string): void {
        this._infoLabel.string = msg;
    }

    public addChatLine(line: string): void {
        this._chatLabel.string += line + '\n';
    }

    // ------------------------------------------------------------------
    private _buildUI(): void {
        const canvasNode = this._findCanvasNode();
        this._loginPanel = this._buildLoginPanel(canvasNode);
        this._hudPanel = this._buildHudPanel(canvasNode);
        // 全局状态栏
        this._statusLabel = this._makeLabel(canvasNode, 'StatusBar', '',
            0, -290, 1200, 40, 16, Label.HorizontalAlign.CENTER, Color.WHITE);
    }

    private _findCanvasNode(): Node {
        const scene = director.getScene()!;
        const canvas = scene.getComponentInChildren(Canvas);
        if (canvas) return canvas.node;
        // 场景里没有 Canvas 就自己建一个（仍需场景里有 Camera 才能渲染）
        const node = new Node('Canvas');
        node.parent = scene;
        node.layer = Layers.Enum.UI_2D;
        node.addComponent(Canvas);
        return node;
    }

    private _buildLoginPanel(canvasNode: Node): Node {
        const panel = this._makePanel(canvasNode, 'LoginPanel', 0, 40, 420, 320);

        this._makeLabel(panel, 'Title', '登  录', 0, 110, 400, 50, 28,
            Label.HorizontalAlign.CENTER, Color.WHITE);

        this._makeLabel(panel, 'AccountLabel', '账号', -140, 50, 60, 30, 18,
            Label.HorizontalAlign.RIGHT, Color.WHITE);
        this._accountInput = this._makeInput(panel, 'AccountInput', 30, 50, 220, 36, 'tester');

        this._makeLabel(panel, 'PasswordLabel', '密码', -140, 0, 60, 30, 18,
            Label.HorizontalAlign.RIGHT, Color.WHITE);
        this._passwordInput = this._makeInput(panel, 'PasswordInput', 30, 0, 220, 36, '123456', true);

        const btn = this._makeButton(panel, 'LoginButton', '登 录', 0, -80, 180, 44);
        btn.node.on(Button.EventType.CLICK, () => {
            this.setStatus('登录中...');
            GameManager.I.login(this._accountInput.string, this._passwordInput.string,
                err => this.setStatus(`登录失败: ${err}`));
        });
        return panel;
    }

    private _buildHudPanel(canvasNode: Node): Node {
        const panel = this._makePanel(canvasNode, 'HudPanel', 0, 40, 900, 480);

        this._infoLabel = this._makeLabel(panel, 'Info', '', 0, 200, 860, 40, 20,
            Label.HorizontalAlign.CENTER, Color.WHITE);

        this._chatLabel = this._makeLabel(panel, 'ChatLog', '', 0, 10, 860, 300, 16,
            Label.HorizontalAlign.LEFT, Color.WHITE);
        this._chatLabel.verticalAlign = Label.VerticalAlign.TOP;
        this._chatLabel.overflow = Label.Overflow.NONE;

        this._chatInput = this._makeInput(panel, 'ChatInput', -160, -200, 480, 36, '');

        const sendBtn = this._makeButton(panel, 'SendButton', '发送', 180, -200, 120, 40);
        sendBtn.node.on(Button.EventType.CLICK, () => {
            GameManager.I.sendChat(this._chatInput.string);
            this._chatInput.string = '';
        });

        const refreshBtn = this._makeButton(panel, 'RefreshButton', '刷新信息', 320, -200, 110, 40);
        refreshBtn.node.on(Button.EventType.CLICK, () => GameManager.I.refreshInfo());

        const rechargeBtn = this._makeButton(panel, 'RechargeButton', '充100钻(测试)', 320, -120, 130, 36);
        rechargeBtn.node.on(Button.EventType.CLICK, () => GameManager.I.testRecharge(100));
        return panel;
    }

    // ------------------------------------------------------------------
    // UI 构件（注意：动态创建的节点要显式同步 layer，否则 UI 相机看不到）
    // ------------------------------------------------------------------
    private _makePanel(parent: Node, name: string,
                       x: number, y: number, w: number, h: number): Node {
        const node = new Node(name);
        node.parent = parent;
        node.layer = parent.layer;
        node.setPosition(x, y, 0);
        const ut = node.addComponent(UITransform);
        ut.setContentSize(w, h);
        const g = node.addComponent(Graphics);
        g.fillColor = COLOR_PANEL;
        g.roundRect(-w / 2, -h / 2, w, h, 8);
        g.fill();
        return node;
    }

    private _makeLabel(parent: Node, name: string, content: string,
                       x: number, y: number, w: number, h: number,
                       fontSize: number, align: Label.HorizontalAlign,
                       color: Color): Label {
        const node = new Node(name);
        node.parent = parent;
        node.layer = parent.layer;
        node.setPosition(x, y, 0);
        const ut = node.addComponent(UITransform);
        ut.setContentSize(w, h);
        const label = node.addComponent(Label);
        label.string = content;
        label.fontSize = fontSize;
        label.lineHeight = fontSize + 4;
        label.color = color;
        label.horizontalAlign = align;
        label.verticalAlign = Label.VerticalAlign.CENTER;
        label.overflow = Label.Overflow.CLAMP;
        return label;
    }

    private _makeInput(parent: Node, name: string,
                       x: number, y: number, w: number, h: number,
                       defaultText: string, password = false): EditBox {
        const node = new Node(name);
        node.parent = parent;
        node.layer = parent.layer;
        node.setPosition(x, y, 0);
        const ut = node.addComponent(UITransform);
        ut.setContentSize(w, h);
        const g = node.addComponent(Graphics);
        g.fillColor = COLOR_INPUT_BG;
        g.roundRect(-w / 2, -h / 2, w, h, 4);
        g.fill();

        const text = this._makeLabel(node, 'Text', defaultText, 0, 0, w - 10, h, 16,
            Label.HorizontalAlign.LEFT, Color.BLACK);
        const placeholder = this._makeLabel(node, 'Placeholder', '', 0, 0, w - 10, h, 16,
            Label.HorizontalAlign.LEFT, Color.GRAY);

        const input = node.addComponent(EditBox);
        input.textLabel = text;
        input.placeholderLabel = placeholder;
        input.string = defaultText;
        if (password) input.inputFlag = EditBox.InputFlag.PASSWORD;
        return input;
    }

    private _makeButton(parent: Node, name: string, label: string,
                        x: number, y: number, w: number, h: number): Button {
        const node = new Node(name);
        node.parent = parent;
        node.layer = parent.layer;
        node.setPosition(x, y, 0);
        const ut = node.addComponent(UITransform);
        ut.setContentSize(w, h);
        const g = node.addComponent(Graphics);
        g.fillColor = COLOR_BUTTON;
        g.roundRect(-w / 2, -h / 2, w, h, 6);
        g.fill();
        this._makeLabel(node, 'Label', label, 0, 0, w, h, 18,
            Label.HorizontalAlign.CENTER, Color.WHITE);
        const btn = node.addComponent(Button);
        btn.transition = Button.Transition.SCALE;
        btn.target = node;
        btn.zoomScale = 1.1;
        return btn;
    }
}
