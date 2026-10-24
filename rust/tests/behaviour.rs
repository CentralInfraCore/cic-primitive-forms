//! Behaviour of the slice beyond the corpus: limits, positions, ordering, refusals and
//! the guarantee that validation changes nothing.

use cic_primitive_forms::{
    emit, parse, parse_with, recognize, Class, Entry, Error, Limits, Location, Node, Phase, Pos,
    Position, Value,
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
fn the_default_limits_are_the_published_ones() {
    assert_eq!(
        Limits::default(),
        Limits {
            max_bytes: 1 << 20,
            max_depth: 64,
            max_nodes: 100_000
        }
    );
}

#[test]
fn limit_boundaries_are_inclusive() {
    let text = "a: 12345"; // 8 bytes
    let at = |max_bytes| Limits {
        max_bytes,
        max_depth: 64,
        max_nodes: 100,
    };
    assert!(
        parse_with(text, at(8)).is_ok(),
        "exactly max_bytes is allowed"
    );
    assert!(
        matches!(parse_with(text, at(7)), Err(Error::Limit(_))),
        "one byte over"
    );
    let two = Limits {
        max_bytes: 1 << 20,
        max_depth: 2,
        max_nodes: 100,
    };
    assert!(parse_with("a: {b: 1}", two).is_ok(), "depth 2");
}

/// Past a limit no tree is kept, but the scan goes on: a syntax error further on still wins, as in Go, which reads the whole text
/// first (#85). The Go peer is `TestASyntaxErrorAfterALimitStillWins` in go/behaviour_test.go.
#[test]
fn a_syntax_error_after_a_limit_still_wins() {
    let few = Limits {
        max_nodes: 3,
        ..Limits::default()
    };
    let shallow = Limits {
        max_depth: 2,
        ..Limits::default()
    };
    assert!(matches!(
        parse_with("a: [1, 2, 3]\nb: 1\n", few),
        Err(Error::Limit(_))
    ));
    assert!(matches!(
        parse_with("a: [1, 2, 3]\nb: [\n", few),
        Err(Error::Syntax(_))
    ));
    assert!(matches!(
        parse_with("a: [1, 2, 3]\n: b\n", few),
        Err(Error::Syntax(_))
    ));
    assert!(matches!(
        parse_with("a: {b: {c: 1}}\nd: 1\n", shallow),
        Err(Error::Limit(_))
    ));
    assert!(matches!(
        parse_with("a: {b: {c: 1}}\nd: [\n", shallow),
        Err(Error::Syntax(_))
    ));
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

    // A lone CR is a line break.
    let n = parse("a: 1\rb: [ z]\r").unwrap();
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

/// With a repeated key (only a hand-built node can have one) the entries are a multiset, and
/// `same` is symmetric (#90). The Go peer is in `TestSameComparesEveryPartOfTheValue`.
#[test]
fn same_with_a_repeated_key_is_symmetric() {
    let dup = |vals: &[i64]| {
        Node::new(Value::Map(
            vals.iter()
                .map(|v| Entry {
                    key: "a".into(),
                    key_pos: None,
                    value: Node::new(Value::Int(*v)),
                })
                .collect(),
        ))
    };
    for (a, b, same) in [
        (&[1, 1][..], &[1, 2][..], false),
        (&[1, 2], &[2, 1], true),
        (&[1, 1], &[1, 1], true),
        (&[1, 2, 2], &[2, 1, 1], false),
    ] {
        assert_eq!(dup(a).same(&dup(b)), same, "{a:?} vs {b:?}");
        assert_eq!(dup(b).same(&dup(a)), same, "{b:?} vs {a:?}");
    }
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

fn paths(text: &str, position: &Position) -> Vec<(&'static str, Option<String>, Location)> {
    let out = recognize(&parse(text).unwrap(), position, &a()).unwrap();
    out.violations
        .into_iter()
        .map(|v| (v.code, v.logical_path, v.location))
        .collect()
}

#[test]
fn a_known_field_does_not_hide_an_unknown_one_and_the_reverse() {
    let structured = Position::Structured(vec!["host".into(), "port".into()]);
    let got = paths("host: a\ncolour: red\nport: 1\nshade: x\n", &structured);
    assert_eq!(
        got.iter().map(|g| g.1.clone().unwrap()).collect::<Vec<_>>(),
        ["$.colour", "$.shade"]
    );
    let list = Position::ListOf(vec![("host".into(), Position::Scalar)]);
    let got = paths("- host: a\n  colour: red\n", &list);
    assert_eq!(
        got.iter().map(|g| g.0).collect::<Vec<_>>(),
        ["UNKNOWN_FIELD"]
    );
    // An unknown field does not stop the fields after it from being checked.
    let got = paths("- colour: x\n  host: {a: 1}\n", &list);
    assert_eq!(
        got.iter().map(|g| g.0).collect::<Vec<_>>(),
        ["UNKNOWN_FIELD", "MAP_AT_SCALAR_POSITION"]
    );
}

#[test]
fn every_unknown_field_is_reported_with_its_canonical_path_in_a_fixed_order() {
    let structured = Position::Structured(vec!["host".into(), "port".into()]);
    // Source order is value, access; the response order is by path: $.access, $.value.
    let got = paths("value: 1\naccess: inherit\n", &structured);
    assert_eq!(
        got,
        vec![
            (
                "UNKNOWN_FIELD",
                Some("$.access".to_string()),
                Location::At(Pos {
                    line: 2,
                    column: 1,
                    offset: 9
                })
            ),
            (
                "UNKNOWN_FIELD",
                Some("$.value".to_string()),
                Location::At(Pos {
                    line: 1,
                    column: 1,
                    offset: 0
                })
            ),
        ]
    );
    // From a parsed value (no text) the same two errors have no coordinates.
    let node = Node::map(vec![
        ("value", Node::new(Value::Int(1))),
        ("access", Node::new(Value::Str("inherit".into()))),
    ]);
    let out = recognize(&node, &structured, &a()).unwrap();
    let got: Vec<_> = out
        .violations
        .iter()
        .map(|v| (v.logical_path.clone().unwrap(), v.location))
        .collect();
    assert_eq!(
        got,
        vec![
            ("$.access".to_string(), Location::Unavailable),
            ("$.value".to_string(), Location::Unavailable)
        ]
    );
}

#[test]
fn logical_paths_are_canonical_adr_0006_text_or_absent() {
    let structured = Position::Structured(vec!["host".into()]);
    let path_of = |text: &str| paths(text, &structured)[0].1.clone();
    assert_eq!(path_of("a.b: 1"), Some("$.a\\.b".to_string()));
    assert_eq!(path_of("x[1]: 1"), Some("$.x\\[1\\]".to_string()));
    assert_eq!(
        path_of("'a=b,c{d}\\e': 1"),
        Some("$.a\\=b\\,c\\{d\\}\\\\e".to_string())
    );
    assert_eq!(path_of("'a b': 1"), Some("$.a b".to_string()));
    // C1 controls and non-ASCII are literal name characters; C0 controls and an empty name
    // cannot be written, so the path is left out and not guessed.
    assert_eq!(path_of("\"\\u0085\": 1"), Some("$.\u{85}".to_string()));
    assert_eq!(path_of("\"a\\u0001b\": 1"), None);
    assert_eq!(path_of("\"a\\u007Fb\": 1"), None);
    assert_eq!(path_of("'': 1"), None);
    // No structure around a single position: no path.
    assert_eq!(paths("{a: 1}", &Position::Scalar)[0].1, None);
    // A list element has no declared identity in the synthetic schema: no path, and the
    // errors that cannot be told apart collapse to one per code.
    let list = Position::ListOf(vec![("host".into(), Position::Scalar)]);
    let got = paths(
        "- host: {a: 1}\n- host: {b: 2}\n- colour: x\n- shade: y\n",
        &list,
    );
    assert_eq!(
        got.iter().map(|g| (g.0, g.1.clone())).collect::<Vec<_>>(),
        vec![("MAP_AT_SCALAR_POSITION", None), ("UNKNOWN_FIELD", None)]
    );
}

fn many_keys(n: usize) -> String {
    (0..n).map(|i| format!("k{i}: 1\n")).collect()
}

#[test]
fn a_late_duplicate_is_still_found_and_many_keys_do_not_cost_quadratic_time() {
    let limits = Limits {
        max_bytes: 16 << 20,
        max_depth: 64,
        max_nodes: 1_000_000,
    };
    let n = 100_000;

    let start = std::time::Instant::now();
    let ok = parse_with(&many_keys(n), limits).expect("distinct keys parse");
    let Value::Map(entries) = &ok.value else {
        panic!()
    };
    assert_eq!(entries.len(), n);
    // Insertion order is kept.
    assert_eq!(entries[0].key, "k0");
    assert_eq!(entries[n - 1].key, format!("k{}", n - 1));

    // The duplicate of the first key, at the very end, is reported at its own position.
    let text = many_keys(n) + "k0: 2\n";
    match parse_with(&text, limits) {
        Err(Error::Contract(v)) => {
            assert_eq!(v.len(), 1);
            assert_eq!(v[0].code, "DUPLICATE_KEY");
            let Location::At(p) = v[0].location else {
                panic!()
            };
            assert_eq!((p.line, p.column), (n + 1, 1));
            assert_eq!(p.offset, many_keys(n).len());
        }
        other => panic!("{other:?}"),
    }
    // A comparison of every key with every earlier key takes minutes at this size; the bound
    // is generous so a slow machine does not fail it, a quadratic implementation does.
    assert!(
        start.elapsed() < std::time::Duration::from_secs(5),
        "took {:?}",
        start.elapsed()
    );
}

/// `same` reads the other mapping once (#85): a lookup per key in order takes minutes on a
/// large mapping in reverse order, where every key is at the far end; the bound is generous so
/// a slow machine does not fail it, a quadratic implementation does. The Go peer is
/// `TestSameOfManyKeysDoesNotCostQuadraticTime` in go/behaviour_test.go.
#[test]
fn same_of_many_keys_does_not_cost_quadratic_time() {
    let limits = Limits {
        max_bytes: 16 << 20,
        max_depth: 64,
        max_nodes: 1_000_000,
    };
    let n = 100_000;
    let a = parse_with(&many_keys(n), limits).expect("distinct keys parse");
    let mut b = parse_with(&many_keys(n), limits).expect("distinct keys parse");
    let Value::Map(entries) = &mut b.value else {
        panic!()
    };
    entries.reverse();
    let start = std::time::Instant::now();
    assert!(
        a.same(&b) && b.same(&a),
        "the same keys and values in another order are the same value"
    );
    let Value::Map(entries) = &mut b.value else {
        panic!()
    };
    entries[0].value = Node::new(Value::Int(2));
    assert!(!a.same(&b) && !b.same(&a), "a changed value was not seen");
    assert!(
        start.elapsed() < std::time::Duration::from_secs(5),
        "took {:?}",
        start.elapsed()
    );
}

/// Both parsers rank their errors alike: a syntax error anywhere in the text wins over an
/// earlier duplicate key. The Go peer reads the whole text before it walks the tree; this one
/// keeps the first non-syntax error and goes on scanning (the Go peer's test of the same name
/// asserts the same answer, and `tools/parity` compares the two on generated inputs).
#[test]
fn a_syntax_error_after_a_duplicate_key_wins_in_both_languages() {
    assert!(matches!(parse("a: 1\na: 2\nb: [\n"), Err(Error::Syntax(_))));
    // The same duplicate with no syntax error after it is the same answer in both languages.
    match parse("a: 1\na: 2\nb: []\n") {
        Err(Error::Contract(v)) => assert_eq!(v[0].code, "DUPLICATE_KEY"),
        other => panic!("{other:?}"),
    }
}

fn class_of(r: Result<Node, Error>) -> &'static str {
    match r {
        Ok(_) => "ok",
        Err(Error::Syntax(_)) => "syntax",
        Err(Error::Unsupported(_)) => "unsupported",
        Err(Error::Contract(_)) => "contract",
        Err(Error::Limit(_)) => "limit",
        Err(_) => "other",
    }
}

fn first_string(text: &str) -> String {
    let n = parse(text).unwrap_or_else(|e| panic!("{text:?}: {e:?}"));
    let Value::Map(entries) = &n.value else {
        panic!("{text:?}: not a mapping")
    };
    match &entries[0].value.value {
        Value::Str(s) => s.clone(),
        other => panic!("{text:?}: {other:?}"),
    }
}

/// `---`, `...` and `%` inside a block or a quoted scalar are text, not a document marker or a
/// directive. The expected values are the YAML 1.2 ones, not "what the other language gives".
#[test]
fn marker_like_text_inside_scalars_is_content() {
    for (text, want) in [
        ("a: |\n  ---\n  ...\n", "---\n...\n"),
        ("a: >\n  x\n  ---\n  y\n", "x --- y\n"),
        ("a: |+\n  ...\n\n", "...\n\n"),
        ("a: |\n  %YAML 1.2\n", "%YAML 1.2\n"),
        ("a: \"x\n  ---\n  ...\"\n", "x --- ..."),
        ("a: 'x\n  ---\n  ...'\n", "x --- ..."),
        ("a: \"x\n  --- y\"\n", "x --- y"),
        ("a: 'x\n  ... y'\n", "x ... y"),
        ("a: \"---\"\n", "---"),
        ("a: '...'\n", "..."),
    ] {
        assert_eq!(first_string(text), want, "{text:?}");
    }
    // The leading marker of a document is allowed, after comments and blank lines too.
    for text in ["---\na: 1\n", "# c\n\n---\na: 1\n", "--- \na: 1\n"] {
        assert_eq!(class_of(parse(text)), "ok", "{text:?}");
    }
}

/// A marker or a directive anywhere else is refused: `...`, a second `---`, an indented one, one
/// at the start of a line inside a quoted scalar, one that ends a block scalar.
#[test]
fn markers_and_directives_outside_content_are_refused() {
    for text in [
        "a: x\n  ---\n",
        "a: |\n  x\n---\n",
        "a: |\n  x\n...\n",
        "a: \"x\n---\n  y\"\n",
        "a: 'x\n...\n  y'\n",
        "a: \"x\n--- y\"\n",
        "a: 1\n---\nb: 2\n",
        "---\n---\n",
        "a: 1\n...\n",
        "  ---\n",
        "%YAML 1.2\n---\na: 1\n",
        "a: 1\n%TAG ! x\n",
    ] {
        assert_eq!(class_of(parse(text)), "unsupported", "{text:?}");
    }
}

// The order in which the problems of a text are reported, whatever else the text holds:
// 1. a size over the limit; 2. U+0085, U+2028, U+2029; 3. the text scan, which comes before the YAML
// reader: control characters, tabs, a BOM inside the text, then in text order directives, document
// markers, tags, anchors, aliases, explicit keys, a comment against `]`/`}`: one of these anywhere
// hides a YAML syntax error anywhere; 4. a YAML syntax error anywhere, which beats everything below;
// 5. a second document; 6. the first in document order of an unsupported construct, a duplicate key
// and a node or depth limit.
#[test]
fn the_order_in_which_problems_are_reported_is_fixed() {
    let tiny = Limits {
        max_bytes: 4,
        ..Limits::default()
    };
    assert_eq!(class_of(parse_with("a: [\n", tiny)), "limit");
    assert_eq!(class_of(parse_with("a: [\n\u{85}", tiny)), "limit");
    // 2.
    assert_eq!(class_of(parse("a: [\u{85}\n")), "unsupported");
    // 3, with the syntax error after and before.
    for text in [
        "a: 1\ta\nb: [\n",
        "b: [\na: \t1\n",
        "a: \u{1}\nb: [\n",
        "b: [\na: \u{1}\n",
        "a: !x\nb: [\n",
        "b: [\na: !x\n",
        "a: *x\nb: [\n",
        "a: [? c]\nb: [\n",
        "%YAML 1.2\nb: [\n",
        "b: [\n%YAML 1.2\n",
        "a: 1\n---\nb: [\n",
        "b: [\n---\n",
        "b: [\n...\n",
    ] {
        assert_eq!(class_of(parse(text)), "unsupported", "{text:?}");
    }
    // 4: a syntax error beats the tree-level problems, wherever they are.
    for text in [
        "a: 1.5\nb: [\n",
        "b: [\na: 1.5\n",
        "a: 0o7\nb: ]\n",
        "a: 1\na: 2\nb: [\n",
        "b: [\na: 1\na: 2\n",
        "<<: 1\nb: [\n",
        "[a]: 1\nb: [\n",
    ] {
        assert_eq!(class_of(parse(text)), "syntax", "{text:?}");
    }
    let one = Limits {
        max_nodes: 1,
        ..Limits::default()
    };
    assert_eq!(class_of(parse_with("a: 1\nb: [\n", one)), "syntax");
    assert_eq!(class_of(parse_with("a: 1\nb: 2\n", one)), "limit");
    // 6: the first in document order.
    assert_eq!(class_of(parse("a: 1.5\na: 2\n")), "unsupported");
    assert_eq!(class_of(parse("a: 1\na: 2\nb: 1.5\n")), "contract");
    assert_eq!(class_of(parse("a: 1\na: 2\n")), "contract");
}

/// Found by the fuzz job: a key past the implicit-key limit is emitted as `? key`, and a plain key
/// that starts with `-`, `?` or `:` (not followed by a space) must read back.
#[test]
fn a_long_key_that_starts_with_an_indicator_reads_back() {
    for first in ["-", "-x", ":", ":x", "?x", "k", "\u{a0}"] {
        let key = format!("{first}{}", "k".repeat(600));
        let n = Node::new(Value::Map(vec![cic_primitive_forms::Entry {
            key,
            key_pos: None,
            value: Node::new(Value::Int(1)),
        }]));
        let text = emit(&n);
        let back = parse(&text).unwrap_or_else(|e| panic!("{first:?}: {e:?}"));
        assert!(back.same(&n), "{first:?}");
    }
}

/// Boundaries of the marker, explicit-key and block-end checks, with the YAML 1.2 answer for each.
#[test]
fn the_edges_of_the_text_scan_give_the_yaml_answer() {
    let reason = |t: &str| match parse(t) {
        Err(Error::Unsupported(m)) => m,
        other => panic!("{t:?}: {other:?}"),
    };
    // A marker is `---` or `...` followed by a blank or the end of the text, nothing else.
    assert_eq!(reason("a: 1\n---"), "document marker");
    assert_eq!(reason("a: 1\n..."), "document marker");
    assert_eq!(reason(" ---\n"), "document marker");
    for text in ["---x", "x\n...y"] {
        assert_eq!(class_of(parse(text)), "ok", "{text:?}");
    }
    // The emitter's explicit key, at the end of the text and with a first character that is an indicator.
    for text in ["? x", "? -x", "? :x"] {
        assert_eq!(class_of(parse(text)), "ok", "{text:?}");
    }
    for text in ["? -", "? :", "? - x", "? : x", "? ?x"] {
        assert_eq!(class_of(parse(text)), "unsupported", "{text:?}");
    }
    // After a block scalar ends, the lines that follow are scanned again.
    assert_eq!(
        reason("k:\n  a: |\n    x\n  b: 1\n  c: [? q]\n"),
        "explicit mapping key"
    );
    // ... also when they are indented deeper than the block scalar was.
    assert_eq!(
        reason("k:\n  a: |\n    x\n  b:\n    - [? q]\n"),
        "explicit mapping key"
    );
    // Text in a comment is text.
    for text in [
        "a: 1 # [? c]\n",
        "# [? c]\na: 1\n",
        "a: |\n  x\n  # [? c]\nb: 1\n",
    ] {
        assert_eq!(class_of(parse(text)), "ok", "{text:?}");
    }
}

/// Found by the fuzz job: `a? *` is one plain scalar (a `?` after text is no indicator), and the emitter writes
/// it as a key unquoted, so the text scan must not take its `*` for an alias.
#[test]
fn an_indicator_inside_a_plain_scalar_is_text_and_at_a_scalar_start_is_not() {
    for ok in [
        "a? *: 1\n",
        "a- &: 1\n",
        ":- *: 1\n",
        "-? &: 1\n",
        "a: b- *c\n",
    ] {
        assert_eq!(class_of(parse(ok)), "ok", "{ok:?}");
    }
    for refused in ["- - *a\n", "- &a b\n", "a: - *b\n"] {
        assert_eq!(class_of(parse(refused)), "unsupported", "{refused:?}");
    }
}

/// Every string of up to three characters from the alphabet of YAML indicators, as a value, a sequence item, a
/// key and in a nested sequence, reads back from what `emit` wrote.
#[test]
fn every_short_string_of_indicators_round_trips() {
    let alphabet: Vec<char> = "a*&!?:-#[]{},\"'\\ |>%@`".chars().collect();
    let mut strings = vec![String::new()];
    let mut frontier = vec![String::new()];
    for _ in 0..3 {
        let mut next = Vec::new();
        for s in &frontier {
            for c in &alphabet {
                next.push(format!("{s}{c}"));
            }
        }
        strings.extend(next.iter().cloned());
        frontier = next;
    }
    let entry = |key: String, value: Node| {
        Node::new(Value::Map(vec![cic_primitive_forms::Entry {
            key,
            key_pos: None,
            value,
        }]))
    };
    for s in &strings {
        let v = Node::new(Value::Str(s.clone()));
        for n in [
            entry("k".into(), v.clone()),
            Node::new(Value::Seq(vec![v.clone()])),
            entry(s.clone(), Node::new(Value::Int(1))),
            entry(
                "k".into(),
                Node::new(Value::Seq(vec![v.clone(), v.clone()])),
            ),
        ] {
            let text = emit(&n);
            let back = parse(&text).unwrap_or_else(|e| panic!("{text:?}: {e:?}"));
            assert!(back.same(&n), "{text:?}");
        }
    }
}
