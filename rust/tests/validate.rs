//! Behaviour of presence validation beyond the corpus vectors. The Go peer has the same tests
//! (`go/validate_test.go`).

use cic_primitive_forms::{
    parse, validate, Error, Location, Node, Phase, Pos, Presence, ScalarType, Shape, ShapeType,
    Value, Violation,
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

fn shape(shape_type: ShapeType, presence: Presence) -> Shape {
    Shape {
        shape_type,
        presence,
        nullable: false,
        has_default: false,
    }
}

fn scalar(t: ScalarType, presence: Presence) -> Shape {
    shape(ShapeType::Scalar(t), presence)
}

fn composite(fields: Vec<(&str, Shape)>) -> Shape {
    shape(
        ShapeType::Composite(
            fields
                .into_iter()
                .map(|(n, s)| (n.to_string(), s))
                .collect(),
        ),
        Presence::Optional,
    )
}

fn run(text: &str, s: &Shape) -> Vec<(&'static str, Phase, Option<String>)> {
    let n = parse(text).unwrap();
    validate(Some(&n), s, &a())
        .unwrap()
        .violations
        .into_iter()
        .map(|v| (v.code, v.phase, v.logical_path))
        .collect()
}

fn codes(text: &str, s: &Shape) -> Vec<&'static str> {
    run(text, s).into_iter().map(|v| v.0).collect()
}

fn refused(text: &str, s: &Shape) -> bool {
    matches!(
        validate(Some(&parse(text).unwrap()), s, &a()),
        Err(Error::Unsupported(_))
    )
}

#[test]
fn absent_and_null_are_different_states() {
    let m = scalar(ScalarType::String, Presence::Mandatory);
    assert_eq!(
        validate(None, &m, &a()).unwrap().violations[0].code,
        "MISSING_MANDATORY"
    );
    assert_eq!(codes("null", &m), ["NULL_NOT_ALLOWED"]);
    assert_eq!(codes("~", &m), ["NULL_NOT_ALLOWED"]);
    let nullable = Shape {
        nullable: true,
        ..m.clone()
    };
    assert!(codes("null", &nullable).is_empty());
    // A nullable field is still mandatory: absent is not null.
    assert_eq!(
        validate(None, &nullable, &a()).unwrap().violations[0].code,
        "MISSING_MANDATORY"
    );
    // An optional field may be absent; the library creates no value for it.
    let o = scalar(ScalarType::String, Presence::Optional);
    let v = validate(None, &o, &a()).unwrap();
    assert!(v.violations.is_empty() && v.default_candidates.is_empty());
}

#[test]
fn a_default_is_a_candidate_only_for_an_absent_optional_field() {
    let with_default = |presence, nullable| Shape {
        has_default: true,
        nullable,
        ..scalar(ScalarType::Integer, presence)
    };
    let cand = |s: &Shape, n: Option<&Node>| validate(n, s, &a()).unwrap().default_candidates;
    let opt = with_default(Presence::Optional, false);
    assert_eq!(cand(&opt, None), ["$"]);
    // Never triggered by a value, null, zero or false.
    for text in ["0", "null", "5"] {
        let n = parse(text).unwrap();
        assert!(cand(&opt, Some(&n)).is_empty(), "{text}");
    }
    // A mandatory field is not defaulted (the schema forbids the pair), an undeclared one has none.
    assert!(cand(&with_default(Presence::Mandatory, false), None).is_empty());
    assert!(cand(&scalar(ScalarType::Integer, Presence::Optional), None).is_empty());
    // Nested: candidates are listed by canonical path, in path order, and nothing is applied.
    let s = composite(vec![
        (
            "zz",
            Shape {
                has_default: true,
                ..scalar(ScalarType::String, Presence::Optional)
            },
        ),
        (
            "aa",
            Shape {
                has_default: true,
                ..scalar(ScalarType::String, Presence::Optional)
            },
        ),
        (
            "a.b",
            Shape {
                has_default: true,
                ..scalar(ScalarType::String, Presence::Optional)
            },
        ),
        (
            "",
            Shape {
                has_default: true,
                ..scalar(ScalarType::String, Presence::Optional)
            },
        ),
    ]);
    let n = parse("{}").unwrap();
    let before = n.clone();
    assert_eq!(cand(&s, Some(&n)), ["$.a\\.b", "$.aa", "$.zz"]);
    assert!(n.same(&before));
}

