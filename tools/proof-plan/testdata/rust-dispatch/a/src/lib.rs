use std::cell::Cell;
use std::cmp::Ordering;
use std::fmt;
use std::ops::{Add, Deref, Index};

/// Display, reached through to_string, a trait object and an `impl Display` argument
pub struct S;

impl fmt::Display for S {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "a")
    }
}

pub fn to_string_s() -> String {
    S.to_string()
}

pub fn dyn_display() -> String {
    let b: Box<dyn fmt::Display> = Box::new(S);
    format!("{}", b)
}

fn show(x: impl fmt::Display) -> String {
    format!("{}", x)
}

pub fn impl_arg() -> String {
    show(S)
}

/// `?` converts the error with From
pub struct E(pub &'static str);
pub struct Other;

impl From<Other> for E {
    fn from(_: Other) -> E {
        E("a")
    }
}

fn fails() -> Result<(), Other> {
    Err(Other)
}

pub fn question() -> &'static str {
    fn inner() -> Result<(), E> {
        fails()?;
        Ok(())
    }
    match inner() {
        Err(E(s)) => s,
        Ok(()) => "ok",
    }
}

/// `for` calls next on a type of the crate
pub struct Count(pub u32);

impl Iterator for Count {
    type Item = u32;
    fn next(&mut self) -> Option<u32> {
        if self.0 < 3 {
            self.0 += 1;
            Some(self.0)
        } else {
            None
        }
    }
}

pub fn sum_for() -> u32 {
    let mut t = 0;
    for x in Count(0) {
        t += x;
    }
    t
}

/// operators
pub struct V(pub i32);

impl Add for V {
    type Output = V;
    fn add(self, o: V) -> V {
        V(self.0 + o.0)
    }
}

pub fn add_op() -> i32 {
    (V(1) + V(2)).0
}

pub struct P(pub i32);

impl PartialEq for P {
    fn eq(&self, o: &P) -> bool {
        self.0 == o.0
    }
}

pub fn eq_op() -> bool {
    P(1) == P(1)
}

pub struct Ix;

impl Index<usize> for Ix {
    type Output = i32;
    fn index(&self, _: usize) -> &i32 {
        &1
    }
}

pub fn index_op() -> i32 {
    Ix[0]
}

/// a method found through Deref
pub struct Inner(pub i32);

impl Inner {
    pub fn val(&self) -> i32 {
        self.0
    }
}

pub struct W(pub Inner, pub Inner);

impl Deref for W {
    type Target = Inner;
    fn deref(&self) -> &Inner {
        &self.0
    }
}

pub fn deref_call() -> i32 {
    W(Inner(1), Inner(2)).val()
}

/// Drop at the end of a scope
pub struct D<'a>(pub &'a Cell<i32>);

impl Drop for D<'_> {
    fn drop(&mut self) {
        self.0.set(1);
    }
}

pub fn drop_effect() -> i32 {
    let c = Cell::new(0);
    {
        let _d = D(&c);
    }
    c.get()
}

/// sort calls Ord::cmp
#[derive(PartialEq, Eq)]
pub struct O(pub i32);

impl PartialOrd for O {
    fn partial_cmp(&self, o: &O) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}

impl Ord for O {
    fn cmp(&self, o: &O) -> Ordering {
        self.0.cmp(&o.0)
    }
}

pub fn sort_first() -> i32 {
    let mut v = vec![O(2), O(1)];
    v.sort();
    v[0].0
}
