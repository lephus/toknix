<?php
// Sample for `toknix outline` (P3-T2): PHP.
declare(strict_types=1);

namespace App\Shop;

use InvalidArgumentException;

const BRACE = '{';

# A hash comment with "function fake() {" in it.
interface Store
{
    public function get(string $sku): ?array;

    public function put(string $sku, array $item): void;
}

trait Loggable
{
    protected array $log = [];

    public function log(string $line): void
    {
        $this->log[] = "[{$line}]";
    }
}

enum Status: string
{
    case Open = 'open';
    case Closed = 'closed';

    public function label(): string
    {
        return ucfirst($this->value);
    }
}

#[\Attribute]
final class Cart
{
    use Loggable;

    private array $items = [];

    public function __construct(private readonly string $owner)
    {
    }

    public function add(string $sku, int $qty = 1): static
    {
        if ($qty <= 0) {
            throw new InvalidArgumentException("qty must be > 0, got {$qty}");
        }
        $this->items[$sku] = ($this->items[$sku] ?? 0) + $qty;
        $this->log("add {$sku}");
        return $this;
    }

    public function total(Store $store): int
    {
        $price = function (string $sku) use ($store): int {
            return $store->get($sku)['price'] ?? 0;
        };
        $sum = 0;
        foreach ($this->items as $sku => $qty) {
            $sum += $price($sku) * $qty;
        }
        return $sum;
    }

    public static function fromArray(array $data): self
    {
        $cart = new self($data['owner']);
        foreach ($data['items'] as $sku => $qty) {
            $cart->add($sku, $qty);
        }
        return $cart;
    }
}

abstract class Shape
{
    abstract public function area(): float;

    public function describe(): string { return static::class . ' {' . $this->area() . '}'; }
}

function format_price(int $cents): string
{
    $pad = fn(int $n): string => str_pad((string) $n, 2, '0', STR_PAD_LEFT);
    return intdiv($cents, 100) . '.' . $pad($cents % 100);
}

function outer(): callable
{
    function inner(): string
    {
        return 'inner';
    }
    return 'App\Shop\inner';
}
