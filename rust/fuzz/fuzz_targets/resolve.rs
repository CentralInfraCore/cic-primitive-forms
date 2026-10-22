//! Fuzzing of `resolve_policy`. The Go peer is `FuzzResolvePolicy` in go/fuzz_test.go.
//!
//! On every input that parses and that `normalize` accepts: the resolution does not panic, leaves the object alone, resolves the
//! same positions whatever the policy state of the root is, and never turns an unknown root into a permissive rule.
//! `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{
    bind_grammar, normalize, parse, resolve_policy, Context, DefaultValue, Node, PolicyRule, PolicySurface, Presence,
    RuleKind, ScalarType, Shape, ShapeType, Value,
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
    let a: Vec<String> = ["value", "access", "modify", "inherit", "default_injection", "conformance"]
        .iter()
        .map(|k| (*k).to_string())
        .collect();
    let one = Value::Int(1);
    let g = bind_grammar(Some(&one), Some(&one)).expect("grammar 1");
    let shape = fuzz_shape();
    let Ok(res) = normalize(&n, &shape, &a, g, &Context::default()) else {
        return;
    };
    let before = res.object.clone();
    let policy = PolicySurface {
        declared: true,
        default_access: Some(PolicyRule::Patterns(vec!["O=r".to_string()])),
        default_modify: None,
        surface_rules: Vec::new(),
        inherit_policy: cic_primitive_forms::Mode::Inherit,
        inherit_policy_declared: false,
    };
    let known = resolve_policy(&res.object, &shape, &policy, "config_surface", &a, g, &[]);
    assert!(res.object.same(&before), "the resolution changed the object for {text:?}");
    let Ok(known) = known else {
        return;
    };
    let unknown = resolve_policy(&res.object, &shape, &policy, "config_surface", &a, g, &["$".to_string()])
        .expect("the same document resolves with an unknown root");
    assert!(known.keys().eq(unknown.keys()), "the policy state of the root changed the positions for {text:?}");
    for e in unknown.values() {
        for r in [&e.access, &e.modify] {
            // an unknown root may be answered by a local rule, never by the system layer
            assert!(
                r.kind != RuleKind::Open && r.kind != RuleKind::Owner,
                "an unknown root gave a permissive system rule for {text:?}"
            );
        }
    }
});
