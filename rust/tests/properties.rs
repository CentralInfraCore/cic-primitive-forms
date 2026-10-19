//! Property tests for parse and emit (PHASES.md: required for parse/emit from P1). The Go peer
//! (`go/properties_test.go`) checks the same properties.
//!
//! * emit then parse gives back the value, for any value of the supported subset, including
//!   strings with every kind of character;
//! * emit is a fixed point under parse;
//! * parse never panics, whatever the text, and the positions it reports lie inside the text;
//! * recognition depends on the key set only, and recognize does not change its input.

use cic_primitive_forms::{emit, parse, recognize, Class, Entry, Node, Position, Value};
use proptest::prelude::*;

fn key_strategy() -> impl Strategy<Value = String> {
    prop_oneof![
        "[a-z]{1,4}",
        "\\PC{0,6}",
        "[a-z]{500,1100}",
        Just("value".to_string()),
        Just("a: b".to_string()),
        Just("yes".to_string()),
        Just("<<".to_string()),
        Just("".to_string()),
    ]
}

fn scalar() -> impl Strategy<Value = Node> {
    prop_oneof![
        Just(Node::new(Value::Null)),
        any::<bool>().prop_map(|b| Node::new(Value::Bool(b))),
        any::<i64>().prop_map(|i| Node::new(Value::Int(i))),
        any::<String>().prop_map(|s| Node::new(Value::Str(s))),
        "\\PC{0,8}".prop_map(|s| Node::new(Value::Str(s))),
    ]
}

fn node() -> impl Strategy<Value = Node> {
    scalar().prop_recursive(4, 48, 4, |inner| {
        prop_oneof![
            prop::collection::vec(inner.clone(), 0..4).prop_map(|v| Node::new(Value::Seq(v))),
            prop::collection::btree_map(key_strategy(), inner, 0..4).prop_map(|m| {
                Node::new(Value::Map(
                    m.into_iter()
                        .map(|(key, value)| Entry {
                            key,
                            key_pos: None,
                            value,
                        })
                        .collect(),
                ))
            }),
        ]
    })
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(512))]

    #[test]
    fn emit_then_parse_gives_back_the_value(n in node()) {
        let text = emit(&n);
        let back = parse(&text).unwrap_or_else(|e| panic!("{text:?}: {e:?}"));
        prop_assert!(back.same(&n), "{:?} -> {:?}", text, back);
    }

    #[test]
    fn emit_is_a_fixed_point_under_parse(n in node()) {
        let once = emit(&n);
        let twice = emit(&parse(&once).unwrap());
        prop_assert_eq!(once, twice);
    }

    #[test]
    fn parse_never_panics_and_its_positions_lie_inside_the_text(text in "\\PC{0,64}|[ -~\n\r\t]{0,64}") {
        if let Ok(n) = parse(&text) {
            check_positions(&n, &text);
            // What parses also survives emit and parse.
            let back = parse(&emit(&n)).unwrap();
            prop_assert!(back.same(&n));
        }
    }

    #[test]
    fn recognition_follows_the_key_set_and_leaves_the_input_alone(
        values in prop::collection::vec(node(), 6),
        extra in prop::option::of(key_strategy()),
    ) {
        let a: Vec<String> = ["value", "access", "modify", "inherit", "default_injection", "conformance"]
            .iter().map(|k| (*k).to_string()).collect();
        let mut entries: Vec<Entry> = a.iter().cloned().zip(values)
            .map(|(key, value)| Entry { key, key_pos: None, value }).collect();
        let mut expect = Class::Expanded;
        if let Some(k) = extra {
            if !a.contains(&k) {
                entries.push(Entry { key: k, key_pos: None, value: Node::new(Value::Null) });
                expect = Class::Error;
            }
        }
        let n = Node::new(Value::Map(entries));
        let before = n.clone();
        let out = recognize(&n, &Position::Opaque, &a).unwrap();
        prop_assert_eq!(out.class, expect);
        prop_assert!(n.same(&before));
    }
}

fn check_positions(n: &Node, text: &str) {
    if let Some(p) = n.pos {
        assert!(
            p.line >= 1 && p.column >= 1 && p.offset <= text.len(),
            "{p:?} in {text:?}"
        );
        assert!(
            text.is_char_boundary(p.offset),
            "{p:?} splits a character of {text:?}"
        );
    }
    match &n.value {
        Value::Seq(items) => items.iter().for_each(|i| check_positions(i, text)),
        Value::Map(entries) => entries.iter().for_each(|e| {
            assert!(e.key_pos.is_some_and(|p| p.offset <= text.len()));
            check_positions(&e.value, text);
        }),
        _ => {}
    }
}