#[test]
fn scalar_types_are_checked_and_a_mismatch_without_a_code_is_refused() {
    use ScalarType::{Boolean, Integer, String as Str};
    for (t, good) in [(Str, "'x'"), (Integer, "7"), (Boolean, "true")] {
        assert!(
            codes(good, &scalar(t, Presence::Mandatory)).is_empty(),
            "{good}"
        );
    }
    for (t, bad) in [
        (Str, "7"),
        (Str, "true"),
        (Str, "[1]"),
        (Integer, "'x'"),
        (Integer, "true"),
        (Integer, "[1]"),
        (Boolean, "1"),
        (Boolean, "'x'"),
        (Boolean, "[1]"),
    ] {
        assert!(
            refused(bad, &scalar(t, Presence::Mandatory)),
            "{bad} as {t:?}"
        );
    }
    // A mapping at a scalar position is the one mismatch that has a code.
    for t in [Str, Integer, Boolean] {
        assert_eq!(
            codes("{a: 1}", &scalar(t, Presence::Mandatory)),
            ["MAP_AT_SCALAR_POSITION"]
        );
    }
}

#[test]
fn min_items_is_inclusive_and_a_non_list_is_refused() {
    let list = |min_items| shape(ShapeType::List { min_items }, Presence::Optional);
    assert_eq!(codes("[1]", &list(Some(2))), ["MIN_ITEMS_NOT_MET"]);
    assert!(codes("[1, 2]", &list(Some(2))).is_empty());
    assert!(codes("[1, 2, 3]", &list(Some(2))).is_empty());
    assert!(codes("[]", &list(None)).is_empty());
    assert_eq!(codes("[]", &list(Some(1))), ["MIN_ITEMS_NOT_MET"]);
    assert!(refused("5", &list(None)));
    assert!(refused("{a: 1}", &list(None)));
}

#[test]
fn a_composite_reports_every_error_at_every_depth_in_path_order() {
    let inner = composite(vec![
        ("x", scalar(ScalarType::String, Presence::Mandatory)),
        ("y", scalar(ScalarType::String, Presence::Mandatory)),
    ]);
    let s = composite(vec![
        ("b", scalar(ScalarType::String, Presence::Mandatory)),
        ("a", inner),
        (
            "c",
            Shape {
                nullable: false,
                ..scalar(ScalarType::Integer, Presence::Optional)
            },
        ),
    ]);
    let got = run("a: {y: null, q: 1}\nc: null\nzz: 2\n", &s);
    let got: Vec<_> = got.iter().map(|(c, p, l)| (*c, *p, l.as_deref())).collect();
    assert_eq!(got.len(), 6);
    assert_eq!(
        got.iter().map(|g| g.2.unwrap()).collect::<Vec<_>>(),
        ["$.a.q", "$.a.x", "$.a.y", "$.b", "$.c", "$.zz"]
    );
    assert_eq!(
        got.iter().map(|g| g.0).collect::<Vec<_>>(),
        [
            "UNKNOWN_FIELD",
            "MISSING_MANDATORY",
            "NULL_NOT_ALLOWED",
            "MISSING_MANDATORY",
            "NULL_NOT_ALLOWED",
            "UNKNOWN_FIELD"
        ]
    );
    // A composite must be a mapping.
    assert!(refused("[1]", &s));
    assert!(refused("'x'", &s));
}

#[test]
fn anchors_follow_adr_0007() {
    let s = composite(vec![
        ("a", scalar(ScalarType::String, Presence::Mandatory)),
        ("b", scalar(ScalarType::String, Presence::Mandatory)),
        (
            "c",
            composite(vec![("d", scalar(ScalarType::String, Presence::Mandatory))]),
        ),
    ]);
    // a: empty scalar -> its key; b: explicit null -> its value; c.d missing -> the parent c.
    let n = parse("a:\nb: null\nc: {}\n").unwrap();
    let v = validate(Some(&n), &s, &a()).unwrap().violations;
    let at = |v: &Violation| match v.location {
        Location::At(p) => (p.line, p.column, p.offset),
        Location::Unavailable => panic!(),
    };
    let by = |path: &str| {
        v.iter()
            .find(|x| x.logical_path.as_deref() == Some(path))
            .unwrap()
    };
    assert_eq!(at(by("$.a")), (1, 1, 0));
    assert_eq!(at(by("$.b")), (2, 4, 6));
    assert_eq!(at(by("$.c.d")), (3, 4, 14));
    // A tilde is a null with text of its own: the value, not the key.
    let n = parse("a: ~\n").unwrap();
    let one = composite(vec![("a", scalar(ScalarType::String, Presence::Mandatory))]);
    let v = validate(Some(&n), &one, &a()).unwrap().violations;
    assert_eq!(
        v[0].location,
        Location::At(Pos {
            line: 1,
            column: 4,
            offset: 3
        })
    );
    // No text, no coordinates.
    let hand = Node::map(vec![("a", Node::new(Value::Null))]);
    let v = validate(Some(&hand), &one, &a()).unwrap().violations;
    assert_eq!(v[0].location, Location::Unavailable);
}

