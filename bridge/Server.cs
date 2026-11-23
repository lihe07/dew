using System;
using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;

namespace StardewBridge;

/// <summary>A client connection. Messages are JSON with a 4-byte big-endian length prefix, so web pages can't speak it.</summary>
internal sealed class Client
{
    // a BufferedStream shares one buffer between reads and writes, so it must never be used for both
    private readonly Stream Input;
    private readonly Stream Output;
    private readonly object WriteLock = new();
    private readonly TcpClient Tcp;

    /// <summary>Instructions, run one request at a time on the game thread.</summary>
    public readonly ConcurrentQueue<JObject> Requests = new();
    public bool Authenticated;
    public volatile bool Closed;
    public volatile bool CancelRequested;

    /// <summary>Read-only requests, answered even while an action runs.</summary>
    public readonly ConcurrentQueue<JObject> Reads = new();

    /// <summary>When we last heard from the client, as Environment.TickCount64.</summary>
    public long LastSeen = Environment.TickCount64;

    public Client(TcpClient tcp)
    {
        this.Tcp = tcp;
        this.Tcp.NoDelay = true;
        this.Tcp.SendTimeout = 3000; // a stalled client must never hang the game thread
        this.Output = tcp.GetStream();
        this.Input = new BufferedStream(this.Output, 1 << 16);
    }

    public JObject Read()
    {
        byte[] header = this.ReadExact(4);
        int length = BinaryPrimitives.ReadInt32BigEndian(header);
        if (length is <= 0 or > 4 << 20)
            throw new IOException("invalid message length");
        return JObject.Parse(Encoding.UTF8.GetString(this.ReadExact(length)));
    }

    private byte[] ReadExact(int count)
    {
        byte[] buffer = new byte[count];
        int read = 0;
        while (read < count)
        {
            int got = this.Input.Read(buffer, read, count - read);
            if (got <= 0)
                throw new EndOfStreamException();
            read += got;
        }
        return buffer;
    }

    public void Send(JObject message)
    {
        if (this.Closed)
            return;
        byte[] body = Encoding.UTF8.GetBytes(message.ToString(Formatting.None));
        byte[] packet = new byte[body.Length + 4];
        BinaryPrimitives.WriteInt32BigEndian(packet, body.Length);
        body.CopyTo(packet, 4);
        try
        {
            lock (this.WriteLock)
                this.Output.Write(packet);
        }
        catch (Exception)
        {
            this.Close();
        }
    }

    public void Close()
    {
        this.Closed = true;
        this.Tcp.Close();
    }
}

internal sealed class Server
{
    private readonly TcpListener Listener;
    private readonly Machine Machine;
    private readonly string Token;

    public Server(Machine machine, int port, string token)
    {
        this.Machine = machine;
        this.Token = token;
        this.Listener = new TcpListener(IPAddress.Loopback, port);
    }

    public void Start()
    {
        this.Listener.Start();
        new Thread(this.Accept) { IsBackground = true, Name = "StardewBridge" }.Start();
    }

    private void Accept()
    {
        while (true)
        {
            try
            {
                var client = new Client(this.Listener.AcceptTcpClient());
                new Thread(() => this.Serve(client)) { IsBackground = true, Name = "StardewBridge client" }.Start();
            }
            catch (Exception ex)
            {
                ModEntry.Log.Log($"Listener error: {ex.Message}", LogLevel.Trace);
                Thread.Sleep(100);
            }
        }
    }

    private void Serve(Client client)
    {
        try
        {
            while (!client.Closed)
            {
                JObject request = client.Read();
                client.LastSeen = Environment.TickCount64;

                if (!client.Authenticated)
                {
                    if ((string?)request["token"] != this.Token)
                    {
                        client.Send(new JObject { ["id"] = request["id"], ["error"] = "wrong token" });
                        break;
                    }
                    if (!this.Machine.TryConnect(client, (bool?)request["steal"] ?? false))
                    {
                        client.Send(new JObject { ["id"] = request["id"], ["error"] = "another client is attached" });
                        break;
                    }
                    client.Authenticated = true;
                    client.Send(new JObject { ["id"] = request["id"], ["results"] = new JArray("connected") });
                    continue;
                }

                if (request["ping"] != null)
                    client.Send(new JObject { ["id"] = request["id"], ["results"] = new JArray() });
                else if (request["cancel"] != null)
                {
                    client.CancelRequested = true;
                    client.Send(new JObject { ["id"] = request["id"], ["results"] = new JArray() });
                }
                else if ((string?)request["lane"] == "read")
                    client.Reads.Enqueue(request);
                else
                    client.Requests.Enqueue(request);
            }
        }
        catch (Exception ex)
        {
            ModEntry.Log.Log($"Client disconnected: {ex.Message}", LogLevel.Trace);
        }
        client.Close();
    }
}
