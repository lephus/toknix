// Sample for `toknix outline` (P3-T2): Go.
package shop

import (
	"errors"
	"fmt"
	"strings"
)

const brace = "{"

var ErrEmpty = errors.New("cart is empty {}")

type Money int64

type Item struct {
	SKU   string
	Price Money
	Tags  []string
}

type Store interface {
	Get(sku string) (Item, error)
	Put(item Item) error
}

type Cart struct {
	owner string
	items map[string]int
}

func NewCart(owner string) *Cart {
	return &Cart{owner: owner, items: map[string]int{}}
}

func (c *Cart) Add(sku string, qty int) {
	if qty <= 0 {
		return
	}
	c.items[sku] += qty
}

func (c Cart) Total(store Store) (Money, error) {
	if len(c.items) == 0 {
		return 0, ErrEmpty
	}
	var total Money
	for sku, qty := range c.items {
		item, err := store.Get(sku)
		if err != nil {
			return 0, fmt.Errorf("get %s: %w", sku, err)
		}
		total += item.Price * Money(qty)
	}
	return total, nil
}

func (c *Cart) Describe() string {
	format := func(sku string, qty int) string {
		return fmt.Sprintf("%s x%d {%s}", sku, qty, c.owner)
	}
	var parts []string
	for sku, qty := range c.items {
		parts = append(parts, format(sku, qty))
	}
	return strings.Join(parts, ", ")
}

func Map[T any, U any](xs []T, f func(T) U) []U {
	out := make([]U, 0, len(xs))
	for _, x := range xs {
		out = append(out, f(x))
	}
	return out
}

type memStore struct {
	items map[string]Item
}

func (m *memStore) Get(sku string) (Item, error) {
	item, ok := m.items[sku]
	if !ok {
		return Item{}, fmt.Errorf("no item %q", sku)
	}
	return item, nil
}

func (m *memStore) Put(item Item) error {
	m.items[item.SKU] = item
	return nil
}

var raw = `a raw string with { and
} across lines`
