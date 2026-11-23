using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewValley;

namespace StardewBridge;

/// <summary>An expected failure whose message is meant for the client.</summary>
internal sealed class OpError : Exception
{
    public OpError(string message) : base(message) { }
}

/// <summary>An instruction which takes time.</summary>
internal interface IActiveOp
{
    /// <summary>Called before each tick, after the previous one ran. Returns the result when finished, else null.</summary>
    JToken? Poll();
}

/// <summary>Executes instructions on the game thread. The world always runs; reads are answered at once, even while an action runs.</summary>
internal sealed class Machine
{
    private volatile Client? Pending;
    private Client? Client;

    private JObject? Request;
    private JArray Ops = new();
    private JArray Results = new();
    private int Index;
    private IActiveOp? Active;

    private int TtlMs = 15000;
    private long SeenSeq;
    private bool Configured;

    public bool Attached => this.Client != null && this.Configured;
    public bool Human { get; private set; }

    /// <summary>Whether the agent's virtual devices are live.</summary>
    public bool Controls => this.Attached && !this.Human;

    /****
    ** Connection
    ****/
    /// <summary>Called from the client's thread.</summary>
    public bool TryConnect(Client client, bool steal)
    {
        Client? current = this.Client;
        if (current is { Closed: false } && Environment.TickCount64 - current.LastSeen < this.TtlMs && !steal)
            return false;
        this.Pending = client;
        return true;
    }

    private void Detach(string reason)
    {
        if (this.Client == null)
            return;
        ModEntry.Log.Log($"Agent detached: {reason}.", LogLevel.Info);
        this.Client.Close();
        this.Client = null;
        this.Configured = false;
        this.Request = null;
        this.Active = null;
        this.Human = false;
        Clock.Speed = 1;
        Devices.Release();
    }

    public void ToggleHuman()
    {
        this.Human = !this.Human;
        Devices.Release();
        ModEntry.Journal.Add("control", this.Human ? "the player took control" : "the player gave control back");
        Game1.addHUDMessage(HUDMessage.ForCornerTextbox(this.Human ? "You have control" : "Agent has control"));
        if (this.Human && this.Request != null)
            this.Reply("the player took control");
    }

    /****
    ** Execution
    ****/
    public void OnTick()
    {
        ModEntry.Journal.Poll();

        if (this.Pending is { } pending)
        {
            this.Pending = null;
            if (this.Client != null)
                this.Detach("replaced by another client");
            this.Client = pending;
            this.SeenSeq = ModEntry.Journal.LastSeq;
        }
        if (this.Client == null)
            return;
        if (this.Client.Closed)
        {
            this.Detach("connection closed");
            return;
        }
        if (Environment.TickCount64 - this.Client.LastSeen > this.TtlMs)
        {
            this.Detach("no heartbeat");
            return;
        }

        if (this.Client.CancelRequested)
        {
            this.Client.CancelRequested = false;
            if (this.Request != null)
            {
                Devices.Release();
                this.Reply("cancelled");
            }
        }

        this.ServeReads();

        try
        {
            // continue the current instruction
            if (this.Active != null)
            {
                JToken? result = this.Active.Poll();
                if (result == null)
                    return;
                this.Active = null;
                this.Results.Add(result);
                this.Index++;
            }

            while (true)
            {
                if (this.Request == null && !this.Next())
                    return;
                while (this.Index < this.Ops.Count)
                {
                    var op = this.Ops[this.Index] as JObject ?? throw new OpError("expected an instruction object");
                    (JToken? result, IActiveOp? active) = this.Execute(op);
                    if (active != null)
                    {
                        this.Active = active;
                        return;
                    }
                    this.Results.Add(result ?? JValue.CreateNull());
                    this.Index++;
                }
                this.Reply(null);
            }
        }
        catch (OpError ex)
        {
            this.Reply(ex.Message);
        }
        catch (Exception ex)
        {
            ModEntry.Log.Log($"Instruction failed:\n{ex}", LogLevel.Error);
            this.Reply($"internal error: {ex.GetType().Name}: {ex.Message}");
        }
    }

    /// <summary>Answer read-only requests straight away, so a planner can look while an action runs.</summary>
    private void ServeReads()
    {
        while (this.Client != null && this.Client.Reads.TryDequeue(out JObject? request))
        {
            var results = new JArray();
            var message = new JObject { ["id"] = request["id"], ["results"] = results };
            try
            {
                foreach (JToken op in request["ops"] as JArray ?? new JArray())
                {
                    string name = (string?)op["op"] ?? "";
                    if (!this.Configured || name is not ("read" or "path"))
                        throw new OpError("only read and path can run beside an action");
                    results.Add(name == "read" ? Reader.Read(op["what"] ?? throw new OpError("read needs 'what'")) : Nav.Path((JObject)op));
                }
            }
            catch (Exception ex)
            {
                message["error"] = ex is OpError ? ex.Message : $"internal error: {ex.GetType().Name}: {ex.Message}";
                message["index"] = results.Count;
            }
            this.Client.Send(message);
        }
    }

