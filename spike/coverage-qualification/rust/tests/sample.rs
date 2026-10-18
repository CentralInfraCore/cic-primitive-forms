// Tests of the qualification sample. The `reduced` feature drops the cases marked cfg(not(feature = "reduced")).

use cov_sample::*;

#[test]
fn t_cond() {
    assert_eq!(cond(1), 1);
#[cfg(not(feature = "reduced"))]
    assert_eq!(cond(-1), 2);
}
#[test]
fn t_let_opt() {
    assert_eq!(let_opt(Some(4)), 4);
    assert_eq!(let_opt(None), 0);
}
#[test]
fn t_m() {
    assert_eq!(m(0), "zero");
    assert_eq!(m(5), "other");
#[cfg(not(feature = "reduced"))]
    assert_eq!(m(11), "big");
}
#[test]
fn t_w() {
    assert_eq!(w(2), 3);
}
#[test]
fn t_wl() {
    assert_eq!(wl(vec![1, 2]), 3);
}
#[test]
fn t_fo() {
#[cfg(not(feature = "reduced"))]
    assert_eq!(fo(&[1]), 1);
    assert_eq!(fo(&[]), 0);
}
#[test]
fn t_lp() {
    assert_eq!(lp(), 3);
}
#[test]
fn t_sc() {
    assert!(sc(true, true));
    assert!(!sc(false, true));
#[cfg(not(feature = "reduced"))]
    assert!(!sc(true, false));
}
#[test]
fn t_so() {
    assert!(so(false, true));
    assert!(so(true, false));
#[cfg(not(feature = "reduced"))]
    assert!(!so(false, false));
}
#[test]
fn t_q() {
    assert_eq!(q("5"), Ok(6));
#[cfg(not(feature = "reduced"))]
    assert!(q("x").is_err());
}
