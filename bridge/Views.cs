using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.GameData.Crops;
using StardewValley.Locations;
using StardewValley.Menus;
using StardewValley.Monsters;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using SObject = StardewValley.Object;

namespace StardewBridge;

/// <summary>Curated names for common things. Everything else is readable by reflection, so this rarely needs changes.</summary>
internal static class Views
{
    public static bool TryGet(object target, string name, out object? value)
    {
        switch (target)
        {
            case Farmer farmer when FarmerField(farmer, name, out value):
            case GameLocation location when LocationField(location, name, out value):
            case Event ev when EventField(ev, name, out value):
                return true;
            case Item item when name is "name" or "id" or "price" or "energy":
                return ItemRow(item).TryGetValue(name, out value);
        }
        value = null;
        return false;
    }

    /****
    ** Player
    ****/
    private static bool FarmerField(Farmer p, string name, out object? value)
    {
        value = name switch
        {
            "name" => p.displayName,
            "farm" => p.farmName.Value,
            "tile" => p.TilePoint,
            "x" => p.TilePoint.X,
            "y" => p.TilePoint.Y,
            "facing" => p.FacingDirection,
            "energy" => p.Stamina,
            "max_energy" => p.MaxStamina,
            "max_health" => p.maxHealth,
            "slot" => p.CurrentToolIndex,
            "held" => p.CurrentItem is { } item ? ItemRow(item) : null,
            "inventory" => p.Items.Select((entry, i) => (entry, i)).Where(e => e.entry != null).Select(e =>
            {
                Row row = ItemRow(e.entry);
                row["slot"] = e.i;
                return row;
            }).ToList(),
            "free_slots" => p.freeSpotsInInventory(),
            "skills" => new Row { ["farming"] = p.FarmingLevel, ["mining"] = p.MiningLevel, ["foraging"] = p.ForagingLevel, ["fishing"] = p.FishingLevel, ["combat"] = p.CombatLevel },
            "quests" => p.questLog.Where(q => !q.IsHidden()).Select(q => new Row
            {
                ["title"] = q.GetName(),
                ["description"] = q.GetDescription(),
                ["objectives"] = q.GetObjectiveDescriptions(),
                ["complete"] = q.ShouldDisplayAsComplete(),
                ["reward"] = q.HasMoneyReward() ? q.GetMoneyReward() : null,
                ["days_left"] = q.IsTimedQuest() ? q.GetDaysLeft() : null
            }).Concat(p.team.specialOrders.Select(o => new Row { ["title"] = o.GetName(), ["description"] = o.GetDescription(), ["objectives"] = o.GetObjectiveDescriptions(), ["special"] = true })).ToList(),
            "recipes" => p.craftingRecipes.Keys.Select(key => new CraftingRecipe(key, false)).Select(r => new Row
            {
                ["id"] = r.name,
                ["name"] = r.DisplayName,
                ["needs"] = string.Join(", ", r.recipeList.Select(e => $"{e.Value}x {r.getNameFromIndex(e.Key)}")),
                ["craftable"] = r.doesFarmerHaveIngredientsInInventory()
            }).ToList(),
            "friends" => Villagers(p),
            "buff_names" => p.buffs.AppliedBuffs.Values.Select(b => b.displayName ?? b.id).ToList(),
            "mailbox_tile" => p.getMailboxPosition(),
            "bed_tile" => Utility.getHomeOfFarmer(p)?.GetPlayerBed()?.GetBedSpot(),
            "home" => Utility.getHomeOfFarmer(p)?.NameOrUniqueName,
            _ => Missing
        };
        return Found(ref value);
    }

    private static readonly object Missing = new();

    private static bool Found(ref object? value)
    {
        if (!ReferenceEquals(value, Missing))
            return true;
        value = null;
        return false;
    }

