using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Input;
using Newtonsoft.Json.Linq;
using StardewValley;
using StardewValley.Buildings;
using StardewValley.Locations;

namespace StardewBridge;

/// <summary>Tile maps and pathfinding, using the game's own collision rules.</summary>
internal static class Nav
{
    private static readonly Point[] Neighbors = { new(0, -1), new(1, 0), new(0, 1), new(-1, 0), new(-1, -1), new(1, -1), new(-1, 1), new(1, 1) };
    private static readonly HashSet<string> AvoidTouchActions = new() { "Sleep", "MagicWarp", "Warp" };

    /// <summary>The locker room the player may not enter (its tiles push them back out).</summary>
    private static string WrongLocker => Game1.player.IsMale ? "WomensLocker" : "MensLocker";

    private static bool InMap(GameLocation location, Point tile)
    {
        var layer = location.Map.Layers[0];
        return tile.X >= 0 && tile.Y >= 0 && tile.X < layer.LayerWidth && tile.Y < layer.LayerHeight;
    }

    /// <summary>Whether the player could stand on a tile, ignoring characters (who move).</summary>
    public static bool Passable(GameLocation location, Point tile, bool avoidTriggers)
    {
        if (!InMap(location, tile))
            return false;
        if (avoidTriggers)
        {
            foreach (Warp warp in location.warps)
            {
                if (warp.X == tile.X && warp.Y == tile.Y && !warp.npcOnly.Value)
                    return false;
            }
            string? touch = location.doesTileHaveProperty(tile.X, tile.Y, "TouchAction", "Back");
            string? kind = touch?.Split(' ')[0];
            if (kind != null && (AvoidTouchActions.Contains(kind) || kind == WrongLocker))
                return false;
        }
        var bounds = new Rectangle(tile.X * 64 + 8, tile.Y * 64 + 16, 48, 32);
        return !location.isCollidingPosition(bounds, Game1.viewport, true, 0, false, Game1.player, pathfinding: true, skipCollisionEffects: true)
            || IsClosedDoor(location, tile);
    }

    public static bool IsClosedDoor(GameLocation location, Point tile)
    {
        if (location.interiorDoors.TryGetValue(tile, out bool open) && !open)
            return true;
        return location.objects.TryGetValue(tile.ToVector2(), out var obj) && obj is Fence fence && fence.isGate.Value && fence.gatePosition.Value == 0;
    }

    /// <summary>Whether basic tools can clear whatever's on a tile.</summary>
    private static bool IsDebris(GameLocation location, Point tile)
    {
        foreach (var clump in location.resourceClumps)
        {
            if (clump.occupiesTile(tile.X, tile.Y) && clump.parentSheetIndex.Value is 752 or 754 or 756 or 758)
                return true; // mine boulders
        }
        return location.objects.TryGetValue(tile.ToVector2(), out var obj)
            ? obj.IsWeeds() || obj.IsTwig() || obj.IsBreakableStone()
            : location.terrainFeatures.TryGetValue(tile.ToVector2(), out var feature) && feature is StardewValley.TerrainFeatures.Tree;
    }

    /****
    ** Instructions
    ****/
    private static GameLocation GetLocation(JToken? name)
    {
        if (name is { Type: JTokenType.String })
            return Game1.getLocationFromName((string)name!) ?? throw new OpError($"no location '{name}'");
        return Game1.currentLocation ?? throw new OpError("no location is loaded");
    }

    private static Point ParseTile(JToken token) => token is JArray { Count: 2 } at ? new Point((int)at[0], (int)at[1]) : throw new OpError("expected a tile as [x, y]");

    private static HashSet<Point> ParseTiles(JToken? token) => (token as JArray)?.Select(ParseTile).ToHashSet() ?? new HashSet<Point>();

