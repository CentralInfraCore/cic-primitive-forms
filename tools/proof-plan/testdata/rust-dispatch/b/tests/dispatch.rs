use rdispatch::*;

#[test]
fn t_to_string() {
    assert_eq!(to_string_s(), "a");
}

#[test]
fn t_dyn_display() {
    assert_eq!(dyn_display(), "a");
}

#[test]
fn t_impl_arg() {
    assert_eq!(impl_arg(), "a");
}

#[test]
fn t_question_from() {
    assert_eq!(question(), "a");
}

#[test]
fn t_for_next() {
    assert_eq!(sum_for(), 6);
}

#[test]
fn t_add() {
    assert_eq!(add_op(), 3);
}

#[test]
fn t_eq() {
    assert!(eq_op());
}

#[test]
fn t_index() {
    assert_eq!(index_op(), 1);
}

#[test]
fn t_deref() {
    assert_eq!(deref_call(), 1);
}

#[test]
fn t_drop() {
    assert_eq!(drop_effect(), 1);
}

#[test]
fn t_sort_ord() {
    assert_eq!(sort_first(), 1);
}
