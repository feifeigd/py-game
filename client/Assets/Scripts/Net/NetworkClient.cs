using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

/// <summary>
/// 网络客户端：与 python 服务端相同的 4 字节大端长度前缀 + JSON 协议。
/// 后台线程收包，主线程 Update 中分发（Unity API 只能在主线程调用）。
///
/// 用法:
///   NetworkClient.I.Connect("127.0.0.1", 11100);
///   NetworkClient.I.Call&lt;LoginAuthRsp&gt;("login.auth", new LoginAuthReq{...}, onOk, onErr);
///   NetworkClient.I.OnPush += (key, json) =&gt; { ... };
/// </summary>
public class NetworkClient : MonoBehaviour
{
    public static NetworkClient I { get; private set; }

    /// <summary>服务器推送回调: (key, dataJson)</summary>
    public event Action<string, string> OnPush;
    /// <summary>连接断开回调</summary>
    public event Action OnDisconnected;

    private TcpClient _client;
    private NetworkStream _stream;
    private Thread _readThread;
    private volatile bool _closed = true;

    private int _seq;
    private readonly object _sendLock = new object();

    private class PendingCall
    {
        public Type rspType;
        public Delegate onOk;   // Action<T>
        public Action<string> onErr;
    }

    private readonly Dictionary<int, PendingCall> _pending = new Dictionary<int, PendingCall>();
    private readonly ConcurrentQueue<Packet> _recvQueue = new ConcurrentQueue<Packet>();
    private readonly ConcurrentQueue<string> _eventQueue = new ConcurrentQueue<string>();

    public bool IsConnected => !_closed && _client != null && _client.Connected;

    private void Awake()
    {
        if (I != null && I != this) { Destroy(gameObject); return; }
        I = this;
        DontDestroyOnLoad(gameObject);
    }

    // ------------------------------------------------------------------
    public void Connect(string host, int port)
    {
        Close();
        _client = new TcpClient();
        _client.Connect(host, port);
        _stream = _client.GetStream();
        _closed = false;
        _readThread = new Thread(ReadLoop) { IsBackground = true };
        _readThread.Start();
        Debug.Log($"[Net] connected {host}:{port}");
    }

    public void Close()
    {
        _closed = true;
        try { _stream?.Close(); } catch { /* ignore */ }
        try { _client?.Close(); } catch { /* ignore */ }
        _stream = null;
        _client = null;
        lock (_sendLock) { _pending.Clear(); }
    }

    // ------------------------------------------------------------------
    /// <summary>请求-响应调用。TResp 必须是 [Serializable]。</summary>
    public void Call<TResp>(string key, object data,
                            Action<TResp> onOk, Action<string> onErr = null)
    {
        if (!IsConnected)
        {
            onErr?.Invoke("not connected");
            return;
        }
        int seq = ++_seq;
        lock (_sendLock)
        {
            _pending[seq] = new PendingCall
            {
                rspType = typeof(TResp),
                onOk = onOk,
                onErr = onErr,
            };
            Send(new Packet
            {
                key = key,
                seq = seq,
                data = data != null ? JsonUtility.ToJson(data) : "{}",
            });
        }
    }

    private void Send(Packet packet)
    {
        try
        {
            byte[] body = Encoding.UTF8.GetBytes(JsonUtility.ToJson(packet));
            byte[] head = BitConverter.GetBytes(System.Net.IPAddress.HostToNetworkOrder(body.Length));
            _stream.Write(head, 0, 4);
            _stream.Write(body, 0, body.Length);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[Net] send failed: {e.Message}");
            _eventQueue.Enqueue("disconnect");
        }
    }

    // ------------------------------------------------------------------
    private void ReadLoop()
    {
        byte[] head = new byte[4];
        try
        {
            while (!_closed)
            {
                if (!ReadExact(head, 4)) break;
                int size = System.Net.IPAddress.NetworkToHostOrder(BitConverter.ToInt32(head, 0));
                if (size <= 0 || size > 4 * 1024 * 1024) break;
                byte[] body = new byte[size];
                if (!ReadExact(body, size)) break;
                string json = Encoding.UTF8.GetString(body);
                Packet packet = JsonUtility.FromJson<Packet>(json);
                _recvQueue.Enqueue(packet);
            }
        }
        catch (Exception)
        {
            // 连接被关闭等
        }
        _eventQueue.Enqueue("disconnect");
    }

    private bool ReadExact(byte[] buf, int len)
    {
        int got = 0;
        while (got < len)
        {
            int n = _stream.Read(buf, got, len - got);
            if (n <= 0) return false;
            got += n;
        }
        return true;
    }

    // ------------------------------------------------------------------
    private void Update()
    {
        // 主线程分发收包
        while (_recvQueue.TryDequeue(out Packet packet))
            Dispatch(packet);
        while (_eventQueue.TryDequeue(out string evt))
        {
            if (evt == "disconnect")
            {
                bool wasConnected = IsConnected;
                Close();
                if (wasConnected) OnDisconnected?.Invoke();
            }
        }
    }

    private void Dispatch(Packet packet)
    {
        if (packet.seq > 0)
        {
            PendingCall call;
            lock (_sendLock)
            {
                if (!_pending.TryGetValue(packet.seq, out call)) return;
                _pending.Remove(packet.seq);
            }
            if (!string.IsNullOrEmpty(packet.error))
            {
                (call.onErr as Action<string>)?.Invoke(packet.error);
                return;
            }
            try
            {
                object rsp = JsonUtility.FromJson(packet.data ?? "{}", call.rspType);
                call.onOk?.DynamicInvoke(rsp);
            }
            catch (Exception e)
            {
                Debug.LogError($"[Net] decode {packet.key} rsp failed: {e}");
                (call.onErr as Action<string>)?.Invoke("decode error");
            }
        }
        else
        {
            OnPush?.Invoke(packet.key, packet.data ?? "{}");
        }
    }

    private void OnDestroy()
    {
        Close();
    }
}
