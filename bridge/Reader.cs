using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Microsoft.Xna.Framework;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewValley;

namespace StardewBridge;

/// <summary>Implements the read instruction.</summary>
internal static class Reader
{
    public static JToken Read(JToken what)
    {
        switch (what)
        {
            case JArray list:
                return new JArray(list.Select(Read));
            case JObject query:
                if (query["$grid"] is JObject grid)
                    return Nav.Grid(grid);
                if (query["$data"] is { } asset)
                    return Data((string)asset!, (string?)query["key"]);
                if (query["$item"] is { } item)
                    return FindItem((string)item!);
                if (query["from"] is { Type: JTokenType.String } from)
                    return Query((string)from!, query);
                // an object of named reads
                var result = new JObject();
                foreach (var pair in query)
                    result[pair.Key] = Read(pair.Value!);
                return result;
            default:
                return ToJson(Expr.Parse((string?)what ?? "").Eval(null), 0);
        }
    }

    private static JToken Query(string from, JObject query)
    {
        IEnumerable rows = Members.Enumerate(Expr.Parse(from).Eval(null)) ?? Array.Empty<object>();
        Expr? where = query["where"] is { } filter ? Expr.Parse((string)filter!) : null;
        var select = (query["select"] as JArray)?.Select(p => (Name: (string)p!, Expr: Expr.Parse((string)p!))).ToArray();
        int limit = Math.Clamp((int?)query["limit"] ?? 1000, 1, 20000);

        IEnumerable<object> matches = rows.Cast<object?>().Select(Members.Unwrap).Where(p => p != null && (where == null || Expr.Truthy(where.Eval(p))))!;
        if (query["near"] is JArray { Count: 2 } near)
        {
            double x = (double)near[0], y = (double)near[1];
            double Distance(object row)
            {
                Members.TryGet(row, "x", out object? rx);
                Members.TryGet(row, "y", out object? ry);
                return Expr.TryNumber(rx, out double dx) && Expr.TryNumber(ry, out double dy) ? Math.Abs(dx - x) + Math.Abs(dy - y) : double.MaxValue;
            }
            matches = matches.OrderBy(Distance);
        }

        var result = new JArray();
        foreach (object row in matches.Take(limit))
        {
            if (select == null)
                result.Add(ToJson(row, 0));
            else
            {
                var entry = new JObject();
                foreach (var (name, expr) in select)
                    entry[name] = ToJson(expr.Eval(row), 1);
                result.Add(entry);
            }
        }
        return result;
    }

    private static JToken Data(string asset, string? key)
    {
        MethodInfo method = typeof(DataLoader).GetMethods(BindingFlags.Public | BindingFlags.Static)
            .FirstOrDefault(p => string.Equals(p.Name, asset, StringComparison.OrdinalIgnoreCase) && p.GetParameters().Length == 1)
            ?? throw new OpError($"unknown data asset '{asset}'");
        object? data = method.Invoke(null, new object[] { Game1.content });
        return key == null ? ToJson(data, 0) : ToJson(Members.Index(data, key), 0);
    }

    private static JToken FindItem(string query)
    {
        var data = ItemRegistry.GetData(query)
            ?? ItemRegistry.ItemTypes.SelectMany(p => p.GetAllIds().Select(p.GetData)).FirstOrDefault(p => string.Equals(p?.DisplayName, query, StringComparison.OrdinalIgnoreCase));
        if (data == null)
            return JValue.CreateNull();
        Item item = ItemRegistry.Create(data.QualifiedItemId);
        var row = Views.ItemRow(item);
        row["description"] = item.getDescription().Replace("\r", "").Replace("\n", " ").Trim();
        row["category_name"] = item.getCategoryName();
        row["crop"] = Game1.cropData.TryGetValue(data.ItemId, out var crop) ? crop : null;
        return ToJson(row, 0);
    }

