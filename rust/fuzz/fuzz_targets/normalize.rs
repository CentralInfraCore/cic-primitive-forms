//! Fuzzing of `normalize` and `denormalize`. The Go peer is `FuzzNormalize` in go/fuzz_test.go.
//!
//! On every input that parses and that `normalize` accepts: the input is left alone, denormalizing the result gives the
//! input back as a value, and normalizing the result again changes neither the object nor the origins.
//! `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{
    bind_grammar, denormalize, normalize, parse, renormalize, Context, DefaultValue, Node, Presence, ScalarType, Shape,
    ShapeType, Value,
};
use libfuzzer_sys::fuzz_target;

fn shape(shape_type: ShapeType, presence: Presence) -> Shape {
    Shape { shape_type, presence, nullable: false, has_default: false, default: None }
}

fn with_default(mut s: Shape, d: Value) -> Shape {
    s.has_default = true;
    s.default = Some(DefaultValue(Node::new(d)));
    s
}

fn fuzz_shape() -> Shape {
    let optional = |t| shape(t, Presence::Optional);
    let inner = Shape {
        nullable: true,
        ..shape(
            ShapeType::Composite(vec![
                ("d".into(), with_default(optional(ShapeType::Scalar(ScalarType::Boolean)), Value::Bool(true))),
                ("e".into(), with_default(optional(ShapeType::Scalar(ScalarType::String)), Value::Str("x".into()))),
            ]),
            Presence::Optional,
        )
    };
    shape(
        ShapeType::Composite(vec![
            ("a".into(), shape(ShapeType::Scalar(ScalarType::String), Presence::Mandatory)),
            (
                "b".into(),
                Shape {
                    nullable: true,
                    ..with_default(optional(ShapeType::Scalar(ScalarType::Integer)), Value::Int(7))
                },
            ),
            ("c".into(), inner),
            (
                "g".into(),
                with_default(optional(ShapeType::Set { item: ScalarType::String, min_items: None }), Value::Seq(vec![])),
            ),
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
    let ctx = Context::default();
    let result = normalize(&n, &shape, &a, g, &ctx);
    assert!(n.same(&before), "normalize changed its input {text:?}");
    let Ok(res) = result else {
        return;
    };
    let back = denormalize(&res, &shape, &a, g, &ctx).expect("denormalize of a normalized document");
    assert!(back.same(&n), "denormalize(normalize(x)) != x for {text:?}");
    let again = renormalize(&res, &shape, &a, g, &ctx).expect("renormalize of a result");
    assert!(
        again.object.same(&res.object) && again.value_origins == res.value_origins,
        "not idempotent on the pair for {text:?}"
    );
});
