// A macro of the crate itself: the call site has no edge to its body.
macro_rules! one {
    () => {
        2
    };
}

pub fn f() -> i32 {
    one!()
}