#[test]
fn recognition_runs_first_and_an_expanded_node_is_not_validated_yet() {
    let s = composite(vec![("a", scalar(ScalarType::String, Presence::Optional))]);
    let expanded = "value: 1\naccess: inherit\nmodify: inherit\ninherit: true\ndefault_injection: null\nconformance: implemented\n";
    let indented: String = expanded.lines().map(|l| format!("  {l}\n")).collect();
    // An expanded Access node where a field value stands: its value is validated in P2.
    assert!(refused(&format!("a:\n{indented}"), &s));
    // All of A plus another key is a recognition error; it replaces the validation errors.
    let t = format!("zz: 1\na:\n{indented}  extra: 1\n");
    let got = run(&t, &s);
    assert_eq!(got.len(), 1);
    assert_eq!(
        (got[0].0, got[0].1),
        ("EXPANDED_WITH_EXTRA_KEY", Phase::Recognize)
    );
    assert_eq!(got[0].2.as_deref(), Some("$.a"));
}

#[test]
fn an_opaque_field_takes_any_value_and_validation_leaves_the_input_alone() {
    let s = composite(vec![("o", shape(ShapeType::Opaque, Presence::Mandatory))]);
    for text in ["o: 1", "o: [1, {a: b}]", "o: {x: 1}", "o: 'x'", "o: true"] {
        assert!(codes(text, &s).is_empty(), "{text}");
    }
    let n = parse("o: {x: [1, 2]}\nzz: 1\n").unwrap();
    let before = n.clone();
    let _ = validate(Some(&n), &s, &a()).unwrap();
    assert!(n.same(&before));
    assert_eq!(n.pos, before.pos);
}

#[test]
fn a_mismatch_below_a_composite_is_refused_not_swallowed() {
    let inner = composite(vec![(
        "b",
        scalar(ScalarType::Integer, Presence::Mandatory),
    )]);
    let s = composite(vec![("a", inner)]);
    assert!(refused("a: {b: 'x'}", &s));
    assert!(codes("a: {b: 1}", &s).is_empty());
}

#[test]
fn with_a_repeated_key_the_first_entry_decides() {
    // Only a hand-built mapping can repeat a key; parse refuses it.
    let s = composite(vec![("a", scalar(ScalarType::String, Presence::Mandatory))]);
    let hand = Node::map(vec![
        ("a", Node::new(Value::Str("x".into()))),
        ("a", Node::new(Value::Null)),
    ]);
    assert!(validate(Some(&hand), &s, &a())
        .unwrap()
        .violations
        .is_empty());
}

#[test]
fn a_quoted_empty_string_has_text_and_a_position_unlike_an_empty_scalar() {
    let n = parse("a: ''\nb:\n").unwrap();
    let Value::Map(entries) = &n.value else {
        panic!()
    };
    assert_eq!(
        entries[0].value.pos,
        Some(Pos {
            line: 1,
            column: 4,
            offset: 3
        })
    );
    assert_eq!(entries[1].value.pos, None);
}

#[test]
fn the_refusal_names_what_was_found_and_what_was_declared() {
    let detail = |text: &str, s: &Shape| match validate(Some(&parse(text).unwrap()), s, &a()) {
        Err(Error::Unsupported(d)) => d,
        other => panic!("{other:?}"),
    };
    let int = scalar(ScalarType::Integer, Presence::Mandatory);
    assert!(detail("'x'", &int).contains("string"));
    assert!(detail("'x'", &int).contains("Integer"));
    assert!(detail("[1]", &int).contains("list"));
    assert!(detail("true", &int).contains("boolean"));
    let list = shape(ShapeType::List { min_items: None }, Presence::Optional);
    assert!(detail("5", &list).contains("integer"));
    assert!(detail("{a: 1}", &list).contains("mapping"));
    assert!(detail("5", &composite(vec![])).contains("integer"));
    assert!(detail("5", &composite(vec![])).contains("mapping is declared"));
}
