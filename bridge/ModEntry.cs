using System;
using System.IO;
using System.Security.Cryptography;
using HarmonyLib;
using Microsoft.Xna.Framework;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewValley;

namespace StardewBridge;

public sealed class ModConfig
{
    /// <summary>The loopback port to listen on.</summary>
    public int Port { get; set; } = 8737;

    /// <summary>Takes control from the agent, or gives it back.</summary>
    public SButton TakeoverKey { get; set; } = SButton.F8;
}

internal sealed class ModEntry : Mod
{
    internal static IMonitor Log = null!;
    internal static Journal Journal = null!;
    internal static Machine Machine = null!;

    public override void Entry(IModHelper helper)
    {
        Log = this.Monitor;
        var config = helper.ReadConfig<ModConfig>();
        Journal = new Journal(helper);
        Machine = new Machine();

        int port = int.TryParse(Environment.GetEnvironmentVariable("STARDEW_BRIDGE_PORT"), out int value) ? value : config.Port;
        string token = Convert.ToHexString(RandomNumberGenerator.GetBytes(16));
        try
        {
            new Server(Machine, port, token).Start();
        }
        catch (Exception ex)
        {
            this.Monitor.Log($"Can't listen on port {port}: {ex.Message}. Is another game instance running?", LogLevel.Error);
            return;
        }
        // the client finds the port and token here, so only this user's programs can attach
        string session = Path.Combine(helper.DirectoryPath, "session.json");
        File.Delete(session);
        using (var stream = new FileStream(session, FileMode.CreateNew, FileAccess.Write))
        {
            if (!OperatingSystem.IsWindows())
                Chmod(session, 0x180); // 0600
            using var writer = new StreamWriter(stream);
            writer.Write(new JObject { ["port"] = port, ["token"] = token, ["version"] = this.ModManifest.Version.ToString() });
        }

        var harmony = new Harmony(this.ModManifest.UniqueID);
        Devices.Patch(harmony);
        Frames.Patch(harmony);

        helper.Events.GameLoop.GameLaunched += (_, _) => Clock.PatchSpeed(harmony);
        helper.Events.GameLoop.UpdateTicking += (_, _) => Machine.OnTick();
        helper.Events.Input.ButtonPressed += (_, e) =>
        {
            if (e.Button == config.TakeoverKey && Machine.Attached)
                Machine.ToggleHuman();
        };
        this.Monitor.Log($"Listening on 127.0.0.1:{port}. Press {config.TakeoverKey} in-game to take control from the agent.", LogLevel.Info);
    }

    [System.Runtime.InteropServices.DllImport("libc", EntryPoint = "chmod", SetLastError = true)]
    private static extern int Chmod(string path, int mode);
}

/// <summary>Controls how fast the world runs. It never pauses: the agent acts in real time, as in multiplayer.</summary>
internal static class Clock
{
    private static System.Reflection.MethodInfo Update = null!;
    private static bool Running;

    public static int Speed = 1;

    public static bool IsSingleplayer => Game1.multiplayerMode == 0 && !Context.IsMultiplayer;

    public static void PatchSpeed(Harmony harmony)
    {
        // the outermost update, so each extra tick goes through SMAPI normally
        Update = AccessTools.Method(GameRunner.instance.GetType(), "Update", new[] { typeof(GameTime) });
        harmony.Patch(Update, postfix: new HarmonyMethod(typeof(Clock), nameof(Update_Postfix)));
    }

    private static void Update_Postfix(GameRunner __instance, GameTime gameTime)
    {
        if (Running || Speed <= 1 || !IsSingleplayer)
            return;
        Running = true;
        try
        {
            // stay within a time budget so the game still draws
            var clock = System.Diagnostics.Stopwatch.StartNew();
            for (int i = 1; i < Speed && ModEntry.Machine.Controls && IsSingleplayer && clock.ElapsedMilliseconds < 25; i++)
                Update.Invoke(__instance, new object[] { gameTime });
        }
        finally
        {
            Running = false;
        }
    }
}
