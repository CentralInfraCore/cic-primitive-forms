//! `edit_scalar` (P3b, step E3) at the places a vector of corpus/edit cannot reach (its trees have no nullable): null, the styles at their edges, the
//! plain-safe rule, a document that does not validate, the parsed value without text. The Go peer is go/editscalar_test.go.

use cic_primitive_forms::{
    edit_parsed, edit_scalar, Case, Elements, Error, Location, Node, Phase, Presence, ScalarType,
    Shape, ShapeType, Value,
};

const BOM: &str = "\u{feff}";

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

fn shape(shape_type: ShapeType, nullable: bool) -> Shape {
    Shape {
        shape_type,
        presence: Presence::Optional,
        nullable,
        has_default: false,
        default: None,
    }
}

/// `n` (a nullable string), `count` (an integer) and `ok` (a boolean).
fn root() -> Shape {
    shape(
        ShapeType::Composite(vec![
            (
                "n".into(),
                shape(ShapeType::Scalar(ScalarType::String), true),
            ),
            (
                "count".into(),
                shape(ShapeType::Scalar(ScalarType::Integer), false),
            ),
            (
                "ok".into(),
                shape(ShapeType::Scalar(ScalarType::Boolean), false),
            ),
        ]),
        false,
    )
}

fn st(s: &str) -> Node {
    Node::new(Value::Str(s.to_string()))
}

fn int(i: i64) -> Node {
    Node::new(Value::Int(i))
}

fn null() -> Node {
    Node::new(Value::Null)
}

fn edit(text: &str, path: &str, value: &Node) -> Result<String, Error> {
    edit_scalar(text, &root(), path, &a(), value).map(|r| r.text)
}

/// The one violation of a refusal: code, phase, whether the location is unavailable.
fn refusal(r: Result<String, Error>) -> (&'static str, Phase, bool) {
    match r {
        Err(Error::Contract(vs)) if vs.len() == 1 => (
            vs[0].code,
            vs[0].phase,
            vs[0].location == Location::Unavailable,
        ),
        other => panic!("one violation of the contract was expected, got {other:?}"),
    }
}

#[test]
fn edit_scalar_writes_and_reads_null() {
    let cases: Vec<(&str, &str, Node, &str)> = vec![
        ("n: abc\n", "$.n", null(), "n: null\n"),
        ("n: ~\n", "$.n", st("x"), "n: x\n"),
        ("n: ~\n", "$.n", null(), "n: ~\n"),
        ("n: null\n", "$.n", null(), "n: null\n"),
        ("n: Null\n", "$.n", st("y"), "n: y\n"),
        (
            "count: 3\n",
            "$.count",
            int(i64::MIN),
            "count: -9223372036854775808\n",
        ),
        (
            "count: 3\n",
            "$.count",
            int(i64::MAX),
            "count: 9223372036854775807\n",
        ),
        ("count: +5\n", "$.count", int(5), "count: +5\n"),
        ("count: +5\n", "$.count", int(6), "count: 6\n"),
    ];
    for (text, path, value, want) in cases {
        assert_eq!(
            edit(text, path, &value).as_deref().ok(),
            Some(want),
            "{text:?} {path}"
        );
    }
}

