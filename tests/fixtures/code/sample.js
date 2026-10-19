// Sample for `toknix outline` (P3-T2): plain JS with a little JSX.
import React, { useState } from "react";

const TEMPLATE = "{ not a block }";
const PATTERN = /\{(\w+)\}/g;
const message = `total: ${1 + 2} {braces in a template}`;

export function formatPrice(cents, currency = "USD") {
  const units = Math.floor(cents / 100);
  return `${currency} ${units}.${String(cents % 100).padStart(2, "0")}`;
}

export default function createStore(reducer, initial) {
  let state = initial;
  const listeners = [];

  function notify() {
    for (const listener of listeners) {
      listener(state);
    }
  }

  const subscribe = (listener) => {
    listeners.push(listener);
    return () => listeners.splice(listeners.indexOf(listener), 1);
  };

  return {
    getState: () => state,
    dispatch(action) {
      state = reducer(state, action);
      notify();
    },
    subscribe,
  };
}

export const sum = (xs) => xs.reduce((a, b) => a + b, 0);

export const fetchJson = async (url, options = {}) => {
  const response = await fetch(url, { ...options, headers: { "x-brace": "{" } });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
};

const notAFunction = (1 + 2) * 3;

class Cart {
  #items = [];
  static TAX = 0.1;

  constructor(owner) {
    this.owner = owner;
  }

  add(item, qty = 1) {
    this.#items.push({ item, qty });
    return this;
  }

  get total() {
    return this.#items.reduce((t, { item, qty }) => t + item.price * qty, 0);
  }

  static fromJSON(json) {
    const cart = new Cart(json.owner);
    json.items.forEach((i) => cart.add(i.item, i.qty));
    return cart;
  }

  async *lines() {
    for (const line of this.#items) {
      yield line;
    }
  }

  onChange = (event) => {
    this.owner = event.target.value;
  };
}

class EmptyCartError extends Error {}

export function CartView({ cart }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="cart">
      <p>Don't forget: {cart.total} {"{"}</p>
      <button onClick={() => setOpen(!open)}>Toggle</button>
    </div>
  );
}
