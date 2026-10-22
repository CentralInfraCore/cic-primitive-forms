//! Fuzzing of presence validation. The Go peer is `FuzzValidate` in go/fuzz_test.go.
//!
//! On every input that parses: validation does not panic, leaves the document alone, and gives
//! either an unsupported answer or a response of one phase with no repeated (path, code).
//! `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{parse, validate, Presence, ScalarType, Shape, ShapeType};
use libfuzzer_sys::fuzz_target;

fn shape(shape_type: ShapeType, presence: Presence) -> Shape {
    Shape { shape_type, presence, nullable: false, has_default: false, default: None }
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
            (
                "b".into(),
                Shape {
                    nullable: true,
                    has_default: true, default: None,
                    ..shape(ShapeType::Scalar(ScalarType::Integer), Presence::Optional)
                },
            ),
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
    let result = validate(Some(&n), &fuzz_shape(), &a);
    assert!(n.same(&before), "validation changed its input {text:?}");
    if let Ok(v) = result {
        let mut seen = std::collections::BTreeSet::new();
        let mut phases = std::collections::BTreeSet::new();
        for x in &v.violations {
            assert!(seen.insert((x.logical_path.clone(), x.code)), "repeated {x:?} for {text:?}");
            phases.insert(x.phase.as_str());
        }
        assert!(phases.len() <= 1, "more than one phase for {text:?}");
    }
});