#[test]
fn edit_scalar_plain_safe_and_the_styles() {
    for v in [
        "a:b", "a#b", "yes", "x y z", "é", "a-b", "a'b", "a\"b", "Null1", "1a",
    ] {
        assert_eq!(
            edit("n: old\n", "$.n", &st(v)).ok(),
            Some(format!("n: {v}\n")),
            "plain {v:?}"
        );
    }
    for v in [
        "a:", "a ", "!x", "%x", "@x", "`x", "?x", "[x", "{x", "*x", "&x", "|x", ">x", "'x", "\"x",
        ",x", "#x", "null", "~", "False", "1.5", "0x1F",
    ] {
        assert_eq!(
            refusal(edit("n: old\n", "$.n", &st(v))).0,
            "EDIT_STYLE_CONFLICT",
            "plain {v:?}"
        );
    }
    let quoted = [
        ("n: 'old'\n", "n: 'null'\n", "null"),
        ("n: 'old'\n", "n: ''\n", ""),
        ("n: \"old\"\n", "n: \"\"\n", ""),
        ("n: \"old\"\n", "n: \"a: b\"\n", "a: b"),
        ("n: 'old'\n", "n: '#x'\n", "#x"),
        ("n: \"old\" # c\n", "n: \"é\" # c\n", "é"),
    ];
    for (text, want, v) in quoted {
        assert_eq!(
            edit(text, "$.n", &st(v)).ok().as_deref(),
            Some(want),
            "{text:?} <- {v:?}"
        );
    }
    // an existing token with an escaped control character is read and can be replaced; a new value never holds one
    assert_eq!(
        edit("n: \"a\\tb\"\n", "$.n", &st("c")).ok().as_deref(),
        Some("n: \"c\"\n")
    );
    // a quoted string cannot carry null; the style is never changed
    assert_eq!(
        refusal(edit("n: \"old\"\n", "$.n", &null())).0,
        "EDIT_STYLE_CONFLICT"
    );
}

#[test]
fn edit_scalar_the_kinds_of_the_value() {
    let text = "n: a\ncount: 3\nok: true\n";
    let seq = Node::new(Value::Seq(vec![]));
    let map = Node::new(Value::Map(vec![]));
    for (path, value) in [
        ("$.count", Node::new(Value::Bool(true))),
        ("$.ok", int(1)),
        ("$.count", null()),
        ("$.n", int(5)),
        ("$.n", seq),
        ("$.n", map),
    ] {
        assert_eq!(
            refusal(edit(text, path, &value)).0,
            "EDIT_TYPE_MISMATCH",
            "{path} <- {value:?}"
        );
    }
    for c in [0x2028_u32, 0x2029, 0x85, 0x7f, 0x00, 0x09, 0x0d] {
        let v = format!("a{}b", char::from_u32(c).expect("a char"));
        assert_eq!(
            refusal(edit("n: a\n", "$.n", &st(&v))).0,
            "EDIT_VALUE_NOT_REPRESENTABLE",
            "{c:#x}"
        );
    }
}

#[test]
fn edit_scalar_refuses_what_earlier_phases_refuse() {
    // a document that does not validate: the violations of the validate phase, as they are
    let (code, phase, _) = refusal(edit("bogus: 1\n", "$.n", &st("x")));
    assert_eq!((code, phase), ("UNKNOWN_FIELD", Phase::Validate));
    assert_eq!(
        refusal(edit("n: a\n", "$.n.", &st("x"))).1,
        Phase::PathParse
    );
    assert_eq!(
        refusal(edit("n: a\n", "$.zzz", &st("x"))).1,
        Phase::PathResolve
    );
    // the parsed value: the document and the path are accepted first, then there is no text
    let doc = Node::map(vec![("n", st("a"))]);
    let r = edit_parsed(&doc, &root(), "$.n", &a(), &st("x"));
    assert_eq!(refusal(Err(r)), ("EDIT_NO_SOURCE_TEXT", Phase::Edit, true));
    let bad = Node::map(vec![("bogus", st("a"))]);
    assert_eq!(
        refusal(Err(edit_parsed(&bad, &root(), "$.n", &a(), &st("x")))).1,
        Phase::Validate
    );
    assert_eq!(
        refusal(Err(edit_parsed(&doc, &root(), "$.zzz", &a(), &st("x")))).1,
        Phase::PathResolve
    );
}

