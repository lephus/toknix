// Sample for `tokopt outline` (P3-T2): Dart.
import 'dart:async';
import 'dart:convert';

const brace = '{';
const raw = r'raw ${not} {interpolated}';
const multi = '''
a multi-line string with { and
} inside
''';

typedef Pricer = int Function(String sku);

enum Status { open, closed }

abstract class Store {
  int? priceOf(String sku);

  bool has(String sku) => priceOf(sku) != null;
}

mixin Logging {
  final List<String> log = [];

  void record(String line) {
    log.add('[$line] {');
  }
}

class Cart with Logging {
  final String owner;
  final Map<String, int> _items = {};

  Cart(this.owner);

  Cart.empty() : owner = 'nobody';

  factory Cart.fromJson(Map<String, dynamic> json) {
    final cart = Cart(json['owner'] as String);
    (json['items'] as Map<String, dynamic>).forEach((k, v) => cart.add(k, v as int));
    return cart;
  }

  int get size => _items.length;

  bool get isEmpty {
    return _items.isEmpty;
  }

  void add(String sku, [int qty = 1]) {
    if (qty <= 0) {
      throw ArgumentError('qty must be > 0, got $qty {');
    }
    _items[sku] = (_items[sku] ?? 0) + qty;
    record('add $sku');
  }

  int total(Store store) {
    int line(String sku, int qty) {
      return (store.priceOf(sku) ?? 0) * qty;
    }

    var sum = 0;
    _items.forEach((sku, qty) {
      sum += line(sku, qty);
    });
    return sum;
  }

  Future<String> describe() async {
    await Future<void>.delayed(Duration.zero);
    return jsonEncode({'owner': owner, 'items': _items});
  }

  @override
  String toString() => 'Cart($owner)';
}

extension Money on int {
  String get asPrice => '${this ~/ 100}.${(this % 100).toString().padLeft(2, '0')}';
}

Future<void> main(List<String> args) async {
  final cart = Cart('ann')..add('a', 2);
  print(await cart.describe());
}

int square(int x) => x * x;