    /// <summary>Who's in the way: people, other players, animals, and monsters, with the tiles they cover.</summary>
    public static IEnumerable<(string Name, Rectangle Box, bool Monster)> Characters(GameLocation location)
    {
        foreach (NPC npc in location.characters)
        {
            if (npc.IsInvisible || npc is StardewValley.Monsters.Monster { Health: <= 0 })
                continue;
            yield return (npc.displayName ?? npc.Name, npc.GetBoundingBox(), npc is StardewValley.Monsters.Monster);
        }
        foreach (Farmer other in location.farmers)
        {
            if (other != Game1.player)
                yield return (other.Name, other.GetBoundingBox(), false);
        }
        foreach (var animal in location.animals.Values)
            yield return (animal.displayName, animal.GetBoundingBox(), false);
    }

    private static IEnumerable<Point> Covered(Rectangle box)
    {
        for (int x = box.Left / 64; x <= (box.Right - 1) / 64; x++)
            for (int y = box.Top / 64; y <= (box.Bottom - 1) / 64; y++)
                yield return new Point(x, y);
    }

    /// <summary>Find a path to the nearest goal.</summary>
    /// <remarks>Options: goals, near (0 to stand on a goal, 4 or 8 to stand beside it), force (tiles which may be entered regardless, like exits),
    /// blocked, debris (allow paths through clearable debris), location and start (default: here), costs (return the cost to every goal instead),
    /// avoid (default true: go around people and animals, and keep away from monsters).</remarks>
    public static JToken Path(JObject op)
    {
        GameLocation location = GetLocation(op["location"]);
        Point start = op["start"] is JArray from ? ParseTile(from) : Game1.player.TilePoint;
        var goals = ParseTiles(op["goals"]);
        var force = ParseTiles(op["force"]);
        var blocked = ParseTiles(op["blocked"]);
        int near = (int?)op["near"] ?? 0;
        bool debris = (bool?)op["debris"] ?? false;
        bool costs = (bool?)op["costs"] ?? false;
        var danger = new Dictionary<Point, float>();
        if ((bool?)op["avoid"] ?? true)
        {
            foreach (var (_, box, monster) in Characters(location))
            {
                foreach (Point at in Covered(box))
                {
                    if (!monster)
                    {
                        if (!goals.Contains(at) && at != start)
                            blocked.Add(at);
                        continue;
                    }
                    // the closer to a monster, the costlier; never impossible, since it may be the way out
                    for (int dx = -2; dx <= 2; dx++)
                        for (int dy = -2; dy <= 2; dy++)
                        {
                            Point p = new(at.X + dx, at.Y + dy);
                            float extra = 6f / (1 + Math.Max(Math.Abs(dx), Math.Abs(dy)));
                            danger[p] = Math.Max(danger.GetValueOrDefault(p), extra);
                        }
                }
            }
        }
        if (near == 0)
        {
            // a goal which triggers something (like a bed) is fine if that's explicitly where we're going
            foreach (Point goal in goals)
            {
                if (!Passable(location, goal, true) && Passable(location, goal, false))
                    force.Add(goal);
            }
        }

        Point? Target(Point tile)
        {
            if (near == 0)
                return goals.Contains(tile) ? tile : null;
            for (int i = 0; i < near; i++)
            {
                if (goals.Contains(tile + Neighbors[i]))
                    return tile + Neighbors[i];
            }
            return null;
        }

        var cache = new Dictionary<Point, bool>();
        bool CanEnter(Point tile)
        {
            if (!cache.TryGetValue(tile, out bool result))
                cache[tile] = result = !blocked.Contains(tile) && (force.Contains(tile) || Passable(location, tile, true) || (debris && IsDebris(location, tile)));
            return result;
        }

        var cost = new Dictionary<Point, float> { [start] = 0 };
        var previous = new Dictionary<Point, Point>();
        var settled = new HashSet<Point>();
        var queue = new PriorityQueue<Point, float>();
        queue.Enqueue(start, 0);
        var found = new Dictionary<Point, float>();
        while (queue.TryDequeue(out Point tile, out _))
        {
            if (!settled.Add(tile))
                continue;
            if (costs)
            {
                for (int i = -1; i < near; i++)
                {
                    Point goal = i < 0 ? tile : tile + Neighbors[i];
                    if ((i >= 0 || near == 0) && goals.Contains(goal))
                        found.TryAdd(goal, cost[tile]);
                }
                if (found.Count == goals.Count)
                    break;
            }
            else if (Target(tile) is { } target)
            {
                var path = new List<Point> { tile };
                while (previous.TryGetValue(path[^1], out Point back))
                    path.Add(back);
                path.Reverse();
                return new JObject
                {
                    ["path"] = new JArray(path.Select(p => new JArray(p.X, p.Y))),
                    ["target"] = new JArray(target.X, target.Y),
                    ["cost"] = cost[tile],
                    ["debris"] = debris ? new JArray(path.Where(p => !Passable(location, p, false) && !force.Contains(p) && p != start).Select(p => new JArray(p.X, p.Y))) : null
                };
            }
            if (tile != start && force.Contains(tile) && !Passable(location, tile, true))
                continue; // never walk through an exit to get elsewhere

            for (int i = 0; i < 8; i++)
            {
                Point next = tile + Neighbors[i];
                if (settled.Contains(next) || !CanEnter(next))
                    continue;
                float step = 1;
                bool isDebris = debris && !Passable(location, next, true) && !force.Contains(next);
                if (isDebris)
                    step += 5;
                if (i >= 4)
                {
                    // only cut corners when both sides are open ground
                    Point a = new(tile.X + Neighbors[i].X, tile.Y), b = new(tile.X, tile.Y + Neighbors[i].Y);
                    if (isDebris || !Passable(location, a, true) || !Passable(location, b, true) || blocked.Contains(a) || blocked.Contains(b)
                        || IsClosedDoor(location, a) || IsClosedDoor(location, b) || IsClosedDoor(location, next) || IsClosedDoor(location, tile))
                        continue;
                    step = 1.4143f;
                }
                float total = cost[tile] + step + danger.GetValueOrDefault(next);
                if (!cost.TryGetValue(next, out float known) || total < known)
                {
                    cost[next] = total;
                    previous[next] = tile;
                    queue.Enqueue(next, total);
                }
            }
        }
        if (costs)
            return new JArray(goals.Select(p => found.TryGetValue(p, out float value) ? new JArray(p.X, p.Y, value) : new JArray(p.X, p.Y, JValue.CreateNull())));
        return JValue.CreateNull();
    }