    private static List<Row> Villagers(Farmer player)
    {
        var rows = new List<Row>();
        Utility.ForEachVillager(npc =>
        {
            if (!npc.CanSocialize)
                return true;
            player.friendshipData.TryGetValue(npc.Name, out Friendship? friendship);
            rows.Add(new Row
            {
                ["name"] = npc.displayName,
                ["internal"] = npc.Name,
                ["hearts"] = friendship?.Points / 250,
                ["points"] = friendship?.Points,
                ["talked"] = friendship?.TalkedToToday,
                ["gifts"] = friendship?.GiftsThisWeek,
                ["gifted_today"] = friendship?.GiftsToday > 0,
                ["status"] = friendship?.Status.ToString(),
                ["location"] = npc.IsInvisible ? null : npc.currentLocation?.NameOrUniqueName,
                ["x"] = npc.TilePoint.X,
                ["y"] = npc.TilePoint.Y,
                ["birthday"] = string.IsNullOrEmpty(npc.Birthday_Season) ? null : $"{npc.Birthday_Season} {npc.Birthday_Day}"
            });
            return true;
        });
        return rows;
    }

    public static Row ItemRow(Item item)
    {
        var obj = item as SObject;
        return new Row
        {
            ["id"] = item.QualifiedItemId,
            ["name"] = item.DisplayName,
            ["stack"] = item.Stack,
            ["max_stack"] = item.maximumStackSize(),
            ["quality"] = item.Quality,
            ["category"] = item.Category,
            ["kind"] = item is MeleeWeapon weapon ? (weapon.isScythe() ? "Scythe" : "Weapon") : item.GetType().Name,
            ["tool"] = item is Tool,
            ["price"] = obj == null ? null : obj.sellToStorePrice(),
            ["energy"] = obj is { Edibility: > -300 } && obj.Edibility != 0 ? obj.staminaRecoveredOnConsumption() : null,
            ["healing"] = obj is { Edibility: > 0 } ? obj.healthRecoveredOnConsumption() : null,
            ["placeable"] = item.isPlaceable(),
            ["recipe"] = item.IsRecipe ? true : null,
            ["upgrade"] = item is Tool tool ? tool.UpgradeLevel : null,
            ["water"] = item is WateringCan can ? can.WaterLeft : null,
            ["water_max"] = item is WateringCan max ? max.waterCanMax : null,
            ["attachments"] = item is Tool { AttachmentSlotsCount: > 0 } rod ? rod.attachments.Where(p => p != null).Select(p => $"{p.DisplayName} x{p.Stack}").ToList() : null,
            ["shippable"] = Utility.highlightShippableObjects(item),
            ["trashable"] = item.canBeTrashed(),
            ["raw"] = item
        };
    }

    /****
    ** Game state
    ****/
    public static Row Time() => new()
    {
        ["clock"] = Game1.timeOfDay,
        ["minutes"] = Utility.ConvertTimeToMinutes(Game1.timeOfDay),
        ["text"] = Game1.getTimeOfDayString(Game1.timeOfDay),
        ["day"] = Game1.dayOfMonth,
        ["season"] = Game1.season.ToString(),
        ["year"] = Game1.year,
        ["weekday"] = Game1.shortDayNameFromDayOfSeason(Game1.dayOfMonth),
        ["total"] = Game1.Date.TotalDays,
        ["weather"] = Context.IsWorldReady ? Game1.currentLocation?.GetWeather().Weather : null,
        ["tomorrow"] = Game1.weatherForTomorrow,
        ["festival"] = new Func<object?>(() => Utility.isFestivalDay()),
        ["birthday"] = new Func<object?>(() => Utility.getTodaysBirthdayNPC()?.displayName)
    };

    public static bool InCutscene => Game1.eventUp && Game1.CurrentEvent is { isFestival: false, playerControlSequence: false };

