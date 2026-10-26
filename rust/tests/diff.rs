//! `diff` (ADR 0008) at the places a vector of corpus/diff cannot reach: its trees have no nullable composite, no integer or boolean set, no
//! integer key, and the operands of a case are never invalid in a way the vectors do not name. The Go peer is go/diff_test.go.

use cic_primitive_forms::{
    bind_grammar, diff, parse, Case, Context, DiffContext, DiffView, DocOrigin, Elements, Error,
    Grammar, Node, Phase, Presence, ScalarType, Shape, ShapeType, Value, Violation,
    ORIGIN_ATTESTATION_MISMATCH,
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

fn grammar() -> Grammar {
    let one = Value::Int(1);
    bind_grammar(Some(&one), Some(&one)).expect("grammar 1")
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

fn scalar(t: ScalarType) -> Shape {
    shape(ShapeType::Scalar(t), false)
}

fn set(t: ScalarType, nullable: bool) -> Shape {
    shape(
        ShapeType::Set {
            item: t,
            min_items: None,
        },
        nullable,
    )
}

fn list(choice: bool, cases: Vec<Case>, nullable: bool) -> Shape {
    shape(
        ShapeType::DeclaredList {
            min_items: None,
            elements: Elements { choice, cases },
        },
        nullable,
    )
}

fn case(name: &str, key: &[&str], fields: Vec<(&str, Shape)>) -> Case {
    Case {
        name: name.into(),
        fields: fields
            .into_iter()
            .map(|(n, s)| (n.to_string(), s))
            .collect(),
        key: key.iter().map(|k| (*k).to_string()).collect(),
    }
}

/// Every shape a diff walks: scalars of the three types, a composite, sets of strings, integers and booleans, an opaque value, the three kinds
/// of list (by a string key, by an integer key, by an item_choice), and a nullable composite, list and set.
fn root() -> Shape {
    use ScalarType::{Boolean, Integer, String as Str};
    let fields = |v: Vec<(&str, Shape)>| -> Vec<(String, Shape)> {
        v.into_iter().map(|(n, s)| (n.to_string(), s)).collect()
    };
    let mut top = shape(
        ShapeType::Composite(fields(vec![
            ("mtu", scalar(Integer)),
            ("name", scalar(Str)),
            ("on", scalar(Boolean)),
            ("note", shape(ShapeType::Opaque, true)),
            (
                "ip",
                shape(
                    ShapeType::Composite(fields(vec![
                        ("gw", scalar(Str)),
                        ("addrs", set(Str, false)),
                    ])),
                    false,
                ),
            ),
            (
                "opt",
                shape(ShapeType::Composite(fields(vec![("x", scalar(Str))])), true),
            ),
            ("ids", set(Integer, false)),
            ("flags", set(Boolean, false)),
            ("oset", set(Str, true)),
            (
                "nics",
                list(
                    false,
                    vec![case(
                        "nic",
                        &["name", "zone"],
                        vec![
                            ("name", scalar(Str)),
                            ("zone", scalar(Str)),
                            ("subnet", scalar(Str)),
                        ],
                    )],
                    true,
                ),
            ),
            (
                "ports",
                list(
                    false,
                    vec![case(
                        "port",
                        &["port"],
                        vec![("port", scalar(Integer)), ("label", scalar(Str))],
                    )],
                    false,
                ),
            ),
            (
                "vlans",
                list(
                    true,
                    vec![
                        case("referenced", &["ref"], vec![("ref", scalar(Str))]),
                        case(
                            "inline",
                            &["vlan_id"],
                            vec![("vlan_id", scalar(Integer)), ("mode", scalar(Str))],
                        ),
                    ],
                    false,
                ),
            ),
            ("plain", shape(ShapeType::List { min_items: None }, false)),
        ])),
        false,
    );
    top.presence = Presence::Mandatory;
    top
}

fn doc(src: &str) -> Node {
    parse(src).unwrap_or_else(|e| panic!("{src:?}: {e:?}"))
}

fn view(doc: &Node) -> DiffView<'_> {
    DiffView {
        doc,
        context: DiffContext::default(),
        trust: Context::default(),
    }
}

fn run(
    x: &Node,
    y: &Node,
    scope: Option<&[&str]>,
) -> Result<cic_primitive_forms::DiffResult, Error> {
    let scope: Option<Vec<String>> = scope.map(|s| s.iter().map(|p| (*p).to_string()).collect());
    diff(
        &view(x),
        &view(y),
        &root(),
        &a(),
        grammar(),
        scope.as_deref(),
    )
}

fn canonical(x: &str, y: &str, scope: Option<&[&str]>) -> String {
    run(&doc(x), &doc(y), scope)
        .unwrap_or_else(|e| panic!("{x:?} against {y:?}: {e:?}"))
        .canonical()
}

/// The one violation of a diff that refuses.
fn refusal(x: &Node, y: &Node, scope: Option<&[&str]>) -> Violation {
    match run(x, y, scope) {
        Err(Error::Contract(vs)) => {
            assert_eq!(vs.len(), 1, "one error per response");
            vs[0].clone()
        }
        other => panic!("expected a contract error, got {other:?}"),
    }
}

const DFLT: &str = r#""access":"inherit","conformance":"implemented","default_injection":null,"inherit":true,"modify":"inherit""#;
const NONE: &str = r#"{"changes":[],"representation":[],"version":1}"#;

fn unsupported(s: &str) -> Node {
    Node::new(Value::Unsupported(s.into()))
}

fn int(i: i64) -> Node {
    Node::new(Value::Int(i))
}

fn string(s: &str) -> Node {
    Node::new(Value::Str(s.into()))
}

fn seq(items: Vec<Node>) -> Node {
    Node::new(Value::Seq(items))
}

fn wrapper(value: Node, over: Option<(&str, Node)>) -> Node {
    let mut members = vec![
        ("value", value),
        ("access", string("inherit")),
        ("modify", string("inherit")),
        ("inherit", Node::new(Value::Bool(true))),
        ("default_injection", Node::new(Value::Null)),
        ("conformance", string("implemented")),
    ];
    if let Some((k, v)) = over {
        for m in &mut members {
            if m.0 == k {
                m.1 = v.clone();
            }
        }
    }
    Node::map(members)
}

#[test]
fn an_unsupported_number_is_named_wherever_it_is() {
    let cases: Vec<(&str, Node, &str)> = vec![
        (
            "inside an opaque value",
            Node::map(vec![("note", seq(vec![unsupported("1.5")]))]),
            "$.note",
        ),
        (
            "as a value",
            Node::map(vec![("mtu", unsupported("9223372036854775808"))]),
            "$.mtu",
        ),
        (
            "in the value of an expanded position",
            Node::map(vec![("note", wrapper(unsupported("2.5"), None))]),
            "$.note",
        ),
        (
            "in a declaration member of an expanded position",
            Node::map(vec![(
                "mtu",
                wrapper(int(1), Some(("access", unsupported("1e3")))),
            )]),
            "$.mtu",
        ),
        (
            "in a set",
            Node::map(vec![(
                "ip",
                Node::map(vec![("addrs", seq(vec![unsupported("1.5")]))]),
            )]),
            "$.ip.addrs",
        ),
        (
            "below a composite",
            Node::map(vec![("ip", Node::map(vec![("gw", unsupported("1.5"))]))]),
            "$.ip.gw",
        ),
        (
            "in a field of a list element",
            Node::map(vec![(
                "nics",
                seq(vec![Node::map(vec![
                    ("name", string("n")),
                    ("zone", string("z")),
                    ("subnet", unsupported("1.5")),
                ])]),
            )]),
            r#"$.nics[{name="n",zone="z"}].subnet"#,
        ),
        (
            "in a list element that has no identity",
            Node::map(vec![(
                "nics",
                seq(vec![Node::map(vec![
                    ("name", string("n")),
                    ("subnet", unsupported("1.5")),
                ])]),
            )]),
            "$.nics",
        ),
        (
            "under a key the schema does not declare",
            Node::map(vec![("bogus", unsupported("1.5"))]),
            "",
        ),
        (
            "in the first of two fields, by name",
            Node::map(vec![
                ("name", unsupported("2.5")),
                ("mtu", unsupported("1.5")),
            ]),
            "$.mtu",
        ),
        (
            "after a clean field",
            Node::map(vec![("mtu", int(1)), ("name", unsupported("2.5"))]),
            "$.name",
        ),
    ];
    let clean = doc("mtu: 1\n");
    for (name, bad, path) in &cases {
        let v = refusal(bad, &clean, None);
        assert_eq!(
            (
                v.code,
                v.phase,
                v.location,
                v.logical_path.clone().unwrap_or_default(),
                v.operand
            ),
            (
                "DIFF_UNSUPPORTED_VALUE",
                Phase::Diff,
                cic_primitive_forms::Location::NotApplicable,
                (*path).to_string(),
                Some("a")
            ),
            "{name} (a)"
        );
        let v = refusal(&clean, bad, None);
        assert_eq!(v.code, "DIFF_UNSUPPORTED_VALUE", "{name} (b)");
        assert_eq!(v.logical_path.unwrap_or_default(), *path, "{name} (b)");
        assert_eq!(v.operand, Some("b"), "{name} (b)");
    }
    // both operands: A is named, and the refusal does not depend on what the other holds
    let v = refusal(
        &Node::map(vec![("mtu", unsupported("1.5"))]),
        &Node::map(vec![("name", unsupported("2.5"))]),
        None,
    );
    assert_eq!(
        (v.logical_path.as_deref(), v.operand),
        (Some("$.mtu"), Some("a"))
    );
}

#[test]
fn it_refuses_before_it_reads_and_in_the_order_of_the_contract() {
    let bad = doc("bogus: 1\n");
    let ok = doc("mtu: 1\n");
    let float = Node::map(vec![("mtu", unsupported("1.5"))]);
    // the contexts first, then the scope, then an unsupported number (A before B), then the views (A before B)
    let other = DiffContext {
        schema: "x".into(),
        ..DiffContext::default()
    };
    let mismatch = diff(
        &DiffView {
            doc: &bad,
            context: other,
            trust: Context::default(),
        },
        &view(&bad),
        &root(),
        &a(),
        grammar(),
        Some(&["$.nope".to_string()]),
    );
    assert_eq!(mismatch.unwrap_err().code(), "DIFF_CONTEXT_MISMATCH");
    let v = refusal(&bad, &bad, Some(&["$.nope"]));
    assert_eq!((v.code, v.phase), ("UNKNOWN_FIELD", Phase::PathResolve));
    let v = refusal(&bad, &float, None);
    assert_eq!((v.code, v.operand), ("DIFF_UNSUPPORTED_VALUE", Some("b")));
    let v = refusal(&bad, &bad, None);
    assert_eq!(
        (v.code, v.phase, v.operand, v.logical_path.as_deref()),
        ("UNKNOWN_FIELD", Phase::Validate, Some("a"), Some("$.bogus"))
    );
    let v = refusal(&ok, &bad, None);
    assert_eq!((v.code, v.operand), ("UNKNOWN_FIELD", Some("b")));
}

#[test]
fn the_contexts_are_compared() {
    let d = doc("mtu: 1\n");
    let base = DiffContext {
        schema: "s".into(),
        schema_digest: "sd".into(),
        grammar: "g".into(),
        grammar_digest: "gd".into(),
        surface: "x".into(),
    };
    let variants = [
        DiffContext {
            schema: "t".into(),
            ..base.clone()
        },
        DiffContext {
            schema_digest: "td".into(),
            ..base.clone()
        },
        DiffContext {
            grammar: "h".into(),
            ..base.clone()
        },
        DiffContext {
            grammar_digest: "hd".into(),
            ..base.clone()
        },
        DiffContext {
            surface: "y".into(),
            ..base.clone()
        },
    ];
    let with = |context: DiffContext| DiffView {
        doc: &d,
        context,
        trust: Context::default(),
    };
    for (i, o) in variants.iter().enumerate() {
        let err = diff(
            &with(base.clone()),
            &with(o.clone()),
            &root(),
            &a(),
            grammar(),
            None,
        )
        .expect_err("different contexts");
        let Error::Contract(vs) = err else {
            panic!("context {i}")
        };
        assert_eq!(
            (
                vs[0].code,
                vs[0].phase,
                vs[0].operand,
                vs[0].logical_path.clone()
            ),
            ("DIFF_CONTEXT_MISMATCH", Phase::Diff, None, None),
            "context {i}"
        );
    }
    assert!(diff(
        &with(base.clone()),
        &with(base),
        &root(),
        &a(),
        grammar(),
        None
    )
    .is_ok());
}

#[test]
fn the_views_are_read_as_their_own_phases() {
    let good = doc("mtu: 1\n");
    let v = refusal(
        &doc("mtu: {access: inherit, modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: 1, extra: 2}\n"),
        &good,
        None,
    );
    assert_eq!(
        (v.code, v.phase, v.operand),
        ("EXPANDED_WITH_EXTRA_KEY", Phase::Recognize, Some("a"))
    );
    let v = refusal(
        &good,
        &doc("nics:\n  - {name: a, zone: b}\n  - {name: a, zone: b}\n"),
        None,
    );
    assert_eq!(
        (v.code, v.phase, v.operand, v.logical_path.as_deref()),
        (
            "PATH_DUPLICATE_IDENTITY",
            Phase::PathOf,
            Some("b"),
            Some(r#"$.nics[{name="a",zone="b"}]"#)
        )
    );
    let v = refusal(&doc("nics:\n  - {name: a}\n"), &good, None);
    assert_eq!(
        (v.code, v.phase, v.operand),
        ("PATH_KEY_MISSING", Phase::PathOf, Some("a"))
    );
    // the trusted context is checked when a view is expanded: a materialized document without its attestation
    let materialized = Context {
        origin: DocOrigin::Materialized,
        ..Context::default()
    };
    let with = |trust: &Context| DiffView {
        doc: &good,
        context: DiffContext::default(),
        trust: trust.clone(),
    };
    for (x, y, operand) in [
        (with(&materialized), view(&good), "a"),
        (view(&good), with(&materialized), "b"),
    ] {
        let Err(Error::Contract(vs)) = diff(&x, &y, &root(), &a(), grammar(), None) else {
            panic!("trust of {operand}")
        };
        assert_eq!(
            (vs[0].code, vs[0].operand),
            (ORIGIN_ATTESTATION_MISMATCH, Some(operand))
        );
    }
    // a structure outside the subset is not a contract error
    assert!(matches!(
        run(&doc("ip: [x]\n"), &good, None),
        Err(Error::Unsupported(_))
    ));
}

#[test]
fn the_scope() {
    let x = "mtu: 1\nname: x\nip: {gw: g1}\nnics:\n  - {name: n1, zone: z, subnet: a}\n";
    let y = "mtu: 2\nname: y\nip: {gw: g2}\nnics:\n  - {name: n1, zone: z, subnet: b}\n  - {name: n2, zone: z}\n";
    assert_eq!(
        canonical(x, y, Some(&[])),
        NONE,
        "an empty scope compares nothing"
    );
    let whole = canonical(x, y, None);
    assert_eq!(
        canonical(x, y, Some(&["$"])),
        whole,
        "the root as the scope is the whole document"
    );
    // repeated and nested entries add nothing, whatever their order
    let ip = canonical(x, y, Some(&["$.ip"]));
    for scope in [
        vec!["$.ip", "$.ip.gw"],
        vec!["$.ip.gw", "$.ip"],
        vec!["$.ip", "$.ip"],
    ] {
        assert_eq!(canonical(x, y, Some(&scope)), ip, "{scope:?}");
    }
    assert_eq!(
        canonical(x, y, Some(&["$.ip.gw", "$.ip.gw"])),
        canonical(x, y, Some(&["$.ip.gw"]))
    );
    // a list element that only B has appears as one event at its path; one that only A has disappears
    let added = canonical(x, y, Some(&[r#"$.nics[{name="n2",zone="z"}]"#]));
    let want = format!(
        r#"{{"changes":[{{"kind":"added","next":{{"name":{{{DFLT},"value":"n2"}},"zone":{{{DFLT},"value":"z"}}}},"path":"$.nics[{{name=\"n2\",zone=\"z\"}}]"}}],"representation":[],"version":1}}"#
    );
    assert_eq!(added, want);
    let removed = canonical(y, x, Some(&[r#"$.nics[{name="n2",zone="z"}]"#]));
    assert!(removed.contains(r#""kind":"removed""#) && removed != NONE);
    assert_eq!(
        canonical(x, y, Some(&[r#"$.nics[{name="zz",zone="z"}]"#])),
        NONE
    );
    // the scope is a path of the schema
    let d = doc("mtu: 1\n");
    for (path, code, phase) in [
        ("$.", "PATH_EMPTY_NAME", Phase::PathParse),
        ("$.ip.gw.x", "PATH_NO_CHILDREN", Phase::PathResolve),
        ("$.addrs", "UNKNOWN_FIELD", Phase::PathResolve),
    ] {
        let v = refusal(&d, &d, Some(&[path]));
        assert_eq!((v.code, v.phase, v.operand), (code, phase, None), "{path}");
    }
    // a failure in the second entry is reported though the first is fine
    assert_eq!(
        refusal(&d, &d, Some(&["$.mtu", "$.nope"])).code,
        "UNKNOWN_FIELD"
    );
}

#[test]
fn a_place_below_a_value_that_cannot_hold_it_is_refused() {
    for (src, scope) in [
        ("opt: null\n", "$.opt.x"),
        ("nics: null\n", r#"$.nics[{name="n",zone="z"}].subnet"#),
    ] {
        let d = doc(src);
        assert!(
            matches!(run(&d, &d, Some(&[scope])), Err(Error::Unsupported(_))),
            "{scope}"
        );
    }
}

#[test]
fn null_against_a_collection() {
    for src in ["opt: null\n", "nics: null\n", "oset: null\n"] {
        assert_eq!(canonical(src, src, None), NONE, "{src}");
    }
    for (x, y) in [
        ("opt: null\n", "opt: {x: a}\n"),
        ("opt: {x: a}\n", "opt: null\n"),
        ("nics: null\n", "nics: []\n"),
        ("nics: []\n", "nics: null\n"),
        ("oset: null\n", "oset: [a]\n"),
        ("oset: [a]\n", "oset: null\n"),
    ] {
        assert!(
            matches!(run(&doc(x), &doc(y), None), Err(Error::Unsupported(_))),
            "{x:?} against {y:?}"
        );
    }
    // a list that declares no elements is not compared
    assert!(matches!(
        run(&doc("plain: [a]\n"), &doc("plain: [b]\n"), None),
        Err(Error::Unsupported(_))
    ));
}

#[test]
fn sets() {
    let cases = [
        (
            "ids: [3, 1, 2]\n",
            "ids: [2, 5, -4]\n",
            r#"{"added":[-4,5],"kind":"set_change","path":"$.ids","removed":[1,3]}"#,
        ),
        (
            "ids: [10, 9]\n",
            "ids: []\n",
            r#"{"added":[],"kind":"set_change","path":"$.ids","removed":[9,10]}"#,
        ),
        (
            "flags: [true]\n",
            "flags: [false]\n",
            r#"{"added":[false],"kind":"set_change","path":"$.flags","removed":[true]}"#,
        ),
        ("flags: [true, false]\n", "flags: [false, true]\n", ""),
        ("ip: {addrs: [a, b, a]}\n", "ip: {addrs: [b, a]}\n", ""),
        (
            "ip: {addrs: [b, a]}\n",
            "ip: {addrs: [a, a, c, c]}\n",
            r#"{"added":["c"],"kind":"set_change","path":"$.ip.addrs","removed":["b"]}"#,
        ),
    ];
    for (x, y, event) in cases {
        let want = if event.is_empty() {
            NONE.to_string()
        } else {
            format!(r#"{{"changes":[{event}],"representation":[],"version":1}}"#)
        };
        assert_eq!(canonical(x, y, None), want, "{x:?} against {y:?}");
    }
    // an appearing set is sorted with its duplicates dropped
    assert_eq!(
        canonical("mtu: 1\n", "mtu: 1\nids: [3, 1, 3, 2]\n", None),
        format!(
            r#"{{"changes":[{{"kind":"added","next":{{{DFLT},"value":[1,2,3]}},"path":"$.ids"}}],"representation":[],"version":1}}"#
        )
    );
}

#[test]
fn a_fragment_keeps_every_declaration_and_orders_what_it_holds() {
    let b = "ports:\n  - {port: 10, label: ten}\n  - {port: 9, label: nine}\n  - port: {access: [OU=ops], modify: inherit, inherit: true, default_injection: 0, conformance: deprecated, value: 2}\nvlans:\n  - {vlan_id: 7, mode: m}\n  - {ref: x}\n  - {ref: a}\n";
    let got = canonical("mtu: 1\n", &format!("mtu: 1\n{b}"), None);
    let s = |v: &str| format!(r#"{{{DFLT},"value":"{v}"}}"#);
    let n = |v: &str| format!(r#"{{{DFLT},"value":{v}}}"#);
    let ports = format!(
        r#"[{{"port":{{"access":["OU=ops"],"conformance":"deprecated","default_injection":0,"inherit":true,"modify":"inherit","value":2}}}},{{"label":{},"port":{}}},{{"label":{},"port":{}}}]"#,
        s("nine"),
        n("9"),
        s("ten"),
        n("10")
    );
    let vlans = format!(
        r#"[{{"ref":{}}},{{"ref":{}}},{{"mode":{},"vlan_id":{}}}]"#,
        s("a"),
        s("x"),
        s("m"),
        n("7")
    );
    let want = format!(
        r#"{{"changes":[{{"kind":"added","next":{{{DFLT},"value":{ports}}},"path":"$.ports"}},{{"kind":"added","next":{{{DFLT},"value":{vlans}}},"path":"$.vlans"}}],"representation":[],"version":1}}"#
    );
    assert_eq!(got, want);
    let back = canonical(&format!("mtu: 1\n{b}"), "mtu: 1\n", None);
    let mirror = format!(
        r#"{{"changes":[{{"kind":"removed","path":"$.ports","previous":{{{DFLT},"value":{ports}}}}},{{"kind":"removed","path":"$.vlans","previous":{{{DFLT},"value":{vlans}}}}}],"representation":[],"version":1}}"#
    );
    assert_eq!(back, mirror);
}

#[test]
fn events_are_in_path_order() {
    let x = "name: a\nmtu: 1\nports:\n  - {port: 2, label: x}\n  - {port: 10, label: x}\nnics:\n  - {name: b, zone: z, subnet: x}\n  - {name: a, zone: z, subnet: x}\n";
    let y = "name: b\nmtu: 2\nports:\n  - {port: 2, label: y}\n  - {port: 10, label: y}\nnics:\n  - {name: b, zone: z, subnet: y}\n  - {name: a, zone: z, subnet: y}\n";
    let r = run(&doc(x), &doc(y), None).unwrap();
    let paths: Vec<&str> = r.changes.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(
        paths,
        [
            "$.mtu",
            "$.name",
            r#"$.nics[{name="a",zone="z"}].subnet"#,
            r#"$.nics[{name="b",zone="z"}].subnet"#,
            "$.ports[{port=2}].label",
            "$.ports[{port=10}].label"
        ]
    );
}

#[test]
fn representation_below_a_list_element_and_only_where_present_in_both() {
    let short = "nics:\n  - {name: n, zone: z, subnet: s}\nmtu: 1\n";
    let expanded = "nics:\n  - name: n\n    zone: z\n    subnet: {access: inherit, modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: s}\nname: {access: inherit, modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: x}\n";
    let want = format!(
        r#"{{"changes":[{{"kind":"removed","path":"$.mtu","previous":{{{DFLT},"value":1}}}},{{"kind":"added","next":{{{DFLT},"value":"x"}},"path":"$.name"}}],"representation":[{{"kind":"representation_change","next":"expanded","path":"$.nics[{{name=\"n\",zone=\"z\"}}].subnet","previous":"short"}}],"version":1}}"#
    );
    assert_eq!(canonical(short, expanded, None), want);
    // outside the scope a writing difference is not reported either
    assert_eq!(
        canonical(short, expanded, Some(&["$.mtu"])),
        format!(
            r#"{{"changes":[{{"kind":"removed","path":"$.mtu","previous":{{{DFLT},"value":1}}}}],"representation":[],"version":1}}"#
        )
    );
}

#[test]
fn an_inversion_mirrors_the_events() {
    let docs = [
        "mtu: 1\nname: a\nip: {gw: g, addrs: [a, b]}\nids: [1, 2]\nports:\n  - {port: 1, label: x}\n",
        "mtu: 2\nname: a\nip: {gw: h, addrs: [b, c]}\nids: [2, 3]\nports:\n  - {port: 1, label: y}\n  - {port: 2, label: z}\n",
        "mtu: {access: [OU=ops], modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: 2}\nnote: {x: [1]}\n",
        "note: null\nvlans:\n  - {ref: a}\n",
        "{}\n",
    ];
    fn mirror(k: &str) -> &str {
        match k {
            "added" => "removed",
            "removed" => "added",
            other => other,
        }
    }
    for (i, x) in docs.iter().enumerate() {
        for (j, y) in docs.iter().enumerate() {
            let ab = run(&doc(x), &doc(y), None).unwrap();
            let ba = run(&doc(y), &doc(x), None).unwrap();
            if i == j {
                assert!(ab.changes.is_empty() && ab.representation.is_empty());
            }
            assert_eq!(ab.changes.len(), ba.changes.len(), "{i} against {j}");
            assert_eq!(ab.representation.len(), ba.representation.len());
            for (e, m) in ab.changes.iter().zip(&ba.changes) {
                assert_eq!(e.path, m.path);
                assert_eq!(mirror(e.kind.as_str()), m.kind.as_str());
            }
        }
    }
}

#[test]
fn the_canonical_strings() {
    let cases = [
        ("plain", r#""plain""#),
        ("a\"b", r#""a\"b""#),
        ("a\\b", r#""a\\b""#),
        ("\u{8}\u{c}\n\r\t", r#""\b\f\n\r\t""#),
        ("\u{0}\u{1}\u{1f}", r#""\u0000\u0001\u001f""#),
        ("\u{7f} é \u{2028}", "\"\u{7f} é \u{2028}\""),
        ("/", r#""/""#),
    ];
    for (s, want) in cases {
        let d = Node::map(vec![("name", string(s))]);
        let r = run(&Node::map(vec![]), &d, None).unwrap();
        assert_eq!(
            r.canonical(),
            format!(
                r#"{{"changes":[{{"kind":"added","next":{{{DFLT},"value":{want}}},"path":"$.name"}}],"representation":[],"version":1}}"#
            ),
            "{s:?}"
        );
    }
}

#[test]
fn a_field_name_that_cannot_be_written_is_refused() {
    let top = shape(
        ShapeType::Composite(vec![("a\nb".to_string(), scalar(ScalarType::String))]),
        false,
    );
    let doc = |v: &str| Node::map(vec![("a\nb", string(v))]);
    let empty = Node::map(vec![]);
    for (x, y) in [
        (doc("x"), doc("y")),
        (empty.clone(), doc("y")),
        (doc("x"), empty),
    ] {
        let r = diff(&view(&x), &view(&y), &top, &a(), grammar(), None);
        assert!(matches!(r, Err(Error::Unsupported(_))), "{r:?}");
    }
}

#[test]
fn a_scope_below_a_null_in_one_operand_only() {
    let null = doc("opt: null\n");
    let ok = doc("opt: {x: a}\n");
    for (x, y) in [(&ok, &null), (&null, &ok)] {
        assert!(matches!(
            run(x, y, Some(&["$.opt.x"])),
            Err(Error::Unsupported(_))
        ));
    }
}

#[test]
fn a_null_collection_that_appears_keeps_its_null() {
    for field in ["opt", "oset", "nics", "note"] {
        assert_eq!(
            canonical("mtu: 1\n", &format!("mtu: 1\n{field}: null\n"), None),
            format!(
                r#"{{"changes":[{{"kind":"added","next":{{{DFLT},"value":null}},"path":"$.{field}"}}],"representation":[],"version":1}}"#
            )
        );
    }
}

#[test]
fn every_removed_element_is_reported_and_the_rest_is_still_compared() {
    let x = "ports:\n  - {port: 1, label: x}\n  - {port: 2, label: x}\n  - {port: 3, label: x}\n  - {port: 4, label: x}\n";
    let y = "ports:\n  - {port: 4, label: y}\n  - {port: 5, label: x}\n  - {port: 6, label: x}\n";
    let r = run(&doc(x), &doc(y), None).unwrap();
    let got: Vec<String> = r
        .changes
        .iter()
        .map(|e| format!("{} {}", e.kind.as_str(), e.path))
        .collect();
    assert_eq!(
        got,
        [
            "removed $.ports[{port=1}]",
            "removed $.ports[{port=2}]",
            "removed $.ports[{port=3}]",
            "value_change $.ports[{port=4}].label",
            "added $.ports[{port=5}]",
            "added $.ports[{port=6}]"
        ]
    );
}

#[test]
fn a_set_that_only_loses_or_only_gains_one_element() {
    for (x, y, event) in [
        (
            "ip: {addrs: [a, b]}\n",
            "ip: {addrs: [a]}\n",
            r#"{"added":[],"kind":"set_change","path":"$.ip.addrs","removed":["b"]}"#,
        ),
        (
            "ip: {addrs: [a]}\n",
            "ip: {addrs: [a, b]}\n",
            r#"{"added":["b"],"kind":"set_change","path":"$.ip.addrs","removed":[]}"#,
        ),
    ] {
        assert_eq!(
            canonical(x, y, None),
            format!(r#"{{"changes":[{event}],"representation":[],"version":1}}"#)
        );
    }
}

#[test]
fn the_scope_keeps_every_entry_that_is_not_inside_another() {
    let x = "mtu: 1\nname: x\nip: {gw: g1}\n";
    let y = "mtu: 2\nname: y\nip: {gw: g2}\n";
    let all = canonical(x, y, None);
    for scope in [
        vec!["$.mtu", "$.name", "$.ip.gw"],
        vec!["$.ip.gw", "$.name", "$.mtu"],
        vec!["$.mtu", "$.ip", "$.name"],
    ] {
        assert_eq!(canonical(x, y, Some(&scope)), all, "{scope:?}");
    }
    let two = canonical(x, y, Some(&["$.mtu", "$.ip.gw"]));
    assert!(
        two != all
            && two != canonical(x, y, Some(&["$.mtu"]))
            && two != canonical(x, y, Some(&["$.ip.gw"]))
    );
}

#[test]
fn an_unsupported_number_after_entries_that_are_clean() {
    let flt = || unsupported("1.5");
    let clean = doc("mtu: 1\n");
    let cases: Vec<(&str, Node, &str)> = vec![
        (
            "as the value of a list position",
            Node::map(vec![("nics", flt())]),
            "$.nics",
        ),
        (
            "in a later element after one without an identity",
            Node::map(vec![(
                "nics",
                seq(vec![
                    Node::map(vec![("name", string("n"))]),
                    Node::map(vec![
                        ("name", string("n")),
                        ("zone", string("n")),
                        ("subnet", flt()),
                    ]),
                ]),
            )]),
            r#"$.nics[{name="n",zone="n"}].subnet"#,
        ),
        (
            "after an undeclared key that is clean",
            Node::map(vec![("bogus", string("n")), ("mtu", flt())]),
            "$.mtu",
        ),
    ];
    for (name, bad, path) in cases {
        let v = refusal(&bad, &clean, None);
        assert_eq!(
            (v.code, v.logical_path.as_deref(), v.operand),
            ("DIFF_UNSUPPORTED_VALUE", Some(path), Some("a")),
            "{name}"
        );
    }
}

#[test]
fn a_composite_that_appears_is_sorted_inside_and_a_float_inside_an_expanded_composite_is_named() {
    assert_eq!(
        canonical("mtu: 1\n", "mtu: 1\nip: {addrs: [b, a]}\n", None),
        format!(
            r#"{{"changes":[{{"kind":"added","next":{{{DFLT},"value":{{"addrs":{{{DFLT},"value":["a","b"]}}}}}},"path":"$.ip"}}],"representation":[],"version":1}}"#
        )
    );
    let inner = Node::map(vec![("gw", unsupported("1.5"))]);
    let bad = Node::map(vec![("ip", wrapper(inner, None))]);
    let v = refusal(&bad, &doc("mtu: 1\n"), None);
    assert_eq!(v.logical_path.as_deref(), Some("$.ip.gw"));
}

// ---- patch (ADR 0013) at the places a vector of corpus/patch cannot reach; the Go peer is go/patch_test.go --------------------------------------

use cic_primitive_forms::{patch, EventKind, PatchDiff, PatchEvent, PatchResult};

const PATCH_DOC: &str = "mtu: 1\nname: a\nip:\n  gw: g\nports:\n  - port: 1\n    label: x\n  - port: 9\n    label: y\nvlans:\n  - ref: r\n  - vlan_id: 7\n    mode: m\nnics:\n  - name: n\n    zone: z\n    subnet: s\n";

fn vc(path: &str, previous: Node, next: Node) -> PatchEvent {
    PatchEvent {
        kind: EventKind::ValueChange,
        path: path.to_string(),
        previous: Some(previous),
        next: Some(next),
    }
}

fn run_patch(text: &str, changes: Vec<PatchEvent>) -> Result<PatchResult, Error> {
    let c = DiffContext::default();
    patch(
        text,
        &root(),
        &a(),
        grammar(),
        &c,
        &c,
        &PatchDiff {
            version: 1,
            changes,
        },
    )
}

fn patch_refusal(text: &str, changes: Vec<PatchEvent>) -> Violation {
    match run_patch(text, changes) {
        Err(Error::Contract(vs)) => {
            assert_eq!(vs.len(), 1);
            vs[0].clone()
        }
        other => panic!("expected one contract error, got {other:?}"),
    }
}

fn at(v: &Violation) -> String {
    match v.location {
        cic_primitive_forms::Location::At(p) => format!("{}:{}:{}", p.line, p.column, p.offset),
        cic_primitive_forms::Location::NotApplicable => "not_applicable".to_string(),
        cic_primitive_forms::Location::Unavailable => "unavailable".to_string(),
    }
}

#[test]
fn patch_applying_and_the_spans() {
    let res = run_patch(
        PATCH_DOC,
        vec![
            vc("$.mtu", int(1), int(12345)),
            vc("$.name", string("a"), string("bcd")),
            vc("$.ports[{port=9}].label", string("y"), string("yy")),
            vc(
                r#"$.nics[{name="n",zone="z"}].subnet"#,
                string("s"),
                string("t"),
            ),
            vc("$.vlans[{vlan_id=7}].mode", string("m"), string("n")),
        ],
    )
    .unwrap();
    let want = "mtu: 12345\nname: bcd\nip:\n  gw: g\nports:\n  - port: 1\n    label: x\n  - port: 9\n    label: yy\nvlans:\n  - ref: r\n  - vlan_id: 7\n    mode: n\nnics:\n  - name: n\n    zone: z\n    subnet: t\n";
    assert_eq!(res.text, want);
    assert_eq!(
        res.replaced,
        [
            "$.mtu",
            "$.name",
            "$.ports[{port=9}].label",
            r#"$.nics[{name="n",zone="z"}].subnet"#,
            "$.vlans[{vlan_id=7}].mode"
        ]
    );
    for sp in &res.spans {
        assert_eq!(
            sp.before,
            cic_primitive_forms::source_span(PATCH_DOC, &root(), &sp.path, &a()).unwrap()
        );
        assert_eq!(
            sp.after,
            cic_primitive_forms::source_span(&res.text, &root(), &sp.path, &a()).unwrap()
        );
    }
    assert_eq!(res.spans[0].after.end - res.spans[0].after.start, 5);
    // the same diff on its own result is a conflict; the empty diff is the identity
    let v = patch_refusal(&res.text, vec![vc("$.mtu", int(1), int(12345))]);
    assert_eq!(
        (v.code, v.logical_path.as_deref()),
        ("PATCH_CONFLICT", Some("$.mtu"))
    );
    let same = run_patch(PATCH_DOC, vec![]).unwrap();
    assert!(same.text == PATCH_DOC && same.replaced.is_empty() && same.spans.is_empty());
    // an event whose next is its previous is accepted and moves nothing
    let noop = run_patch(PATCH_DOC, vec![vc("$.mtu", int(1), int(1))]).unwrap();
    assert!(noop.text == PATCH_DOC && noop.replaced == ["$.mtu"]);
}

#[test]
fn patch_the_key_components_and_the_targets_it_refuses() {
    for path in [
        "$.ports[{port=1}].port",
        r#"$.vlans[{ref="r"}].ref"#,
        "$.vlans[{vlan_id=7}].vlan_id",
        r#"$.nics[{name="n",zone="z"}].name"#,
        r#"$.nics[{name="n",zone="z"}].zone"#,
        "$.ip",
        "$.ip.addrs",
        "$.ports",
        "$.note",
        "$.ports[{port=1}]",
    ] {
        let v = patch_refusal(PATCH_DOC, vec![vc(path, string("x"), string("y"))]);
        assert_eq!(
            (v.code, v.phase, at(&v), v.logical_path.as_deref()),
            (
                "PATCH_UNSUPPORTED_EVENT",
                Phase::Patch,
                "not_applicable".to_string(),
                Some(path)
            ),
            "{path}"
        );
    }
    for (path, previous) in [
        ("$.ip.gw", "g"),
        ("$.ports[{port=1}].label", "x"),
        (r#"$.nics[{name="n",zone="z"}].subnet"#, "s"),
        ("$.vlans[{vlan_id=7}].mode", "m"),
    ] {
        assert!(
            run_patch(PATCH_DOC, vec![vc(path, string(previous), string("q"))]).is_ok(),
            "{path}"
        );
    }
    // a value change that does not carry both values
    for e in [
        PatchEvent {
            kind: EventKind::ValueChange,
            path: "$.mtu".into(),
            previous: None,
            next: Some(int(2)),
        },
        PatchEvent {
            kind: EventKind::ValueChange,
            path: "$.mtu".into(),
            previous: Some(int(1)),
            next: None,
        },
    ] {
        assert_eq!(
            patch_refusal(PATCH_DOC, vec![e]).code,
            "PATCH_UNSUPPORTED_EVENT"
        );
    }
}

#[test]
fn patch_the_order_of_the_checks_across_events() {
    let absent = || {
        vc(
            r#"$.nics[{name="zz",zone="z"}].subnet"#,
            string("s"),
            string("t"),
        )
    };
    let conflict = || vc("$.mtu", int(2), int(3));
    let bad_path = || vc("$.nope", int(1), int(2));
    let unsupported = || PatchEvent {
        kind: EventKind::SetChange,
        path: "$.ip.addrs".into(),
        previous: None,
        next: None,
    };
    let v = patch_refusal(PATCH_DOC, vec![conflict(), bad_path(), unsupported()]);
    assert_eq!(
        (v.code, v.logical_path.as_deref()),
        ("PATCH_UNSUPPORTED_EVENT", Some("$.ip.addrs"))
    );
    let v = patch_refusal(PATCH_DOC, vec![absent(), conflict(), bad_path()]);
    assert_eq!(
        (v.code, v.phase, v.logical_path.as_deref()),
        ("UNKNOWN_FIELD", Phase::PathResolve, Some("$.nope"))
    );
    let v = patch_refusal(PATCH_DOC, vec![absent(), conflict()]);
    assert_eq!(
        (v.code, at(&v)),
        ("PATCH_TARGET_ABSENT", "15:3:137".to_string())
    );
    let v = patch_refusal(PATCH_DOC, vec![conflict(), absent()]);
    assert_eq!(
        (v.code, v.logical_path.as_deref(), at(&v)),
        ("PATCH_CONFLICT", Some("$.mtu"), "1:6:5".to_string())
    );
    let v = patch_refusal("name: a\n", vec![vc("$.mtu", int(1), int(2))]);
    assert_eq!(
        (v.code, at(&v)),
        ("PATCH_TARGET_ABSENT", "1:1:0".to_string())
    );
    assert_eq!(
        patch_refusal(PATCH_DOC, vec![vc("$.mtu", string("1"), int(2))]).code,
        "PATCH_CONFLICT"
    );
    assert_eq!(
        patch_refusal(PATCH_DOC, vec![vc("$.", int(1), int(2))]).phase,
        Phase::PathParse
    );
}

#[test]
fn patch_the_context_and_the_earlier_phases() {
    let base = DiffContext {
        schema: "s".into(),
        schema_digest: "d".into(),
        grammar: "g".into(),
        grammar_digest: "gd".into(),
        surface: "x".into(),
    };
    let run = |text: &str, schema: &DiffContext, context: &DiffContext, version: i64| {
        patch(
            text,
            &root(),
            &a(),
            grammar(),
            schema,
            context,
            &PatchDiff {
                version,
                changes: vec![],
            },
        )
    };
    let variants = [
        DiffContext {
            schema: "t".into(),
            ..base.clone()
        },
        DiffContext {
            schema_digest: "t".into(),
            ..base.clone()
        },
        DiffContext {
            grammar: "t".into(),
            ..base.clone()
        },
        DiffContext {
            grammar_digest: "t".into(),
            ..base.clone()
        },
        DiffContext {
            surface: "t".into(),
            ..base.clone()
        },
    ];
    for (i, other) in variants.iter().enumerate() {
        match run(PATCH_DOC, &base, other, 1) {
            Err(Error::Contract(vs)) => assert_eq!(
                (vs[0].code, vs[0].phase, vs[0].logical_path.clone()),
                ("PATCH_CONTEXT_MISMATCH", Phase::Patch, None),
                "context {i}"
            ),
            other => panic!("context {i}: {other:?}"),
        }
    }
    for version in [0, 2, -1] {
        assert_eq!(
            run(PATCH_DOC, &base, &base, version).unwrap_err().code(),
            "PATCH_CONTEXT_MISMATCH",
            "version {version}"
        );
    }
    assert!(run(PATCH_DOC, &base, &base, 1).is_ok());
    for (src, code) in [
        ("a: 1\na: 2\n", "DUPLICATE_KEY"),
        ("bogus: 1\n", "UNKNOWN_FIELD"),
    ] {
        assert_eq!(
            run(src, &base, &variants[0], 9).unwrap_err().code(),
            code,
            "{src:?}"
        );
    }
}
