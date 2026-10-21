//! Fuzzing of expand and collapse (ADR 0001 "Round trip"). The Go peer is `FuzzExpandCollapse` in go/fuzz_test.go.
//!
//! On every input that parses and expands: the input is left alone, `collapse(expand(x))` with the provenance gives
//! `x` back, and `expand` is idempotent. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{
    bind_grammar, collapse, expand, parse, Context, Presence, ScalarType, Shape, ShapeType, Value,
};
use libfuzzer_sys::fuzz_target;

fn shape(shape_type: ShapeType, presence: Presence) -> Shape {
    Shape { shape_type, presence, nullable: false, has_default: false }
}

fn fuzz_shape() -> Shape {
    let list = |min| shape(ShapeType::List { min_items: Some(min) }, Presence::Optional);
    let inner = ShapeType::Composite(vec![
        ("d".into(), shape(ShapeType::Scalar(ScalarType::Boolean), Presence::Mandatory)),
        ("e".into(), list(1)),
        ("f".into(), shape(ShapeType::Opaque, Presence::Optional)),
    ]);
    shape(
        ShapeType::Composite(vec![
            ("a".into(), shape(ShapeType::Scalar(ScalarType::String), Presence::Mandatory)),
            ("b".into(), Shape { nullable: true, ..shape(ShapeType::Scalar(ScalarType::Integer), Presence::Optional) }),
            ("c".into(), shape(inner, Presence::Optional)),
            ("g".into(), list(2)),
        ]),
        Presence::Optional,
    )
}

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && text.starts_with("CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let Ok(n) = parse(text) else {
        return;
    };
    let before = n.clone();
    let a: Vec<String> = ["value", "access", "modify", "inherit", "default_injection", "conformance"]
        .iter()
        .map(|k| (*k).to_string())
        .collect();
    let one = Value::Int(1);
    let g = bind_grammar(Some(&one), Some(&one)).expect("grammar 1");
    let shape = fuzz_shape();
    let result = expand(&n, &shape, &a, g, &Context::default());
    assert!(n.same(&before), "expand changed its input {text:?}");
    let Ok((expanded, prov)) = result else {
        return;
    };
    let back = collapse(&expanded, &shape, &a, g, &prov, &Context::default()).expect("collapse of an expanded document");
    assert!(back.same(&n), "collapse(expand(x)) != x for {text:?}");
    let (again, _) = expand(&expanded, &shape, &a, g, &Context::default()).expect("expand of an expanded document");
    assert!(again.same(&expanded), "expand is not idempotent for {text:?}");
});
