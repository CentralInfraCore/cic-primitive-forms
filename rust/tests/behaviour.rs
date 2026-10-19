//! Behaviour of the slice beyond the corpus: limits, positions, ordering, refusals and
//! the guarantee that validation changes nothing.

use cic_primitive_forms::{
    emit, parse, parse_with, recognize, Class, Error, Limits, Location, Node, Phase, Pos, Position,
    Value,
};

fn a() -> Vec<String> {
    [
        "value",
        "access",
        "modify",
        "inherit",
        "default_injection",
        "conformance",
    ]
    .iter()
    .map(|k| (*k).to_string())
    .collect()
}

const EXPANDED: &str = "value: 1\naccess: inherit\nmodify: inherit\ninherit: true\ndefault_injection: null\nconformance: implemented\n";

#[test]
fn limits_are_enforced_and_explicit() {
    let small = Limits {
        max_bytes: 8,
        ..Limits::default()
    };
    assert!(matches!(
        parse_with("a: 1234567890", small),
        Err(Error::Limit(_))
    ));

    let shallow = Limits {
        max_depth: 2,
        ..Limits::default()
    };
    assert!(parse_with("[[1]]", shallow).is_ok());
    assert!(matches!(
        parse_with("[[[1]]]", shallow),
        Err(Error::Limit(_))
    ));
    assert!(matches!(
        parse_with("a: {b: {c: 1}}", shallow),
        Err(Error::Limit(_))
    ));

    let few = Limits {
        max_nodes: 3,
        ..Limits::default()
    };
    assert!(parse_with("[1, 2]", few).is_ok());
    assert!(matches!(parse_with("[1, 2, 3]", few), Err(Error::Limit(_))));
    assert_eq!(
        parse_with("[1, 2, 3]", few).unwrap_err().code(),
        "LIMIT_EXCEEDED"
    );
}

#[test]
fn deep_nesting_is_refused_not_overflowed() {
    // Past our limit but below the YAML library's own: ours answers.
    let text = "[".repeat(200) + &"]".repeat(200);
    assert!(matches!(parse(&text), Err(Error::Limit(_))));
    // Far past both: whoever answers first, it is an error and not a stack overflow.
    let text = "[".repeat(100_000) + &"]".repeat(100_000);
    assert!(parse(&text).is_err());
}

#[test]
fn positions_follow_adr_0007() {
    let n = parse("a: 1\r\nb:\r\n  - x\r\n").unwrap();
    let Value::Map(entries) = &n.value else {
        panic!()
    };
    assert_eq!(
        n.pos,
        Some(Pos {
            line: 1,
            column: 1,
            offset: 0
        })
    );
    assert_eq!(
        entries[1].key_pos,
        Some(Pos {
            line: 2,
            column: 1,
            offset: 6
        })
    );
    let Value::Seq(items) = &entries[1].value.value else {
        panic!()
    };
    assert_eq!(
        items[0].pos,
        Some(Pos {
            line: 3,
            column: 5,
            offset: 14
        })
    );

    // A lone CR is a line break; a tab is one column.
    let n = parse("a: 1\rb: [\tz]\r").unwrap();
    let Value::Map(entries) = &n.value else {
        panic!()
    };
    assert_eq!(
        entries[1].key_pos,
        Some(Pos {
            line: 2,
            column: 1,
            offset: 5
        })
    );
    let Value::Seq(items) = &entries[1].value.value else {
        panic!()
    };
    assert_eq!(
        items[0].pos,
        Some(Pos {
            line: 2,
            column: 6,
            offset: 10
        })
    );
}