    /// <summary>Get tile layers as rows of characters.</summary>
    public static JToken Grid(JObject query)
    {
        GameLocation location = GetLocation(query["location"]);
        var layer = location.Map.Layers[0];
        Rectangle rect = query["rect"] is JArray { Count: 4 } r ? new((int)r[0], (int)r[1], (int)r[2], (int)r[3]) : new(0, 0, layer.LayerWidth, layer.LayerHeight);
        rect = Rectangle.Intersect(rect, new(0, 0, layer.LayerWidth, layer.LayerHeight));
        var result = new JObject { ["x"] = rect.X, ["y"] = rect.Y, ["w"] = rect.Width, ["h"] = rect.Height };
        foreach (string name in (query["layers"] as JArray)?.Select(p => (string)p!) ?? new[] { "pass" })
        {
            var rows = new JArray();
            for (int y = rect.Top; y < rect.Bottom; y++)
            {
                var row = new StringBuilder(rect.Width);
                for (int x = rect.Left; x < rect.Right; x++)
                {
                    var tile = new Point(x, y);
                    row.Append(name switch
                    {
                        "pass" => IsClosedDoor(location, tile) ? 'd' : Passable(location, tile, false) ? '.' : '#',
                        "water" => location.isWaterTile(x, y) ? '1' : '0',
                        "fish" => location.isTileFishable(x, y) ? '1' : '0',
                        "refill" => location.CanRefillWateringCanOnTile(x, y) ? '1' : '0',
                        "dig" => location.doesTileHaveProperty(x, y, "Diggable", "Back") != null ? '1' : '0',
                        "touch" => location.doesTileHaveProperty(x, y, "TouchAction", "Back") is { Length: > 0 } t ? t[0] : '.',
                        _ => throw new OpError($"unknown layer '{name}'")
                    });
                }
                rows.Add(row.ToString());
            }
            result[name] = rows;
        }
        return result;
    }