#[test]
fn edit_scalar_keeps_everything_else() {
    let text = format!("{BOM}# top\nn:   abc   # keep this\r\n\r\ncount: 3\n# end");
    let got = edit_scalar(&text, &root(), "$.count", &a(), &int(10)).expect("an edit");
    assert_eq!(
        got.text,
        format!("{BOM}# top\nn:   abc   # keep this\r\n\r\ncount: 10\n# end")
    );
    let start = format!("{BOM}# top\nn:   abc   # keep this\r\n\r\ncount: ").len();
    assert_eq!(
        (got.span.start, got.span.end, got.changed),
        (start, start + 2, true)
    );
}

/// `routes`, a list whose elements are identified by `to` and hold a scalar `metric` and a composite `net` with a scalar `ip`.
fn list_root() -> Shape {
    let string = || shape(ShapeType::Scalar(ScalarType::String), false);
    shape(
        ShapeType::Composite(vec![(
            "routes".into(),
            shape(
                ShapeType::DeclaredList {
                    min_items: None,
                    elements: Elements {
                        choice: false,
                        cases: vec![Case {
                            name: "route".into(),
                            fields: vec![
                                ("to".into(), string()),
                                (
                                    "metric".into(),
                                    shape(ShapeType::Scalar(ScalarType::Integer), false),
                                ),
                                (
                                    "net".into(),
                                    shape(
                                        ShapeType::Composite(vec![("ip".into(), string())]),
                                        false,
                                    ),
                                ),
                            ],
                            key: vec!["to".into()],
                        }],
                    },
                },
                false,
            ),
        )]),
        false,
    )
}

#[test]
fn edit_scalar_reaches_a_scalar_below_a_composite_in_a_list_element() {
    let text = "routes:\n  - to: a\n    net:\n      ip: 1.1.1.1\n  - to: b\n    metric: 2\n    net:\n      ip: 2.2.2.2\n";
    let path = "$.routes[{to=\"b\"}].net.ip";
    let got = edit_scalar(text, &list_root(), path, &a(), &st("9.9.9.9")).map(|r| r.text);
    assert_eq!(
        got.ok().as_deref(),
        Some("routes:\n  - to: a\n    net:\n      ip: 1.1.1.1\n  - to: b\n    metric: 2\n    net:\n      ip: 9.9.9.9\n")
    );
    // a kind the position does not have is found through the same walk
    let r = edit_scalar(text, &list_root(), path, &a(), &int(5)).map(|r| r.text);
    assert_eq!(refusal(r).0, "EDIT_TYPE_MISMATCH");
}

#[test]
fn edit_scalar_through_an_expanded_wrapper_of_an_integer() {
    // the validator reads the wrapper as its `value`: an integer position holds the integer, not the first member that is not `value`
    let wrapper = "count:\n  access: inherit\n  value: 3\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n";
    let want = "count:\n  access: inherit\n  value: 4\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n";
    assert_eq!(
        edit(wrapper, "$.count", &int(4)).ok().as_deref(),
        Some(want)
    );
}

#[test]
fn edit_parsed_refuses_a_bad_path_before_it_refuses_the_missing_text() {
    let doc = Node::map(vec![("n", st("a"))]);
    let r = edit_parsed(&doc, &root(), "$.n.", &a(), &st("x"));
    assert_eq!(refusal(Err(r)).1, Phase::PathParse);
}

#[test]
fn edit_scalar_through_an_expanded_wrapper_in_a_list_element() {
    let wrapper = "      access: inherit\n      value: 1\n      modify: inherit\n      inherit: true\n      default_injection: ~\n      conformance: implemented\n";
    let text = format!("routes:\n  - to: a\n    metric:\n{wrapper}");
    let got = edit_scalar(
        &text,
        &list_root(),
        "$.routes[{to=\"a\"}].metric",
        &a(),
        &int(7),
    )
    .map(|r| r.text);
    let want = format!(
        "routes:\n  - to: a\n    metric:\n{}",
        wrapper.replace("value: 1", "value: 7")
    );
    assert_eq!(got.ok(), Some(want));
}
