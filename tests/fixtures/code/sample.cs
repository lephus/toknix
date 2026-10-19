// Sample for `tokopt outline` (P3-T2): C#.
using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Shop.Core;

public record Item(string Sku, long Price);

public interface IStore
{
    long? PriceOf(string sku);

    Task<Item?> FindAsync(string sku);
}

public enum Status { Open, Closed }

[Serializable]
public sealed class Cart : IDisposable
{
    private const string Brace = "{";
    private static readonly string Verbatim = @"C:\path with ""quotes"" and {";
    private readonly Dictionary<string, int> _items = new();

    public string Owner { get; }

    public int Count => _items.Count;

    public Cart(string owner)
    {
        Owner = owner ?? throw new ArgumentNullException(nameof(owner));
    }

    public Cart Add(string sku, int qty = 1)
    {
        if (qty <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(qty), $"qty must be > 0, got {qty}");
        }
        _items[sku] = _items.TryGetValue(sku, out var n) ? n + qty : qty;
        return this;
    }

    public long Total(IStore store)
    {
        long Line(string sku, int qty)
        {
            return (store.PriceOf(sku) ?? 0) * qty;
        }

        long sum = 0;
        foreach (var (sku, qty) in _items)
        {
            sum += Line(sku, qty);
        }
        return sum;
    }

    public async Task<string> DescribeAsync()
    {
        await Task.Yield();
        return $"cart of {Owner} {{";
    }

    public T Get<T>(string key) where T : class, new()
    {
        return new T();
    }

    public static Cart Empty() => new Cart("nobody");

    public void Dispose()
    {
        _items.Clear();
    }

    private sealed class Line
    {
        public int Qty { get; set; }

        public override string ToString() => $"x{Qty}";
    }
}

public struct Point
{
    public int X;
    public int Y;

    public Point(int x, int y)
    {
        X = x;
        Y = y;
    }

    public readonly int Sum() { return X + Y; }
}

public static class Extensions
{
    public static string Shout(this string s) => s.ToUpperInvariant() + "!";
}