    /****
    ** Tables
    ****/
    public static List<Row> Exits(GameLocation location)
    {
        var rows = new List<Row>();
        foreach (Warp warp in location.warps)
        {
            if (!warp.npcOnly.Value)
                rows.Add(new Row { ["x"] = warp.X, ["y"] = warp.Y, ["door"] = false, ["to"] = warp.TargetName, ["ax"] = warp.TargetX, ["ay"] = warp.TargetY });
        }
        void AddDoor(Point door)
        {
            Warp? warp = null;
            try
            {
                warp = location.getWarpFromDoor(door, Game1.player);
            }
            catch
            {
                // ignore invalid door
            }
            if (warp != null && rows.All(p => (int)p["x"]! != door.X || (int)p["y"]! != door.Y))
                rows.Add(new Row { ["x"] = door.X, ["y"] = door.Y, ["door"] = true, ["to"] = warp.TargetName, ["ax"] = warp.TargetX, ["ay"] = warp.TargetY });
        }
        foreach (Point door in location.doors.Keys.ToArray())
            AddDoor(door);
        foreach (Building building in location.buildings)
        {
            if (building.HasIndoors() && building.daysOfConstructionLeft.Value <= 0)
                AddDoor(building.getPointForHumanDoor());
        }
        return rows;
    }

    private static IEnumerable<Point> Tiles(GameLocation location)
    {
        var layer = location.Map.Layers[0];
        for (int y = 0; y < layer.LayerHeight; y++)
        {
            for (int x = 0; x < layer.LayerWidth; x++)
                yield return new Point(x, y);
        }
    }

    public static List<Row> Actions(GameLocation location)
    {
        var rows = new List<Row>();
        foreach (Point tile in Tiles(location))
        {
            string? action = location.doesTileHaveProperty(tile.X, tile.Y, "Action", "Buildings");
            if (!string.IsNullOrWhiteSpace(action) && !action.StartsWith("None"))
                rows.Add(new Row { ["x"] = tile.X, ["y"] = tile.Y, ["action"] = action });
        }
        return rows;
    }

    public static List<Row> Ladders(GameLocation location)
    {
        var rows = new List<Row>();
        if (location is not MineShaft)
            return rows;
        foreach (Point tile in Tiles(location))
        {
            string? kind = location.getTileIndexAt(tile.X, tile.Y, "Buildings", "mine") switch { 115 => "up", 173 => "down", 174 => "shaft", 112 => "elevator", _ => null };
            if (kind != null)
                rows.Add(new Row { ["x"] = tile.X, ["y"] = tile.Y, ["kind"] = kind });
        }
        return rows;
    }
}

/// <summary>Walks a path by holding movement keys, tick by tick.</summary>
/// <remarks>Options: path, until, n (max ticks), stop_for ("all" by default, "people" to keep going past monsters, or "none"): who on the next path tiles stops the walk as "blocked".</remarks>
internal sealed class FollowOp : IActiveOp
{
    // the player may have rebound these
    private readonly Keys Up = Game1.options.moveUpButton[0].key, Left = Game1.options.moveLeftButton[0].key, Down = Game1.options.moveDownButton[0].key, Right = Game1.options.moveRightButton[0].key;
    private readonly Keys[] Movement;
    private readonly GameLocation Location;
    private readonly List<Point> Path;
    private readonly Conditions Until;
    private readonly int MaxTicks;
    private readonly string StopFor;
    private int Index = 1;
    private int Ticks;
    private int StuckTicks;
    private Vector2 LastPosition;

    public FollowOp(JObject op)
    {
        this.Location = Game1.currentLocation ?? throw new OpError("no location is loaded");
        this.Path = (op["path"] as JArray)?.Select(p => new Point((int)p[0]!, (int)p[1]!)).ToList() ?? throw new OpError("follow needs a path");
        this.Until = new Conditions(op["until"]);
        this.MaxTicks = Math.Clamp((int?)op["n"] ?? 3600, 1, 36000);
        this.StopFor = (string?)op["stop_for"] ?? "all";
        if (this.StopFor is not ("all" or "people" or "none"))
            throw new OpError("stop_for must be 'all', 'people', or 'none'");
        this.Movement = new[] { this.Up, this.Left, this.Down, this.Right };
        this.LastPosition = Game1.player.Position;
        this.Steer();
    }