    /// <summary>What the player can do right now.</summary>
    public static Row State()
    {
        bool ready = Context.IsWorldReady && Game1.gameMode == 3;
        bool fading = Game1.fadeToBlack || Game1.globalFade || Game1.locationRequest != null;
        bool cutscene = InCutscene;
        bool night = Game1.newDay || Game1.showingEndOfNightStuff || Game1.endOfNightMenus.Count > 0 || Game1.farmEvent != null;
        bool menu = Game1.activeClickableMenu != null;
        return new Row
        {
            ["world"] = ready,
            ["title"] = Game1.activeClickableMenu is TitleMenu,
            ["menu"] = menu,
            ["fading"] = fading,
            ["cutscene"] = cutscene,
            ["festival"] = Game1.eventUp && Game1.CurrentEvent?.isFestival == true,
            ["night"] = night,
            ["minigame"] = Game1.currentMinigame != null,
            ["passed_out"] = ready && (Game1.player.passedOut || Game1.killScreen || Game1.player.health <= 0),
            // can walk and act
            ["free"] = ready && !fading && !cutscene && !night && !menu && Game1.currentMinigame == null && Game1.player.CanMove && !Game1.player.UsingTool
                && !Game1.player.isEating && Game1.player.freezePause <= 0 && Game1.pauseTime <= 0,
            // nothing is animating, so a decision can be made
            ["settled"] = ready && !fading && !night && (menu || Game1.currentMinigame != null || cutscene || (Game1.player.CanMove && !Game1.player.UsingTool && !Game1.player.isEating && Game1.player.freezePause <= 0)),
            ["multiplayer"] = !Clock.IsSingleplayer
        };
    }

    public static Row Screen() => new()
    {
        ["ui"] = new Point(Game1.uiViewport.Width, Game1.uiViewport.Height),
        ["viewport"] = new Rectangle(Game1.viewport.X, Game1.viewport.Y, Game1.viewport.Width, Game1.viewport.Height),
        ["zoom"] = Game1.options.zoomLevel,
        ["ui_scale"] = Game1.options.uiScale
    };

    private static bool EventField(Event e, string name, out object? value)
    {
        value = name switch
        {
            "commands" => e.eventCommands,
            "index" => e.CurrentCommand,
            "actor_rows" => e.actors.Select(NpcRow).ToList(),
            "festival_name" => e.isFestival ? e.FestivalName : null,
            _ => Missing
        };
        return Found(ref value);
    }

