using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using StardewValley;

namespace StardewBridge;

/// <summary>A read-only expression like <c>player.energy &lt; 20 or menu != null</c>. There are no function calls or writes.</summary>
internal abstract class Expr
{
    private static readonly ConcurrentDictionary<string, Expr> Cache = new();

    /// <param name="scope">The row being filtered, if any; names resolve there first.</param>
    public abstract object? Eval(object? scope);

    public static Expr Parse(string text)
    {
        if (Cache.TryGetValue(text, out Expr? known))
            return known;
        if (Cache.Count > 2000)
            Cache.Clear();
        return Cache[text] = new Parser(text).Parse();
    }

    public static bool Truthy(object? value) => value switch
    {
        null => false,
        bool b => b,
        string s => s.Length > 0,
        _ => !TryNumber(value, out double number) || number != 0
    };

    public static bool TryNumber(object? value, out double number)
    {
        switch (value)
        {
            case int or long or short or byte or uint or ulong or ushort or sbyte or float or double or decimal:
                number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                return true;
            case bool b:
                number = b ? 1 : 0;
                return true;
            default:
                number = 0;
                return false;
        }
    }

    private static bool Same(object? a, object? b)
    {
        if (a == null || b == null)
            return a == null && b == null;
        if (TryNumber(a, out double x) && TryNumber(b, out double y))
            return x == y;
        if (a is string or Enum || b is string or Enum)
            return string.Equals(a.ToString(), b.ToString(), StringComparison.OrdinalIgnoreCase);
        return Equals(a, b);
    }

    private sealed class Const : Expr
    {
        private readonly object? Value;
        public Const(object? value) => this.Value = value;
        public override object? Eval(object? scope) => this.Value;
    }

    private sealed class Name : Expr
    {
        private readonly string Text;
        public Name(string text) => this.Text = text;
        public override object? Eval(object? scope)
        {
            if (scope != null && Members.TryGet(scope, this.Text, out object? value))
                return value;
            return Roots.TryGet(this.Text, out value)
                ? value
                : scope != null ? null : throw new OpError($"unknown name '{this.Text}'");
        }
    }

    private sealed class Member : Expr
    {
        private readonly Expr Target;
        private readonly string Text;
        public Member(Expr target, string text) => (this.Target, this.Text) = (target, text);
        public override object? Eval(object? scope)
        {
            object? target = this.Target.Eval(scope);
            return target != null && Members.TryGet(target, this.Text, out object? value) ? value : null;
        }
    }

    private sealed class Index : Expr
    {
        private readonly Expr Target, Key;
        public Index(Expr target, Expr key) => (this.Target, this.Key) = (target, key);
        public override object? Eval(object? scope) => Members.Index(this.Target.Eval(scope), this.Key.Eval(scope));
    }

    private sealed class Not : Expr
    {
        private readonly Expr Inner;
        public Not(Expr inner) => this.Inner = inner;
        public override object? Eval(object? scope) => !Truthy(this.Inner.Eval(scope));
    }

    private sealed class Binary : Expr
    {
        private readonly string Op;
        private readonly Expr Left, Right;
        public Binary(string op, Expr left, Expr right) => (this.Op, this.Left, this.Right) = (op, left, right);
        public override object? Eval(object? scope)
        {
            object? a = this.Left.Eval(scope);
            switch (this.Op)
            {
                case "and": return Truthy(a) && Truthy(this.Right.Eval(scope));
                case "or": return Truthy(a) || Truthy(this.Right.Eval(scope));
            }
            object? b = this.Right.Eval(scope);
            switch (this.Op)
            {
                case "==": return Same(a, b);
                case "!=": return !Same(a, b);
                case "contains": return a?.ToString()?.Contains(b?.ToString() ?? "", StringComparison.OrdinalIgnoreCase) == true;
            }
            if (!TryNumber(a, out double x) || !TryNumber(b, out double y))
                return this.Op is "+" or "-" or "*" or "/" ? null : false;
            return this.Op switch
            {
                "<" => x < y,
                "<=" => x <= y,
                ">" => x > y,
                ">=" => x >= y,
                "+" => x + y,
                "-" => x - y,
                "*" => x * y,
                "/" => y == 0 ? null : x / y,
                _ => null
            };
        }
    }

    private sealed class Parser
    {
        private readonly List<(char Kind, string Text)> Tokens = new();
        private int At;
        private static readonly Dictionary<string, int> Precedence = new()
        {
            ["or"] = 1, ["and"] = 2, ["=="] = 3, ["!="] = 3, ["contains"] = 3, ["<"] = 4, ["<="] = 4, [">"] = 4, [">="] = 4, ["+"] = 5, ["-"] = 5, ["*"] = 6, ["/"] = 6
        };