#[test]
fn the_first_duplicate_in_source_order_is_the_one_reported() {
    match parse("a: 1\nb: {x: 1, x: 2}\na: 3\n") {
        Err(Error::Contract(v)) => {
            assert_eq!(v.len(), 1);
            assert_eq!(v[0].code, "DUPLICATE_KEY");
            assert_eq!(v[0].phase, Phase::Parse);
            assert_eq!(
                v[0].location,
                Location::At(Pos {
                    line: 2,
                    column: 11,
                    offset: 15
                })
            );
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn recognition_is_by_key_set_and_never_by_value() {
    let pos = Position::Opaque;
    // Same keys, hostile values: still expanded.
    let n = parse(&EXPANDED.replace("inherit: true", "inherit: [1]")).unwrap();
    assert_eq!(recognize(&n, &pos, &a()).unwrap().class, Class::Expanded);
    // One key short: data.
    let n = parse("value: 1\naccess: inherit\n").unwrap();
    assert_eq!(recognize(&n, &pos, &a()).unwrap().class, Class::Short);
    // The empty map and the empty list are short.
    for t in ["{}", "[]", "x"] {
        let n = parse(t).unwrap();
        assert_eq!(recognize(&n, &pos, &a()).unwrap().class, Class::Short);
    }
    // An empty A makes every mapping expanded only when it is empty too.
    let n = parse("{}").unwrap();
    assert_eq!(recognize(&n, &pos, &[]).unwrap().class, Class::Expanded);
}

#[test]
fn a_list_of_records_recognizes_each_field_and_orders_its_errors() {
    let record = Position::ListOf(vec![
        ("host".into(), Position::Scalar),
        ("port".into(), Position::Scalar),
    ]);
    let text =
        "- host: {a: 1}\n  port: 22\n- port: {b: 2}\n  colour: red\n  extra: 1\n- host: ok\n";
    let out = recognize(&parse(text).unwrap(), &record, &a()).unwrap();
    assert_eq!(out.class, Class::Short);
    // MAP_AT_SCALAR_POSITION and UNKNOWN_FIELD, one each (smallest path), by path: [0].host
    // sorts before [1].colour.
    let codes: Vec<&str> = out.violations.iter().map(|v| v.code).collect();
    assert_eq!(codes, ["MAP_AT_SCALAR_POSITION", "UNKNOWN_FIELD"]);
    assert_eq!(
        out.violations[1].location,
        Location::At(Pos {
            line: 4,
            column: 3,
            offset: 43
        })
    );

    // A recognition error anywhere hides the validation errors (phases do not mix).
    let text = format!(
        "- host: {{a: 1}}\n- host:\n{}",
        EXPANDED
            .lines()
            .map(|l| format!("    {l}\n"))
            .collect::<String>()
            + "    extra: 1\n"
    );
    let out = recognize(&parse(&text).unwrap(), &record, &a()).unwrap();
    assert_eq!(out.class, Class::Error);
    assert_eq!(out.violations.len(), 1);
    assert_eq!(out.violations[0].code, "EXPANDED_WITH_EXTRA_KEY");
    assert_eq!(out.violations[0].phase, Phase::Recognize);
}

#[test]
fn shapes_without_a_rule_are_refused_not_accepted() {
    let structured = Position::Structured(vec!["host".into()]);
    let list = Position::ListOf(vec![("host".into(), Position::Scalar)]);
    let nested = Position::ListOf(vec![("inner".into(), list.clone())]);
    let cases: Vec<(&str, &Position)> = vec![
        ("5", &structured),
        ("5", &list),
        ("- 5", &list),
        ("- inner: [1]", &nested),
    ];
    for (text, position) in cases {
        let n = parse(text).unwrap();
        assert!(
            matches!(recognize(&n, position, &a()), Err(Error::Unsupported(_))),
            "{text}"
        );
    }
}

#[test]
fn recognize_does_not_change_its_input() {
    let n = parse(&format!("{EXPANDED}extra: 1\n")).unwrap();
    let before = n.clone();
    let _ = recognize(&n, &Position::Scalar, &a());
    assert!(n.same(&before));
    assert_eq!(n.pos, before.pos);
}

#[test]
fn same_ignores_position_and_mapping_order_but_not_value_or_type() {
    let x = parse("a: 1\nb: [1, 2]\n").unwrap();
    let y = parse("b: [1, 2]\na: 1\n").unwrap();
    assert!(x.same(&y));
    for other in [
        "a: 1\nb: [2, 1]\n",
        "a: 2\nb: [1, 2]\n",
        "a: '1'\nb: [1, 2]\n",
        "a: 1\n",
        "a: 1\nc: [1, 2]\n",
    ] {
        assert!(!x.same(&parse(other).unwrap()), "{other}");
    }
    assert!(Node::new(Value::Bool(true)).same(&Node::new(Value::Bool(true))));
    assert!(!Node::new(Value::Bool(true)).same(&Node::new(Value::Null)));
    assert!(Node::new(Value::Null).same(&Node::new(Value::Null)));
    assert!(!Node::new(Value::Int(1)).same(&Node::new(Value::Int(2))));
}

#[test]
fn emit_is_stable_under_reparse() {
    let n = parse("z: [1, {y: 'a b', x: ''}]\nk: null\n").unwrap();
    let once = emit(&n);
    assert_eq!(emit(&parse(&once).unwrap()), once);
}

#[test]
fn error_codes_outside_the_registry_are_named() {
    assert_eq!(
        Error::Unsupported(String::new()).code(),
        "UNSUPPORTED_SYNTAX"
    );
    assert_eq!(Error::Syntax(String::new()).code(), "YAML_SYNTAX");
    assert_eq!(Phase::Parse.as_str(), "parse");
    assert_eq!(Phase::Recognize.as_str(), "recognize");
    assert_eq!(Phase::Validate.as_str(), "validate");
}
