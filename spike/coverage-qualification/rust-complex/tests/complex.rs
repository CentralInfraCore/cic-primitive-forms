// Tests of the second Rust sample. The `reduced` feature drops the cases marked cfg(not(feature = "reduced")).
use cov_complex::*;

#[test]
fn t_else_if() {
    assert_eq!(else_if(-1), "neg");
    assert_eq!(else_if(5), "small");
    #[cfg(not(feature = "reduced"))]
    {
        assert_eq!(else_if(0), "zero");
        assert_eq!(else_if(50), "big");
    }
}

#[test]
fn t_find() {
    let m = vec![vec![1, 2], vec![3]];
    assert!(find(&m, 3));
    #[cfg(not(feature = "reduced"))]
    assert!(!find(&m, 9));
}

#[test]
fn t_arms() {
    assert_eq!(arms(0), 2);
    assert_eq!(arms(1), 10);
    assert_eq!(arms(7), 6);
    #[cfg(not(feature = "reduced"))]
    {
        assert_eq!(arms(150), 4);
        assert_eq!(arms(300), 3);
        assert_eq!(arms(8), 5);
    }
}

#[test]
fn t_drain() {
    assert_eq!(drain(vec![1, 2]), 3);
    #[cfg(not(feature = "reduced"))]
    {
        assert_eq!(drain(vec![-1, 5]), 5);
        assert_eq!(drain(vec![1, 200, 3]), 3);
    }
}

#[test]
fn t_quest2() {
    assert_eq!(quest2("1", "2"), Ok(4));
    #[cfg(not(feature = "reduced"))]
    {
        assert!(quest2("x", "2").is_err());
        assert!(quest2("1", "y").is_err());
    }
}

#[test]
fn t_first_even() {
    assert_eq!(first_even(&[2]), Some(2));
    assert_eq!(first_even(&[]), None);
    #[cfg(not(feature = "reduced"))]
    assert_eq!(first_even(&[3]), None);
}

#[test]
fn t_closure_cond() {
    assert_eq!(closure_cond(&[2, 3]), 1);
    #[cfg(not(feature = "reduced"))]
    assert_eq!(closure_cond(&[-1, 0]), 0);
}

#[test]
fn t_generic() {
    assert_eq!(generic(1, 2), 2);
    #[cfg(not(feature = "reduced"))]
    assert_eq!(generic(2.0, 1.0), 2.0);
}

#[test]
fn t_iflet_chain() {
    assert_eq!(iflet_chain(Some(1), None), 1);
    assert_eq!(iflet_chain(None, Some(2)), 2);
    #[cfg(not(feature = "reduced"))]
    assert_eq!(iflet_chain(None, None), 0);
}

#[test]
fn t_loop_break() {
    assert_eq!(loop_break(3), 3);
}

#[test]
fn t_nested_cond() {
    assert_eq!(nested_cond(true, false, true), 1);
    assert_eq!(nested_cond(false, true, true), 2);
    #[cfg(not(feature = "reduced"))]
    {
        assert_eq!(nested_cond(true, true, false), 1);
        assert_eq!(nested_cond(true, false, false), 2);
    }
}

#[test]
fn t_for_continue() {
    assert_eq!(for_continue(&[1, 3]), 4);
    #[cfg(not(feature = "reduced"))]
    assert_eq!(for_continue(&[1, 2, 3]), 4);
}

#[test]
fn t_ret_early() {
    assert_eq!(ret_early(Some(9)), 9);
    assert_eq!(ret_early(None), 0);
    #[cfg(not(feature = "reduced"))]
    assert_eq!(ret_early(Some(3)), 4);
}
