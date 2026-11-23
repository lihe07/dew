using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;

namespace StardewBridge;

/// <summary>A numbered log of everything the player would notice, so nothing on screen goes unreported.</summary>
internal sealed class Journal
{
    private const int Capacity = 3000;
    private readonly Queue<JObject> Entries = new();
    private readonly HashSet<HUDMessage> SeenMessages = new();
    private readonly Dictionary<object, string> Bubbles = new(ReferenceEqualityComparer.Instance);
    private object? LastDialogue;
    private string? LastText;
    private string[]? LastChoices;
    private object? LastLetter;
    private Event? LastEvent;
    private int LastChat;
    private object? LastMenu;

    public long LastSeq { get; private set; }

    public Journal(IModHelper helper)
    {
        helper.Events.Player.Warped += (_, e) =>
        {
            if (e.IsLocalPlayer)
                this.Add("place", $"entered {e.NewLocation.NameOrUniqueName}");
        };
        helper.Events.GameLoop.DayStarted += (_, _) => this.Add("day", $"{Game1.shortDayNameFromDayOfSeason(Game1.dayOfMonth)} {Game1.season} {Game1.dayOfMonth}, year {Game1.year} began");
        helper.Events.GameLoop.Saved += (_, _) => this.Add("saved", "game saved");
        helper.Events.Player.LevelChanged += (_, e) =>
        {
            if (e.IsLocalPlayer)
                this.Add("level", $"{e.Skill} level {e.OldLevel} -> {e.NewLevel}");
        };
        helper.Events.Player.InventoryChanged += (_, e) =>
        {
            if (!e.IsLocalPlayer)
                return;
            // a stack moving between slots shows up as both added and removed, so report the net change
            var net = new Dictionary<string, int>();
            void Count(Item item, int amount)
            {
                string name = item.DisplayName + item.Quality switch { 1 => " (silver)", 2 => " (gold)", 4 => " (iridium)", _ => "" };
                net[name] = net.GetValueOrDefault(name) + amount;
            }
            foreach (Item item in e.Added)
                Count(item, item.Stack);
            foreach (Item item in e.Removed)
                Count(item, -item.Stack);
            foreach (ItemStackSizeChange change in e.QuantityChanged)
                Count(change.Item, change.NewSize - change.OldSize);
            foreach (var pair in net.Where(p => p.Value != 0))
                this.Add("item", $"{pair.Key} {pair.Value:+#;-#}");
        };
    }

    public void Add(string kind, string text, JToken? data = null)
    {
        var entry = new JObject { ["seq"] = ++this.LastSeq, ["kind"] = kind, ["text"] = text };
        if (Context.IsWorldReady)
            entry["time"] = Game1.timeOfDay;
        if (data != null)
            entry["data"] = data;
        this.Entries.Enqueue(entry);
        while (this.Entries.Count > Capacity)
            this.Entries.Dequeue();
    }

    public JArray Since(long seq) => new(this.Entries.Where(p => (long)p["seq"]! > seq));

    /// <summary>Check for things which have no event.</summary>
    public void Poll()
    {
        foreach (HUDMessage message in Game1.hudMessages)
        {
            // item pickups are covered by inventory changes
            if (this.SeenMessages.Add(message) && message.messageSubject == null && !string.IsNullOrWhiteSpace(message.message))
                this.Add("message", Clean(message.message));
        }
        if (this.SeenMessages.Count > 0)
            this.SeenMessages.RemoveWhere(p => !Game1.hudMessages.Contains(p));

        this.PollMenu();

        if (!ReferenceEquals(Game1.CurrentEvent, this.LastEvent))
        {
            if (this.LastEvent != null)
                this.Add("event", $"{Describe(this.LastEvent)} ended");
            this.LastEvent = Game1.CurrentEvent;
            if (this.LastEvent != null)
                this.Add("event", $"{Describe(this.LastEvent)} started");
        }

        if (Context.IsWorldReady && Game1.currentLocation != null)
        {
            foreach (NPC npc in Game1.currentLocation.characters)
                this.PollBubble(npc);
            if (Game1.CurrentEvent != null)
            {
                foreach (NPC npc in Game1.CurrentEvent.actors)
                    this.PollBubble(npc);
            }
        }

        if (Game1.chatBox != null && Members.TryGet(Game1.chatBox, "messages", out object? messages) && messages is System.Collections.ICollection { Count: var count })
        {
            if (count > this.LastChat && !Clock.IsSingleplayer)
            {
                var list = ((System.Collections.IEnumerable)messages).Cast<ChatMessage>().Skip(this.LastChat);
                foreach (ChatMessage message in list)
                    this.Add("chat", Clean(string.Concat(message.message.Select(p => p.message))));
            }
            this.LastChat = count;
        }
    }

    private static string Describe(Event e) => e.isFestival ? $"festival {e.FestivalName}" : $"cutscene {e.id}";

    private void PollBubble(NPC npc)
    {
        string? text = Members.TryGet(npc, "textAboveHeadTimer", out object? timer) && timer is > 0 && Members.TryGet(npc, "textAboveHead", out object? value) ? value as string : null;
        if (string.IsNullOrWhiteSpace(text))
        {
            this.Bubbles.Remove(npc);
            return;
        }
        if (this.Bubbles.TryGetValue(npc, out string? known) && known == text)
            return;
        this.Bubbles[npc] = text;
        this.Add("bubble", $"{npc.displayName}: {Clean(text)}");
    }

    private void PollMenu()
    {
        IClickableMenu? menu = Views.ActiveMenu();
        if (!ReferenceEquals(menu, this.LastMenu))
        {
            this.LastMenu = menu;
            if (menu != null && menu is not (DialogueBox or LetterViewerMenu or TitleMenu or BobberBar))
                this.Add("menu", $"{menu.GetType().Name} opened");
        }

        if (menu is DialogueBox box)
        {
            string text = box.getCurrentString() ?? "";
            if (text.Length > 0 && (!ReferenceEquals(box, this.LastDialogue) || text != this.LastText))
            {
                string? speaker = box.characterDialogue?.speaker?.displayName;
                this.Add("dialogue", (speaker != null ? $"{speaker}: " : "") + Clean(text));
            }
            this.LastDialogue = box;
            this.LastText = text;
            string[]? choices = box.isQuestion ? box.responses.Select(p => p.responseText).ToArray() : null;
            if (choices != null && (this.LastChoices == null || !choices.SequenceEqual(this.LastChoices)))
                this.Add("question", string.Join(" / ", choices.Select((p, i) => $"{i}: {Clean(p)}")));
            this.LastChoices = choices;
        }
        else
        {
            this.LastDialogue = null;
            this.LastText = null;
            this.LastChoices = null;
        }

        if (menu is LetterViewerMenu letter)
        {
            if (!ReferenceEquals(letter, this.LastLetter))
                this.Add("letter", Clean(string.Join(" ", letter.mailMessage)));
            this.LastLetter = letter;
        }
        else
            this.LastLetter = null;
    }

    private static string Clean(string text) => text.Replace("\r", "").Replace("\n", " ").Replace("^", " ").Replace("  ", " ").Trim();
}
