//! Fuzzing of `apply_defaults`. The Go peer is `FuzzApplyDefaults` in go/fuzz_test.go.
//!
//! On every input that parses: the input is left alone; the result validates; taking out the values that came from a
//! default gives the input back (no authored value is changed or dropped); applying the defaults again changes
//! nothing and finds every value authored. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{
    apply_defaults, parse, validate, DefaultValue, Node, Presence, ScalarType, Shape, ShapeType, Value,
    VALUE_AUTHORED, VALUE_DEFAULT,
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

fn remove_default(doc: &mut Node, path: &str) {
    let parts: Vec<&str> = path.trim_start_matches("$.").split('.').collect();
    let mut holder = doc;
    for p in &parts[..parts.len() - 1] {
        let Value::Map(entries) = &mut holder.value else { return };
        let Some(e) = entries.iter_mut().find(|e| e.key == *p) else { return };
        holder = &mut e.value;
    }
    if let Value::Map(entries) = &mut holder.value {
        let last = parts[parts.len() - 1];
        entries.retain(|e| e.key != last);
    }
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
    let shape = fuzz_shape();
    let result = apply_defaults(Some(&n), &shape, &a);
    assert!(n.same(&before), "apply_defaults changed its input {text:?}");
    let Ok(res) = result else {
        return;
    };
    let Some(doc) = res.document else {
        return;
    };
    let v = validate(Some(&doc), &shape, &a).expect("the result is a supported document");
    assert!(v.violations.is_empty(), "the result does not validate for {text:?}");
    let again = apply_defaults(Some(&doc), &shape, &a).expect("apply_defaults of a result");
    assert!(
        again.document.as_ref().is_some_and(|d| d.same(&doc)),
        "applying the defaults again changed the result for {text:?}"
    );
    assert!(
        again.origins.iter().all(|o| o.origin == VALUE_AUTHORED),
        "a value is not authored after the defaults were applied for {text:?}"
    );
    let mut stripped = doc.clone();
    for o in res.origins.iter().filter(|o| o.origin == VALUE_DEFAULT) {
        remove_default(&mut stripped, &o.path);
    }
    assert!(stripped.same(&n), "an authored value was changed or dropped for {text:?}");
});
