using System;
using System.IO;
using System.IO.Compression;
using System.Threading.Tasks;
using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Newtonsoft.Json.Linq;
using StardewValley;

namespace StardewBridge;

/// <summary>Captures the final frame before it's presented.</summary>
internal static class Frames
{
    public static FrameOp? Pending;

    public static void Patch(Harmony harmony)
    {
        harmony.Patch(AccessTools.Method(typeof(GameRunner), "Draw"), postfix: new HarmonyMethod(typeof(Frames), nameof(Draw_Postfix)));
    }

    private static void Draw_Postfix()
    {
        FrameOp? op = Pending;
        Pending = null;
        op?.Capture();
    }
}

internal sealed class FrameOp : IActiveOp
{
    private readonly int Scale;
    private volatile JToken? Result;

    public FrameOp(JObject op)
    {
        this.Scale = Math.Clamp((int?)op["shrink"] ?? 1, 1, 8);
        Frames.Pending = this;
    }

    public JToken? Poll() => this.Result;

    public void Capture()
    {
        try
        {
            GraphicsDevice device = Game1.graphics.GraphicsDevice;
            int width = device.PresentationParameters.BackBufferWidth, height = device.PresentationParameters.BackBufferHeight;
            var pixels = new Color[width * height];
            device.GetBackBufferData(pixels);
            var info = new JObject
            {
                ["viewport"] = new JArray(Game1.viewport.X, Game1.viewport.Y, Game1.viewport.Width, Game1.viewport.Height),
                ["zoom"] = Game1.options.zoomLevel,
                ["ui_scale"] = Game1.options.uiScale
            };
            // pack and compress off the game thread
            Task.Run(() =>
            {
                try
                {
                    info["width"] = width / this.Scale;
                    info["height"] = height / this.Scale;
                    info["rgb"] = this.Pack(pixels, width, height);
                    this.Result = info;
                }
                catch (Exception ex)
                {
                    this.Result = new JObject { ["error"] = ex.Message };
                }
            });
        }
        catch (Exception ex)
        {
            this.Result = new JObject { ["error"] = ex.Message };
        }
    }

    private string Pack(Color[] pixels, int width, int height)
    {
        int s = this.Scale, w = width / s, h = height / s;
        byte[] rgb = new byte[w * h * 3];
        int i = 0;
        for (int y = 0; y < h; y++)
        {
            for (int x = 0; x < w; x++)
            {
                int r = 0, g = 0, b = 0;
                for (int dy = 0; dy < s; dy++)
                {
                    for (int dx = 0; dx < s; dx++)
                    {
                        Color color = pixels[(y * s + dy) * width + x * s + dx];
                        r += color.R;
                        g += color.G;
                        b += color.B;
                    }
                }
                rgb[i++] = (byte)(r / (s * s));
                rgb[i++] = (byte)(g / (s * s));
                rgb[i++] = (byte)(b / (s * s));
            }
        }
        using var stream = new MemoryStream();
        using (var zlib = new ZLibStream(stream, CompressionLevel.Fastest))
            zlib.Write(rgb);
        return Convert.ToBase64String(stream.ToArray());
    }
}