    /****
    ** Location
    ****/
    private static bool LocationField(GameLocation l, string name, out object? value)
    {
        value = name switch
        {
            "name" => l.NameOrUniqueName,
            "size" => new Point(l.Map.Layers[0].LayerWidth, l.Map.Layers[0].LayerHeight),
            "mine_level" => (l as MineShaft)?.mineLevel,
            "npcs" => l.characters.Where(p => p is not Monster && !p.IsInvisible).Concat(Game1.currentLocation == l && Game1.CurrentEvent != null ? Game1.CurrentEvent.actors : Enumerable.Empty<NPC>()).Select(NpcRow).ToList(),
            "monsters" => l.characters.OfType<Monster>().Where(p => p.Health > 0).Select(p => new Row { ["name"] = p.displayName, ["x"] = p.TilePoint.X, ["y"] = p.TilePoint.Y, ["px"] = p.StandingPixel.X, ["py"] = p.StandingPixel.Y, ["hp"] = p.Health, ["damage"] = p.DamageToFarmer, ["hidden"] = p.IsInvisible, ["raw"] = p }).ToList(),
            "animal_rows" => l.animals.Values.Select(p => new Row { ["name"] = p.displayName, ["kind"] = p.displayType, ["x"] = p.TilePoint.X, ["y"] = p.TilePoint.Y, ["petted"] = p.wasPet.Value, ["produce"] = p.currentProduce.Value, ["tool"] = p.GetAnimalData()?.HarvestTool, ["raw"] = p }).ToList(),
            "object_rows" => l.objects.Pairs.Select(p => ObjectRow(p.Key, p.Value)).ToList(),
            "terrain" => l.terrainFeatures.Pairs.Select(p => TerrainRow(p.Key, p.Value)).Concat(l.objects.Pairs.Where(p => p.Value is IndoorPot).Select(p => TerrainRow(p.Key, ((IndoorPot)p.Value).hoeDirt.Value))).ToList(),
            "clumps" => l.resourceClumps.Select(p => new Row { ["x"] = (int)p.Tile.X, ["y"] = (int)p.Tile.Y, ["w"] = p.width.Value, ["h"] = p.height.Value, ["kind"] = ClumpKind(p), ["raw"] = p }).ToList(),
            "bushes" => l.largeTerrainFeatures.OfType<Bush>().Select(p => new Row { ["x"] = (int)p.Tile.X, ["y"] = (int)p.Tile.Y, ["berries"] = p.tileSheetOffset.Value == 1, ["raw"] = p }).ToList(),
            "building_rows" => l.buildings.Select(p => new Row { ["name"] = p.buildingType.Value, ["x"] = p.tileX.Value, ["y"] = p.tileY.Value, ["w"] = p.tilesWide.Value, ["h"] = p.tilesHigh.Value, ["door"] = p.HasIndoors() ? p.getPointForHumanDoor() : null, ["days_left"] = p.daysOfConstructionLeft.Value, ["raw"] = p }).ToList(),
            "furniture_rows" => l.furniture.Select(p => new Row { ["name"] = p.DisplayName, ["x"] = (int)p.TileLocation.X, ["y"] = (int)p.TileLocation.Y, ["w"] = p.getTilesWide(), ["h"] = p.getTilesHigh(), ["rug"] = p.furniture_type.Value == Furniture.rug, ["raw"] = p }).ToList(),
            "exits" => Nav.Exits(l),
            "actions" => Nav.Actions(l),
            "ladders" => Nav.Ladders(l),
            _ => Missing
        };
        return Found(ref value);
    }

    private static Row NpcRow(NPC p) => new() { ["name"] = p.displayName, ["internal"] = p.Name, ["x"] = p.TilePoint.X, ["y"] = p.TilePoint.Y, ["villager"] = p.IsVillager, ["raw"] = p };

    private static Row ObjectRow(Vector2 tile, SObject o)
    {
        string kind = o switch
        {
            _ when o.IsWeeds() => "weeds",
            _ when o.IsTwig() => "twig",
            _ when o.IsBreakableStone() => "stone",
            _ when o.QualifiedItemId is "(O)590" or "(O)SeedSpot" => "artifact_spot",
            Chest { giftbox.Value: true } => "gift",
            Chest => "chest",
            Fence fence => fence.isGate.Value ? "gate" : "fence",
            _ when o.isSpawnedObject.Value => "forage",
            _ when o.bigCraftable.Value => "machine",
            _ => "object"
        };
        bool machine = o.heldObject.Value != null && o is not (Chest or Furniture);
        return new Row
        {
            ["x"] = (int)tile.X,
            ["y"] = (int)tile.Y,
            ["name"] = kind == "gift" ? "gift box" : o.DisplayName,
            ["id"] = o.QualifiedItemId,
            ["kind"] = kind,
            ["ready"] = machine && o.readyForHarvest.Value ? true : null,
            ["holds"] = machine ? o.heldObject.Value!.DisplayName : null,
            ["minutes"] = machine && !o.readyForHarvest.Value ? o.MinutesUntilReady : null,
            ["stacks"] = o is Chest { giftbox.Value: false } chest ? chest.GetItemsForPlayer().Count(p => p != null) : null,
            ["raw"] = o
        };
    }