    public JToken? Poll()
    {
        this.Ticks++;
        if (Game1.currentLocation != this.Location || Game1.locationRequest != null)
            return this.Stop("warped", null);
        if (Game1.activeClickableMenu != null || Views.InCutscene || Game1.currentMinigame != null)
            return this.Stop("interrupted", null);
        if (this.Until.Fired() is { } fired)
            return this.Stop("until", fired);
        if (this.Ticks >= this.MaxTicks)
            return this.Stop("timeout", null);

        string? ended = this.Steer();
        if (ended != null)
            return this.Stop(ended, null);
        if (this.StopFor != "none" && this.Ticks % 4 == 0 && this.InTheWay() is { } who)
            return this.Blocked(who); // someone stepped into the path: stop, so the caller can plan around them

        if (Game1.player.CanMove && !Game1.fadeToBlack)
        {
            if (Vector2.Distance(Game1.player.Position, this.LastPosition) < 0.5f)
            {
                if (++this.StuckTicks > 35)
                    return this.Touching() is { } pressed ? this.Blocked(pressed) : this.Stop("stuck", null);
            }
            else
                this.StuckTicks = 0;
            this.LastPosition = Game1.player.Position;
        }
        return null;
    }

    private (string Name, bool Monster)? InTheWay()
    {
        Rectangle me = Game1.player.GetBoundingBox();
        for (int i = this.Index; i < Math.Min(this.Index + 2, this.Path.Count); i++)
        {
            Point tile = this.Path[i];
            var area = new Rectangle(tile.X * 64 + 8, tile.Y * 64 + 8, 48, 48);
            foreach (var (name, box, monster) in Nav.Characters(this.Location))
            {
                if (box.Intersects(area) && !box.Intersects(me) && !(monster && this.StopFor == "people"))
                    return (name, monster);
            }
        }
        return null;
    }

    /// <summary>Someone pressed against the farmer, which InTheWay skips: a villager we block pushes through after a while, and we push into them.</summary>
    private (string Name, bool Monster)? Touching()
    {
        Rectangle me = Game1.player.GetBoundingBox();
        me.Inflate(4, 4);
        foreach (var (name, box, monster) in Nav.Characters(this.Location))
        {
            if (this.StopFor != "none" && box.Intersects(me) && !(monster && this.StopFor == "people"))
                return (name, monster);
        }
        return null;
    }

    private JObject Blocked((string Name, bool Monster) who)
    {
        JObject stop = this.Stop("blocked", null);
        stop["by"] = who.Name;
        stop["monster"] = who.Monster;
        return stop;
    }

    private JObject Stop(string status, string? fired)
    {
        Devices.SetKeys(this.Movement, Array.Empty<Keys>());
        return new JObject
        {
            ["status"] = status,
            ["fired"] = fired,
            ["ticks"] = this.Ticks,
            ["at"] = this.Index < this.Path.Count ? new JArray(this.Path[this.Index].X, this.Path[this.Index].Y) : null
        };
    }

    private string? Steer()
    {
        Farmer player = Game1.player;
        while (this.Index < this.Path.Count)
        {
            Point target = this.Path[this.Index];
            if (Nav.IsClosedDoor(this.Location, target))
                return "door";
            Rectangle box = player.GetBoundingBox();
            int dx = target.X * 64 + 32 - box.Center.X, dy = target.Y * 64 + 32 - box.Center.Y;
            int tolerance = Math.Max(5, (int)Math.Ceiling(player.getMovementSpeed() * 0.75f));
            if (Math.Abs(dx) <= tolerance && Math.Abs(dy) <= tolerance)
            {
                this.Index++;
                this.StuckTicks = 0;
                continue;
            }
            var keys = new List<Keys>(2);
            if (dx < -tolerance)
                keys.Add(this.Left);
            else if (dx > tolerance)
                keys.Add(this.Right);
            if (dy < -tolerance)
                keys.Add(this.Up);
            else if (dy > tolerance)
                keys.Add(this.Down);
            Devices.SetKeys(this.Movement, keys);
            return null;
        }
        return "arrived";
    }
}
