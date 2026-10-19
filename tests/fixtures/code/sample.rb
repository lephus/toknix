# Sample for `tokopt outline` (P3-T2): Ruby.
# A comment with "def fake" and "end" in it.
require "json"

BRACE = "{ #{1 + 1} }"

module Shop
  VERSION = "1.0"

  class Error < StandardError; end

  class Cart
    attr_reader :owner, :items

    def initialize(owner)
      @owner = owner
      @items = Hash.new(0)
    end

    def add(sku, qty = 1)
      raise Error, "qty must be > 0" unless qty.positive?
      @items[sku] += qty
      self
    end

    def total(store)
      @items.sum do |sku, qty|
        price = store.fetch(sku) { 0 }
        price * qty
      end
    end

    def empty? = @items.empty?

    def each_line
      return enum_for(:each_line) unless block_given?

      @items.each { |sku, qty| yield "#{sku} x#{qty}" }
    end

    def to_json(*args)
      { owner: owner, items: items }.to_json(*args)
    end

    def summary
      lines = items.map do |sku, qty|
        if qty > 1
          "#{sku} (#{qty})"
        else
          sku
        end
      end
      lines.join(", ")
    end

    class << self
      def from_json(text)
        data = JSON.parse(text)
        cart = new(data["owner"])
        data["items"].each { |sku, qty| cart.add(sku, qty) }
        cart
      end
    end

    private

    def log(message)
      warn <<~MSG
        [cart #{owner}] #{message}
        end of message
      MSG
    end
  end

  module Pricing
    def self.discount(total, percent)
      while percent > 100
        percent -= 100
      end
      total - (total * percent / 100)
    end

    def round(value) = value.round(2)
  end
end

def helper(x)
  case x
  when Integer then x * 2
  else 0
  end
end