    /// <summary>Convert a value for the client. Unrecognized objects become their type name, to be read by path instead.</summary>
    public static JToken ToJson(object? value, int depth)
    {
        value = Members.Unwrap(value);
        if (value is Func<object?> lazy)
            value = lazy();
        switch (value)
        {
            case null:
                return JValue.CreateNull();
            case string or bool or int or long or short or byte or uint or ulong or ushort or sbyte:
                return new JValue(value);
            case float f:
                return new JValue(float.IsFinite(f) ? Math.Round(f, 3) : 0);
            case double d:
                return new JValue(double.IsFinite(d) ? Math.Round(d, 3) : 0);
            case Enum e:
                return new JValue(e.ToString());
            case JToken token:
                return token;
            case Vector2 v:
                return new JArray(Math.Round(v.X, 2), Math.Round(v.Y, 2));
            case Point p:
                return new JArray(p.X, p.Y);
            case Rectangle r:
                return new JArray(r.X, r.Y, r.Width, r.Height);
            case Row row:
                var obj = new JObject();
                foreach (var pair in row)
                {
                    if (pair.Key != "raw" && pair.Value != null)
                        obj[pair.Key] = ToJson(pair.Value, depth + 1);
                }
                return obj;
            case Item item:
                return ToJson(Views.ItemRow(item), depth);
        }
        if (depth > 4)
            return new JValue($"<{value.GetType().Name}>");
        if (value is IDictionary map && (map.Count == 0 || map.Keys.Cast<object>().First() is string or int))
        {
            var obj = new JObject();
            foreach (object key in map.Keys)
                obj[key.ToString()!] = ToJson(map[key], depth + 1);
            return obj;
        }
        if (Members.Enumerate(value) is { } list)
            return new JArray(list.Cast<object?>().Take(2000).Select(p => ToJson(p, depth + 1)));
        if (value.GetType().Namespace?.StartsWith("StardewValley.GameData") == true)
        {
            var obj = new JObject();
            foreach (PropertyInfo property in value.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                if (property.CanRead && property.GetIndexParameters().Length == 0)
                    obj[property.Name] = ToJson(property.GetValue(value), depth + 1);
            }
            foreach (FieldInfo field in value.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
                obj[field.Name] = ToJson(field.GetValue(value), depth + 1);
            return obj;
        }
        return new JValue($"<{value.GetType().Name}>");
    }
}

/// <summary>The names an expression can start from.</summary>
internal static class Roots
{
    private static readonly StaticType GameType = new(typeof(Game1));
    private static readonly LocationIndex LocationIndex = new();

    public static bool TryGet(string name, out object? value)
    {
        switch (name)
        {
            case "game": value = GameType; return true;
            case "player": value = Game1.player; return true;
            case "location": value = Context.IsWorldReady ? Game1.currentLocation : null; return true;
            case "locations": value = LocationIndex; return true;
            case "time": value = Views.Time(); return true;
            case "state": value = Views.State(); return true;
            case "screen": value = Views.Screen(); return true;
            case "menu": value = Views.Menu(); return true;
            case "event": value = Game1.CurrentEvent; return true;
            case "minigame": value = Game1.currentMinigame; return true;
            case "options": value = Game1.options; return true;
            case "saves": value = Saves(); return true;
            default: value = null; return false;
        }
    }

    private static List<Row> Saves()
    {
        var list = new List<Row>();
        if (!Directory.Exists(Constants.SavesPath))
            return list;
        foreach (string path in Directory.GetDirectories(Constants.SavesPath))
        {
            string info = Path.Combine(path, "SaveGameInfo");
            if (!File.Exists(info))
                continue;
            string xml = File.ReadAllText(info);
            string Get(string field) => System.Text.RegularExpressions.Regex.Match(xml, $"<{field}>([^<]*)</{field}>").Groups[1].Value;
            list.Add(new Row { ["folder"] = Path.GetFileName(path), ["name"] = Get("name"), ["farm"] = Get("farmName"), ["season"] = Get("seasonForSaveGame"), ["day"] = Get("dayOfMonthForSaveGame"), ["year"] = Get("yearForSaveGame"), ["money"] = Get("money") });
        }
        return list;
    }
}