    /// <summary>Take the next request, if any.</summary>
    private bool Next()
    {
        if (!this.Client!.Requests.TryDequeue(out JObject? request))
            return false;
        this.Request = request;
        this.Ops = this.Request["ops"] as JArray ?? new JArray();
        this.Results = new JArray();
        this.Index = 0;
        return true;
    }

    private void Reply(string? error)
    {
        JObject? request = this.Request;
        this.Request = null;
        this.Active = null;
        if (request == null || this.Client == null)
            return;

        var message = new JObject { ["id"] = request["id"], ["results"] = this.Results };
        if (error != null)
        {
            message["error"] = error;
            message["index"] = this.Index;
        }
        JArray journal = ModEntry.Journal.Since(this.SeenSeq);
        if (journal.Count > 0)
            message["journal"] = journal;
        this.SeenSeq = ModEntry.Journal.LastSeq;
        if (this.Human)
            message["human"] = true;
        this.Client.Send(message);
    }

    private (JToken?, IActiveOp?) Execute(JObject op)
    {
        string name = (string?)op["op"] ?? "";
        if (name != "attach" && !this.Configured)
            throw new OpError("attach first");
        if (this.Human && name is ("input" or "step" or "follow"))
            throw new OpError("the player has control");

        switch (name)
        {
            case "attach":
                this.TtlMs = Math.Clamp((int?)op["ttl_ms"] ?? this.TtlMs, 2000, 120000);
                Clock.Speed = Math.Clamp((int?)op["speed"] ?? Clock.Speed, 1, 16);
                this.Configured = true;
                return (new JObject { ["mode"] = "realtime", ["speed"] = Clock.Speed, ["human"] = this.Human }, null);

            case "input":
                Devices.Apply(op);
                return (null, null);

            case "step":
                return (null, new StepOp(op));

            case "read":
                return (Reader.Read(op["what"] ?? throw new OpError("read needs 'what'")), null);

            case "frame":
                return (null, new FrameOp(op));

            case "path":
                return (Nav.Path(op), null);

            case "follow":
                return (null, new FollowOp(op));

            case "cheat":
                // the only non-input action: shortcuts the player explicitly allowed
                return (string?)op["what"] switch
                {
                    "catch_fish" => (null, new CatchFishOp()),
                    var what => throw new OpError($"unknown cheat '{what}'")
                };

            default:
                throw new OpError($"unknown instruction '{name}'");
        }
    }
}

/// <summary>Named conditions which end an instruction early.</summary>
internal sealed class Conditions
{
    private readonly List<(string Name, Expr Expr)> List = new();

    public Conditions(JToken? token)
    {
        if (token is JObject obj)
        {
            foreach (var pair in obj)
                this.List.Add((pair.Key, Expr.Parse((string?)pair.Value ?? "")));
        }
        else if (token is { Type: JTokenType.String })
            this.List.Add(("until", Expr.Parse((string)token!)));
    }

    public string? Fired()
    {
        foreach (var (name, expr) in this.List)
        {
            if (Expr.Truthy(expr.Eval(null)))
                return name;
        }
        return null;
    }
}

internal sealed class StepOp : IActiveOp
{
    private readonly int Ticks;
    private readonly Conditions Until;
    private int Ran;

    public StepOp(JObject op)
    {
        this.Ticks = Math.Clamp((int?)op["n"] ?? 1, 1, 36000);
        this.Until = new Conditions(op["until"]);
    }

    public JToken? Poll()
    {
        this.Ran++;
        string? fired = this.Until.Fired();
        return fired != null || this.Ran >= this.Ticks
            ? new JObject { ["ticks"] = this.Ran, ["fired"] = fired }
            : null;
    }
}

/// <summary>Wins the fishing minigame through the game's own catch path, so the fish, quality, experience, and treasure are as normal.</summary>
internal sealed class CatchFishOp : IActiveOp
{
    private int Ticks;

    public CatchFishOp()
    {
        if (Game1.activeClickableMenu is not StardewValley.Menus.BobberBar)
            throw new OpError("the fishing minigame isn't open");
    }

    public JToken? Poll()
    {
        if (Game1.activeClickableMenu is StardewValley.Menus.BobberBar bar && !bar.fadeOut && ++this.Ticks < 300)
        {
            // the meter can drain within a tick, so hold it full until the game registers the catch
            bar.distanceFromCatching = 1f;
            return null;
        }
        return new JObject { ["ticks"] = this.Ticks };
    }
}