        public Parser(string text)
        {
            int i = 0;
            while (i < text.Length)
            {
                char c = text[i];
                if (char.IsWhiteSpace(c))
                {
                    i++;
                }
                else if (char.IsDigit(c))
                {
                    int start = i;
                    while (i < text.Length && (char.IsDigit(text[i]) || text[i] == '.'))
                        i++;
                    this.Tokens.Add(('n', text[start..i]));
                }
                else if (char.IsLetter(c) || c == '_')
                {
                    int start = i;
                    while (i < text.Length && (char.IsLetterOrDigit(text[i]) || text[i] == '_'))
                        i++;
                    string word = text[start..i];
                    this.Tokens.Add((word is "and" or "or" or "not" or "contains" ? 'o' : 'w', word));
                }
                else if (c is '\'' or '"')
                {
                    int end = text.IndexOf(c, i + 1);
                    if (end < 0)
                        throw new OpError($"unterminated string in '{text}'");
                    this.Tokens.Add(('s', text[(i + 1)..end]));
                    i = end + 1;
                }
                else
                {
                    string two = i + 1 < text.Length ? text.Substring(i, 2) : "";
                    string op = two is "==" or "!=" or "<=" or ">=" ? two : c.ToString();
                    if (op.Length == 1 && !"<>+-*/().[]".Contains(c))
                        throw new OpError($"unexpected '{c}' in '{text}'");
                    this.Tokens.Add(('o', op));
                    i += op.Length;
                }
            }
        }

        public Expr Parse()
        {
            Expr result = this.Operators(0);
            if (this.At < this.Tokens.Count)
                throw new OpError($"unexpected '{this.Tokens[this.At].Text}'");
            return result;
        }

        private bool Take(string op)
        {
            if (this.At < this.Tokens.Count && this.Tokens[this.At] is { Kind: 'o' } token && token.Text == op)
            {
                this.At++;
                return true;
            }
            return false;
        }

        private void Expect(string op)
        {
            if (!this.Take(op))
                throw new OpError($"expected '{op}'");
        }

        private Expr Operators(int min)
        {
            Expr left = this.Unary();
            while (this.At < this.Tokens.Count && this.Tokens[this.At] is { Kind: 'o' } token && Precedence.TryGetValue(token.Text, out int level) && level > min)
            {
                this.At++;
                left = new Binary(token.Text, left, this.Operators(level));
            }
            return left;
        }

        private Expr Unary()
        {
            if (this.Take("not"))
                return new Not(this.Operators(2));
            if (this.Take("-"))
                return new Binary("-", new Const(0.0), this.Unary());
            return this.Postfix();
        }

        private Expr Postfix()
        {
            Expr value;
            if (this.Take("("))
            {
                value = this.Operators(0);
                this.Expect(")");
            }
            else
            {
                if (this.At >= this.Tokens.Count)
                    throw new OpError("unexpected end of expression");
                var (kind, text) = this.Tokens[this.At++];
                value = kind switch
                {
                    'n' => new Const(double.Parse(text, CultureInfo.InvariantCulture)),
                    's' => new Const(text),
                    'w' => text switch { "true" => new Const(true), "false" => new Const(false), "null" => new Const(null), _ => new Name(text) },
                    _ => throw new OpError($"unexpected '{text}'")
                };
            }
            while (true)
            {
                if (this.Take("."))
                {
                    if (this.At >= this.Tokens.Count || this.Tokens[this.At].Kind is not ('w' or 'o'))
                        throw new OpError("expected a name after '.'");
                    value = new Member(value, this.Tokens[this.At++].Text);
                }
                else if (this.Take("["))
                {
                    value = new Index(value, this.Operators(0));
                    this.Expect("]");
                }
                else
                    return value;
            }
        }
    }
}

/// <summary>Reads members by name: curated ones first, then any field or public property by reflection.</summary>
internal static class Members
{
    private static readonly ConcurrentDictionary<(Type, string), Func<object, object?>?> Accessors = new();

