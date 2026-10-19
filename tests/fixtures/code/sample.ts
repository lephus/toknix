// Sample for `toknix outline` (P3-T2): TypeScript.
import { Injectable } from "./di";

export interface Item {
  id: string;
  price: number;
  tags?: string[];
}

export type Money = number;

export type Result<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

export enum Currency {
  VND = "VND",
  USD = "USD",
}

const BRACE = "}";

export function parse(input: string): number;
export function parse(input: number): number;
export function parse(input: string | number): number {
  return typeof input === "string" ? Number(input.replace("{", "")) : input;
}

export function byId<T extends { id: string }>(items: T[], id: string): T | undefined {
  return items.find((i) => i.id === id);
}

export async function loadAll(
  urls: string[],
  retries = 2,
): Promise<Map<string, Item[]>> {
  const out = new Map<string, Item[]>();
  for (const url of urls) {
    out.set(url, await fetchItems(url, retries));
  }
  return out;
}

async function fetchItems(url: string, retries: number): Promise<Item[]> {
  const attempt = async (n: number): Promise<Item[]> => {
    try {
      return (await fetch(url)).json();
    } catch (e) {
      if (n === 0) throw e;
      return attempt(n - 1);
    }
  };
  return attempt(retries);
}

export abstract class Repository<T extends Item> {
  protected readonly items = new Map<string, T>();

  abstract validate(item: T): boolean;

  public save(item: T): void {
    if (!this.validate(item)) {
      throw new Error(`invalid ${item.id}`);
    }
    this.items.set(item.id, item);
  }

  get size(): number {
    return this.items.size;
  }
}

@Injectable({
  scope: "singleton",
  deps: ["{config}"],
})
export class ItemRepository extends Repository<Item> {
  private static instance?: ItemRepository;

  constructor(private readonly prefix: string = "item") {
    super();
  }

  validate(item: Item): boolean {
    return item.id.startsWith(this.prefix) && item.price >= 0;
  }

  static shared(): ItemRepository {
    return (this.instance ??= new ItemRepository());
  }

  private format = (item: Item): string => `${this.prefix}:${item.id}`;
}

export const total = (items: Item[]): Money =>
  items.reduce((sum, i) => sum + i.price, 0);
