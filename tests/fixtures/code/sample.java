// Sample for `toknix outline` (P3-T2): Java.
package com.example.shop;

import java.util.*;
import java.util.function.Function;

/** A shop. The text "class Fake {" in a comment is not code. */
@Deprecated(since = "2.0")
public class Shop implements Comparable<Shop> {
    private static final String BRACE = "{";
    private final String name;
    private final Map<String, Integer> stock = new HashMap<>();

    public Shop(String name) {
        this.name = name;
    }

    @Override
    public int compareTo(Shop other) {
        return name.compareTo(other.name);
    }

    public synchronized void add(String sku, int qty)
            throws IllegalArgumentException {
        if (qty <= 0) {
            throw new IllegalArgumentException("qty must be > 0 {" + qty + "}");
        }
        stock.merge(sku, qty, Integer::sum);
    }

    public static <T extends Comparable<T>> T max(List<T> xs) {
        T best = null;
        for (T x : xs) {
            if (best == null || x.compareTo(best) > 0) {
                best = x;
            }
        }
        return best;
    }

    public List<String> report(Function<String, String> format) {
        class Line {
            final String text;

            Line(String sku) {
                this.text = format.apply(sku);
            }

            String render() {
                return "- " + text;
            }
        }
        List<String> out = new ArrayList<>();
        for (String sku : stock.keySet()) {
            out.add(new Line(sku).render());
        }
        Runnable r = new Runnable() {
            public void run() {
                System.out.println('{');
            }
        };
        r.run();
        return out;
    }

    public static class Receipt {
        private final int total;

        Receipt(int total) {
            this.total = total;
        }

        public int total() {
            return total;
        }
    }

    enum Status {
        OPEN, CLOSED;

        boolean isOpen() {
            return this == OPEN;
        }
    }
}

interface Repository<T> {
    Optional<T> find(String id);

    default boolean exists(String id) {
        return find(id).isPresent();
    }
}

record Point(int x, int y) {
    Point {
        if (x < 0) throw new IllegalArgumentException("x");
    }

    int sum() { return x + y; }
}