    public static bool TryGet(object target, string name, out object? value)
    {
        value = null;
        if (target is Row row)
        {
            if (row.TryGetValue(name, out value))
            {
                if (value is Func<object?> lazy)
                    value = lazy();
                return true;
            }
            return row.TryGetValue("raw", out object? raw) && raw != null && name != "raw" && TryGet(raw, name, out value);
        }
        if (target is StaticType type)
            return Reflect(type.Type, null, name, out value);
        if (name == "type")
        {
            value = target.GetType().Name;
            return true;
        }
        if (name == "count" && target is IEnumerable list and not string)
        {
            value = target is ICollection collection ? collection.Count : list.Cast<object?>().Count();
            return true;
        }
        if (Views.TryGet(target, name, out value))
            return true;
        return Reflect(target.GetType(), target, name, out value);
    }

    private static bool Reflect(Type type, object? target, string name, out object? value)
    {
        var accessor = Accessors.GetOrAdd((type, name), key => Find(key.Item1, key.Item2));
        value = accessor == null ? null : Unwrap(accessor(target!));
        return accessor != null;
    }

    private static Func<object, object?>? Find(Type type, string name)
    {
        if (type.Namespace?.StartsWith("System.Reflection") == true || typeof(Delegate).IsAssignableFrom(type) || typeof(Type).IsAssignableFrom(type))
            return null;
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.IgnoreCase | BindingFlags.DeclaredOnly;
        for (Type? t = type; t != null; t = t.BaseType)
        {
            foreach (PropertyInfo property in t.GetProperties(flags))
            {
                if (string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase) && property.GetMethod is { IsPublic: true } && property.GetIndexParameters().Length == 0)
                    return target => property.GetValue(property.GetMethod.IsStatic ? null : target);
            }
            foreach (FieldInfo field in t.GetFields(flags))
            {
                if (string.Equals(field.Name, name, StringComparison.OrdinalIgnoreCase))
                    return target => field.GetValue(field.IsStatic ? null : target);
            }
        }
        return null;
    }

    private static readonly ConcurrentDictionary<Type, PropertyInfo?> NetValues = new();

    /// <summary>Get the value inside a net field.</summary>
    public static object? Unwrap(object? value)
    {
        if (value == null || value.GetType() is not { } type)
            return value;
        bool isNet = false;
        for (Type? t = type; t != null && !isNet; t = t.BaseType)
            isNet = t.Name.StartsWith("NetFieldBase") || t.Name.StartsWith("NetPausableField");
        if (!isNet)
            return value;
        PropertyInfo? property = NetValues.GetOrAdd(type, t => t.GetProperties().FirstOrDefault(p => p.Name == "Value" && p.GetIndexParameters().Length == 0));
        return property == null ? value : property.GetValue(value);
    }

    public static IEnumerable? Enumerate(object? value)
    {
        if (value is null or string)
            return null;
        if (value is IEnumerable list)
            return list;
        // net dictionaries
        PropertyInfo? pairs = value.GetType().GetProperty("Pairs");
        return pairs?.GetValue(value) as IEnumerable;
    }

    public static object? Index(object? target, object? key)
    {
        if (target == null || key == null)
            return null;
        if (target is Row row)
            return TryGet(row, key.ToString()!, out object? member) ? member : null;
        if (target is LocationIndex)
            return Game1.getLocationFromName(key.ToString());
        if (target is IDictionary map)
        {
            foreach (object? candidate in map.Keys)
            {
                if (string.Equals(candidate?.ToString(), key.ToString(), StringComparison.OrdinalIgnoreCase))
                    return Unwrap(map[candidate!]);
            }
            return null;
        }
        if (Expr.TryNumber(key, out double number))
        {
            int index = (int)number;
            if (target is IList list)
                return index >= 0 && index < list.Count ? Unwrap(list[index]) : null;
            int i = 0;
            foreach (object? entry in Enumerate(target) ?? Array.Empty<object>())
            {
                if (i++ == index)
                    return Unwrap(entry);
            }
            return null;
        }
        foreach (object? entry in Enumerate(target) ?? Array.Empty<object>())
        {
            if (entry != null && TryGet(entry, "key", out object? entryKey) && string.Equals(entryKey?.ToString(), key.ToString(), StringComparison.OrdinalIgnoreCase) && TryGet(entry, "value", out object? entryValue))
                return entryValue;
        }
        return TryGet(target, key.ToString()!, out object? result) ? result : null;
    }
}

/// <summary>A record built for the client. 'raw' is the underlying game object, for reading anything we didn't curate.</summary>
internal sealed class Row : Dictionary<string, object?>
{
    public Row() : base(StringComparer.OrdinalIgnoreCase) { }
}

internal sealed record StaticType(Type Type);

internal sealed class LocationIndex { }
