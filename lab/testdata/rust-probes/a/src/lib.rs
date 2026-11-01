use std::fmt;

pub struct S;

impl fmt::Display for S {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "a")
    }
}

/// a trait impl reached through format!
pub fn show_s() -> String {
    format!("{}", S)
}

/// a generic function: the trait impl is reached through the type parameter
pub fn show_generic<T: fmt::Display>(t: T) -> String {
    format!("{}", t)
}

/// a lifetime parameter, and a helper that changes
pub fn pick<'a>(s: &'a str) -> &'a str {
    helper(s)
}

fn helper(s: &str) -> &str {
    &s[..1]
}

/// a macro of the standard library whose arguments call a function that changes
pub fn macro_sum() -> u8 {
    vec![limit(), limit()].iter().sum()
}

fn limit() -> u8 {
    1
}
