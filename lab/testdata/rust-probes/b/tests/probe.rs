use probe::*;

#[test]
fn t_trait_impl() {
    assert_eq!(show_s(), "a");
}

#[test]
fn t_generic() {
    assert_eq!(show_generic(S), "a");
}

#[test]
fn t_lifetime() {
    assert_eq!(pick("xy"), "x");
}

#[test]
fn t_macro() {
    assert_eq!(macro_sum(), 2);
}
