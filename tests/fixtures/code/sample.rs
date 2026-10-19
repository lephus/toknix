//! Sample for `tokopt outline` (P3-T2): Rust.
use std::collections::HashMap;
use std::fmt;

const BRACE: char = '{';
static GREETING: &str = "hello {world}";

/// A product. The text `fn fake() {` in a doc comment is not code.
#[derive(Debug, Clone, PartialEq)]
pub struct Item {
    pub sku: String,
    pub price: u64,
}

pub struct Unit;

pub struct Pair(pub u32, pub u32);

#[derive(Debug)]
pub enum Event {
    Added { sku: String, qty: u32 },
    Removed(String),
}

pub trait Store {
    fn get(&self, sku: &str) -> Option<&Item>;

    fn contains(&self, sku: &str) -> bool {
        self.get(sku).is_some()
    }
}

pub struct Cart<'a> {
    owner: &'a str,
    items: HashMap<String, u32>,
}

impl<'a> Cart<'a> {
    pub fn new(owner: &'a str) -> Self {
        Cart { owner, items: HashMap::new() }
    }

    pub fn add(&mut self, sku: &str, qty: u32) -> &mut Self {
        *self.items.entry(sku.to_string()).or_insert(0) += qty;
        self
    }

    pub fn total<S: Store>(&self, store: &S) -> u64 {
        fn line(price: u64, qty: u32) -> u64 {
            price * u64::from(qty)
        }
        self.items
            .iter()
            .filter_map(|(sku, qty)| store.get(sku).map(|i| line(i.price, *qty)))
            .sum()
    }
}

impl fmt::Display for Cart<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{{cart of {}}}", self.owner)
    }
}

impl<T> From<Vec<T>> for Pair
where
    T: Into<u32> + Copy,
{
    fn from(v: Vec<T>) -> Self {
        Pair(v[0].into(), v[1].into())
    }
}

pub async fn fetch(url: &str) -> Result<String, Box<dyn std::error::Error>> {
    let body = format!("GET {} {{}}", url);
    Ok(body)
}

pub(crate) fn parse_qty(s: &str) -> u32 {
    s.trim().parse().unwrap_or(0)
}

mod tests {
    use super::*;

    #[test]
    fn adds_items() {
        let mut cart = Cart::new("ann");
        cart.add("a", 2);
        assert_eq!(cart.items["a"], 2);
    }
}