    private static Row TerrainRow(Vector2 tile, TerrainFeature feature)
    {
        var row = new Row { ["x"] = (int)tile.X, ["y"] = (int)tile.Y, ["raw"] = feature };
        switch (feature)
        {
            case Tree tree:
                row["kind"] = tree.stump.Value ? "stump" : tree.growthStage.Value >= 5 ? "tree" : "sapling";
                row["stage"] = tree.growthStage.Value;
                row["tapped"] = tree.tapped.Value ? true : null;
                row["falling"] = tree.falling.Value ? true : null;
                break;
            case FruitTree fruit:
                row["kind"] = "fruit_tree";
                row["fruit"] = fruit.fruit.Count;
                break;
            case Grass:
                row["kind"] = "grass";
                break;
            case HoeDirt dirt:
                row["kind"] = "dirt";
                row["watered"] = dirt.isWatered();
                row["fertilizer"] = dirt.HasFertilizer() ? ItemRegistry.GetDataOrErrorItem(dirt.fertilizer.Value).DisplayName : null;
                if (dirt.crop is { } crop)
                {
                    row["crop"] = crop.indexOfHarvest.Value != null ? ItemRegistry.GetDataOrErrorItem(crop.indexOfHarvest.Value).DisplayName : "forage crop";
                    row["dead"] = crop.dead.Value;
                    row["ready"] = dirt.readyForHarvest();
                    row["thirsty"] = !crop.dead.Value && dirt.needsWatering() && !dirt.isWatered();
                    row["scythe"] = crop.GetHarvestMethod() == HarvestMethod.Scythe;
                    row["days_left"] = DaysLeft(dirt);
                }
                break;
            default:
                row["kind"] = feature.GetType().Name;
                break;
        }
        return row;
    }

    private static int DaysLeft(HoeDirt dirt)
    {
        Crop crop = dirt.crop;
        if (crop.dead.Value || dirt.readyForHarvest())
            return 0;
        if (crop.fullyGrown.Value)
            return crop.dayOfCurrentPhase.Value;
        int days = -crop.dayOfCurrentPhase.Value;
        for (int i = crop.currentPhase.Value; i < crop.phaseDays.Count - 1; i++)
            days += crop.phaseDays[i];
        return days;
    }

    private static string ClumpKind(ResourceClump clump) => clump.parentSheetIndex.Value switch
    {
        ResourceClump.stumpIndex => "large stump",
        ResourceClump.hollowLogIndex => "large log",
        ResourceClump.boulderIndex => "boulder",
        ResourceClump.meteoriteIndex => "meteorite",
        ResourceClump.mineRock1Index or ResourceClump.mineRock2Index or ResourceClump.mineRock3Index or ResourceClump.mineRock4Index => "mine boulder",
        _ => clump is GiantCrop ? "giant crop" : "large resource"
    };

    /****
    ** Menus
    ****/
    /// <summary>The menu receiving input, or null.</summary>
    public static IClickableMenu? ActiveMenu()
    {
        IClickableMenu? menu = Game1.activeClickableMenu;
        if (menu is TitleMenu && TitleMenu.subMenu != null)
            menu = TitleMenu.subMenu;
        while (menu?.GetChildMenu() != null)
            menu = menu.GetChildMenu();
        if (menu is GameMenu game)
            menu = game.GetCurrentPage();
        return menu;
    }

    public static Row? Menu()
    {
        IClickableMenu? menu = ActiveMenu();
        if (menu == null)
            return null;
        return new Row
        {
            ["type"] = menu.GetType().Name,
            ["root"] = Game1.activeClickableMenu!.GetType().Name,
            ["speaker"] = (menu as DialogueBox)?.characterDialogue?.speaker?.displayName,
            ["text"] = menu switch
            {
                DialogueBox box => box.getCurrentString(),
                LetterViewerMenu letter => string.Join("\n", letter.mailMessage),
                _ => null
            },
            ["choices"] = menu is DialogueBox { isQuestion: true } question ? question.responses.Select(p => p.responseText).ToList() : null,
            ["ready"] = menu is not DialogueBox dialogue || (!dialogue.transitioning && dialogue.safetyTimer <= 0 && dialogue.questionFinishPauseTimer <= 0),
            ["closable"] = new Func<object?>(() => menu.readyToClose()),
            ["tabs"] = (Game1.activeClickableMenu as GameMenu)?.tabs.Select(p => new Row { ["name"] = p.name, ["at"] = p.bounds.Center }).ToList(),
            ["tab"] = (Game1.activeClickableMenu as GameMenu)?.currentTab,
            ["held"] = HeldItem(menu) is { } held ? ItemRow(held) : null,
            ["components"] = new Func<object?>(() => Components(menu)),
            ["inventories"] = new Func<object?>(() => Inventories(menu)),
            ["raw"] = menu
        };
    }

