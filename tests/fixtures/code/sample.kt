// Sample for `tokopt outline` (P3-T2): Kotlin.
package com.example.shop

import kotlin.math.max

const val BRACE = "{"
val template = """
    a raw string with { and
    } inside ${1 + 1}
"""

data class Item(val sku: String, val price: Long)

typealias Pricer = (String) -> Long?

interface Store {
    fun priceOf(sku: String): Long?

    fun has(sku: String): Boolean = priceOf(sku) != null
}

sealed class Event {
    data class Added(val sku: String, val qty: Int) : Event()
    object Cleared : Event()
}

enum class Status { OPEN, CLOSED }

@JvmInline
value class Money(val cents: Long)

class Cart(private val owner: String) : Iterable<Pair<String, Int>> {
    private val items = mutableMapOf<String, Int>()

    init {
        require(owner.isNotBlank()) { "owner {required}" }
    }

    fun add(sku: String, qty: Int = 1): Cart {
        require(qty > 0) { "qty must be > 0, got $qty" }
        items[sku] = (items[sku] ?: 0) + qty
        return this
    }

    fun total(store: Store): Long {
        fun line(sku: String, qty: Int): Long =
            (store.priceOf(sku) ?: 0L) * qty

        return items.entries.sumOf { (sku, qty) -> line(sku, qty) }
    }

    suspend fun describe(): String {
        val label = "cart of ${owner.uppercase()} {"
        return label
    }

    override fun iterator(): Iterator<Pair<String, Int>> =
        items.entries.map { it.key to it.value }.iterator()

    fun `adds two items`() = add("a").add("b")

    companion object {
        fun empty(): Cart = Cart("nobody")

        fun <T> merge(carts: List<Cart>, pick: (Cart) -> T): List<T> {
            return carts.map(pick)
        }
    }
}

object Registry {
    private val carts = mutableListOf<Cart>()

    fun register(cart: Cart) {
        carts += cart
    }
}

fun String.shout(): String = uppercase() + "!"

fun main(args: Array<String>) {
    val cart = Cart("ann").add("a", max(1, 2))
    println(cart.describe2())
}

private fun Cart.describe2(): String {
    return "cart {}"
}
