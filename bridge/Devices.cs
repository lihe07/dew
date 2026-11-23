using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Input;
using Newtonsoft.Json.Linq;
using StardewValley;
using StardewValley.Menus;

namespace StardewBridge;

/// <summary>A virtual keyboard and mouse. They replace the real devices while the agent has control; the real cursor never moves.</summary>
internal static class Devices
{
    private enum Space { Screen, Ui, Tile }

    private static readonly HashSet<Keys> KeysDown = new();
    private static bool LeftDown, RightDown, MiddleDown;
    private static int Wheel;
    private static Space CursorSpace = Space.Screen;
    private static Vector2 Cursor = new(1, 1);

    private static bool Live => ModEntry.Machine.Controls;

    private static void ReleaseButtons()
    {
        KeysDown.Clear();
        LeftDown = RightDown = MiddleDown = false;
    }

    public static void Release()
    {
        ReleaseButtons();
        CursorSpace = Space.Screen;
        Cursor = new Vector2(1, 1);
    }

    public static void SetKeys(IEnumerable<Keys> remove, IEnumerable<Keys> add)
    {
        foreach (Keys key in remove)
            KeysDown.Remove(key);
        foreach (Keys key in add)
            KeysDown.Add(key);
    }

    /// <summary>Apply an input instruction. Omitted fields keep their state.</summary>
    public static void Apply(JObject op)
    {
        if (op["buttons"] is JArray buttons)
        {
            ReleaseButtons();
            foreach (string name in buttons.Select(p => (string?)p ?? ""))
            {
                switch (name.ToLowerInvariant())
                {
                    case "mouse_left": LeftDown = true; break;
                    case "mouse_right": RightDown = true; break;
                    case "mouse_middle": MiddleDown = true; break;
                    default:
                        if (!Enum.TryParse(name, true, out Keys key))
                            throw new OpError($"unknown button '{name}'");
                        KeysDown.Add(key);
                        break;
                }
            }
        }
        if (op["cursor"] is JObject cursor)
        {
            foreach (var (key, space) in new[] { ("tile", Space.Tile), ("ui", Space.Ui), ("screen", Space.Screen) })
            {
                if (cursor[key] is JArray { Count: 2 } at)
                {
                    CursorSpace = space;
                    Cursor = new Vector2((float)at[0], (float)at[1]);
                }
            }
        }
        if (op["scroll"] is { Type: JTokenType.Integer } scroll)
            Wheel += (int)scroll * 120;
        if (op["text"] is { Type: JTokenType.String } text)
        {
            var target = Game1.keyboardDispatcher.Subscriber ?? throw new OpError("no text box is focused; click it first");
            if (target is TextBox box && (bool?)op["clear"] == true)
                box.Text = "";
            target.RecieveTextInput((string)text!);
        }
    }

    private static Point ScreenCursor()
    {
        switch (CursorSpace)
        {
            case Space.Tile:
                // recomputed on each read, since the camera moves
                return new Point((int)((Cursor.X * 64 + 32 - Game1.viewport.X) * Game1.options.zoomLevel), (int)((Cursor.Y * 64 + 32 - Game1.viewport.Y) * Game1.options.zoomLevel));
            case Space.Ui:
                return new Point((int)(Cursor.X * Game1.options.uiScale), (int)(Cursor.Y * Game1.options.uiScale));
            default:
                return Cursor.ToPoint();
        }
    }

    /****
    ** Patches
    ****/
    public static void Patch(Harmony harmony)
    {
        Type input = Game1.input.GetType();
        HarmonyMethod Method(string name) => new(typeof(Devices), name);
        harmony.Patch(AccessTools.Method(input, "GetMouseState"), postfix: Method(nameof(Mouse)));
        harmony.Patch(AccessTools.Method(input, "GetKeyboardState"), postfix: Method(nameof(Keyboard)));
        harmony.Patch(AccessTools.Method(input, "GetGamePadState"), postfix: Method(nameof(Pad)));

        // the game ignores input when its window is inactive
        harmony.Patch(AccessTools.PropertyGetter(typeof(Game1), "IsActiveNoOverlay"), postfix: Method(nameof(WindowActive)));

        // when pointing at a tile, don't click the HUD in front of it
        foreach (Type type in new[] { typeof(IClickableMenu), typeof(Toolbar), typeof(DayTimeMoneyBox), typeof(BuffsDisplay) })
        {
            if (AccessTools.DeclaredMethod(type, "isWithinBounds") is { } method)
                harmony.Patch(method, postfix: Method(nameof(HudBounds)));
        }
    }

    private static void Mouse(ref MouseState __result)
    {
        if (!Live)
            return;
        Point at = ScreenCursor();
        static ButtonState State(bool down) => down ? ButtonState.Pressed : ButtonState.Released;
        __result = new MouseState(at.X, at.Y, Wheel, State(LeftDown), State(MiddleDown), State(RightDown), ButtonState.Released, ButtonState.Released);
    }

    private static void Keyboard(ref KeyboardState __result)
    {
        if (Live)
            __result = new KeyboardState(KeysDown.ToArray());
    }

    private static void Pad(ref GamePadState __result)
    {
        if (Live)
            __result = default;
    }

    private static void WindowActive(ref bool __result)
    {
        if (Live)
            __result = true;
    }

    private static void HudBounds(IClickableMenu __instance, ref bool __result)
    {
        if (__result && Live && CursorSpace == Space.Tile && Game1.onScreenMenus.Contains(__instance))
            __result = false;
    }
}