    private static Item? HeldItem(IClickableMenu menu)
    {
        return Members.TryGet(menu, "heldItem", out object? held) ? held as Item : Game1.player?.CursorSlotItem;
    }

    private static IEnumerable<(string Field, object? Value)> Fields(IClickableMenu menu)
    {
        for (Type? type = menu.GetType(); type != null && type != typeof(object); type = type.BaseType)
        {
            foreach (FieldInfo field in type.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                if (field.Name is not ("allClickableComponents" or "currentlySnappedComponent"))
                    yield return (field.Name, field.GetValue(menu));
            }
        }
    }

    /// <summary>The menu's clickable components, except inventory slots.</summary>
    private static List<Row> Components(IClickableMenu menu)
    {
        var rows = new List<Row>();
        var seen = new HashSet<object>(ReferenceEqualityComparer.Instance);
        void Add(string field, object? value, int depth)
        {
            switch (value)
            {
                case null or string or InventoryMenu:
                    return;
                case ClickableComponent c:
                    if (c.visible && c.bounds.Width > 0 && seen.Add(c))
                    {
                        rows.Add(new Row
                        {
                            ["i"] = rows.Count,
                            ["name"] = c.name,
                            ["label"] = string.IsNullOrWhiteSpace(c.label) ? null : c.label,
                            ["hover"] = c is ClickableTextureComponent { hoverText.Length: > 0 } texture ? texture.hoverText.Replace("\r", "").Replace("\n", "; ") : null,
                            ["item"] = c.item?.DisplayName,
                            ["field"] = field,
                            ["at"] = c.bounds.Center,
                            ["raw"] = c
                        });
                    }
                    return;
                case IDictionary map when depth < 3:
                    foreach (object key in map.Keys)
                        Add(field, key, depth + 1);
                    foreach (object? entry in map.Values)
                        Add(field, entry, depth + 1);
                    return;
                case IList list when depth < 3 && list.Count > 0 && list[0] is ClickableComponent or IList or IDictionary:
                    foreach (object? entry in list)
                        Add(field, entry, depth + 1);
                    return;
            }
        }
        foreach (var (field, value) in Fields(menu))
        {
            if (value is ClickableComponent or IList or IDictionary)
                Add(field, value, 0);
        }
        return rows;
    }

    /// <summary>The menu's item grids, such as your backpack or a chest.</summary>
    private static List<Row> Inventories(IClickableMenu menu)
    {
        var rows = new List<Row>();
        foreach (var (field, value) in Fields(menu))
        {
            if (value is not InventoryMenu grid)
                continue;
            rows.Add(new Row
            {
                ["field"] = field,
                ["player"] = ReferenceEquals(grid.actualInventory, Game1.player.Items) || grid.playerInventory,
                ["slots"] = grid.inventory.Select((slot, i) =>
                {
                    Item? item = i < grid.actualInventory.Count ? grid.actualInventory[i] : null;
                    return new Row { ["slot"] = i, ["at"] = slot.bounds.Center, ["item"] = item == null ? null : ItemRow(item), ["usable"] = item == null || grid.highlightMethod(item) };
                }).ToList(),
                ["raw"] = grid
            });
        }
        return rows;
    }
}
