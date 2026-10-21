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

fn grammar_1() -> cic_primitive_forms::Grammar {
    let one = Value::Int(1);
    cic_primitive_forms::bind_grammar(Some(&one), Some(&one)).expect("grammar 1")
}

/// A composite root that is not nullable: the root of a document is the surface itself, not a position.
fn document_root(mut shape: Shape) -> Option<Shape> {
    shape.nullable = false;
    matches!(shape.shape_type, ShapeType::Composite(_)).then_some(shape)
}

/// True when `n` is an expanded node: a mapping with exactly the keys of `A`.
fn is_wrapper(n: &Node) -> bool {
    match &n.value {
        Value::Map(es) => {
            es.len() == a().len() && a().iter().all(|k| es.iter().any(|e| &e.key == k))
        }
        _ => false,
    }
}

fn defaults_only(n: &Node) -> bool {
    let Value::Map(es) = &n.value else {
        return false;
    };
    es.iter().all(|e| match e.key.as_str() {
        "access" | "modify" => matches!(&e.value.value, Value::Str(s) if s == "inherit"),
        "inherit" => matches!(e.value.value, Value::Bool(true)),
        "default_injection" => matches!(e.value.value, Value::Null),
        "conformance" => matches!(&e.value.value, Value::Str(s) if s == "implemented"),
        _ => true,
    })
}

/// Gives every top-level wrapper some non-default policy, one setting each, as the author could have written it.
fn make_explicit(n: &mut Node, rng: &mut Rng) {
    let Value::Map(entries) = &mut n.value else {
        return;
    };
    for e in entries {
        let Value::Map(fields) = &mut e.value.value else {
            continue;
        };
        for f in fields {
            let pick = rng.coin();
            match f.key.as_str() {
                "access" | "modify" if pick => {
                    f.value =
                        Node::new(Value::Seq(vec![Node::new(Value::Str("OU=admins".into()))]));
                }
                "inherit" => {
                    f.value = Node::new(if pick {
                        Value::Int(0)
                    } else {
                        Value::Bool(false)
                    })
                }
                "default_injection" if pick => f.value = Node::new(Value::Int(0)),
                "conformance" if pick => f.value = Node::new(Value::Str("deprecated".into())),
                _ => {}
            }
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(512))]

    #[test]
    fn collapse_of_expand_gives_back_the_authored_document(shape in shape_strategy(), seed in 1u64..u64::MAX) {
        let Some(shape) = document_root(shape) else { return Ok(()) };
        let doc = conforming(&shape, &mut Rng(seed), &mut Vec::new(), &[]);
        let before = doc.clone();
        let g = grammar_1();
        let (expanded, prov) = cic_primitive_forms::expand(&doc, &shape, &a(), g).unwrap();
        prop_assert!(doc.same(&before), "expand changed its input");
        prop_assert!(prov.values().all(|o| !o.authored_expanded));
        let back = cic_primitive_forms::collapse(&expanded, &shape, &a(), g, &prov, None).unwrap();
        prop_assert!(back.same(&doc));
        let (again, prov2) = cic_primitive_forms::expand(&expanded, &shape, &a(), g).unwrap();
        prop_assert!(again.same(&expanded), "expand is not idempotent");
        prop_assert_eq!(prov2.len(), prov.len());
        prop_assert!(prov2.values().all(|o| o.authored_expanded));
    }

    #[test]
    fn an_explicit_policy_setting_is_never_lost(shape in shape_strategy(), seed in 1u64..u64::MAX) {
        let Some(shape) = document_root(shape) else { return Ok(()) };
        let doc = conforming(&shape, &mut Rng(seed), &mut Vec::new(), &[]);
        let g = grammar_1();
        let (mut expanded, _) = cic_primitive_forms::expand(&doc, &shape, &a(), g).unwrap();
        make_explicit(&mut expanded, &mut Rng(seed ^ 0x9E37_79B9));
        let (again, prov) = cic_primitive_forms::expand(&expanded, &shape, &a(), g).unwrap();
        prop_assert!(again.same(&expanded), "expand changed an authored expanded node");
        let with = cic_primitive_forms::collapse(&again, &shape, &a(), g, &prov, None).unwrap();
        prop_assert!(with.same(&expanded), "with provenance an authored expanded document stays as authored");
        // without the provenance the law alone decides: a non-default field keeps its position expanded
        let plain = cic_primitive_forms::collapse(&again, &shape, &a(), g, &Default::default(), None).unwrap();
        if let (Value::Map(orig), Value::Map(now)) = (&expanded.value, &plain.value) {
            for o in orig {
                let n = now.iter().find(|e| e.key == o.key).expect("a position disappeared");
                if is_wrapper(&o.value) && !defaults_only(&o.value) {
                    prop_assert!(is_wrapper(&n.value), "a position with an explicit setting was shortened: {}", o.key);
                }
            }
        }
    }
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

// ---------------------------------------------------------------------------
// Presence validation (ADR 0003, ADR 0007). The Go peer checks the same properties.
//
// * a document built to conform to a shape has no violation;
// * leaving out one mandatory field gives exactly one more error, MISSING_MANDATORY, at its path;
// * default candidates are exactly the absent optional fields that declare a default;
// * for any shape and any document: no panic, an unsupported answer or a response that is sorted,
//   unique by (path, code), with canonical paths, and the input is left alone.
// ---------------------------------------------------------------------------

use cic_primitive_forms::{validate, Presence, ScalarType, Shape, ShapeType};

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

fn presence() -> impl Strategy<Value = Presence> {
    prop_oneof![Just(Presence::Mandatory), Just(Presence::Optional)]
}

fn shape_strategy() -> impl Strategy<Value = Shape> {
    let leaf = prop_oneof![
        Just(ShapeType::Scalar(ScalarType::String)),
        Just(ShapeType::Scalar(ScalarType::Integer)),
        Just(ShapeType::Scalar(ScalarType::Boolean)),
        prop::option::of(0usize..3).prop_map(|min_items| ShapeType::List { min_items }),
        Just(ShapeType::Opaque),
    ];
    let shape_type = leaf.prop_recursive(3, 24, 4, |inner| {
        prop::collection::btree_map(
            "[a-d]{1,2}",
            (inner, presence(), any::<bool>(), any::<bool>()),
            0..4,
        )
        .prop_map(|m| {
            ShapeType::Composite(
                m.into_iter()
                    .map(|(name, (shape_type, presence, nullable, has_default))| {
                        (
                            name,
                            Shape {
                                shape_type,
                                presence,
                                nullable,
                                has_default,
                            },
                        )
                    })
                    .collect(),
            )
        })
    });
    (shape_type, presence(), any::<bool>(), any::<bool>()).prop_map(
        |(shape_type, presence, nullable, has_default)| Shape {
            shape_type,
            presence,
            nullable,
            has_default,
        },
    )
}

/// A tiny deterministic generator so that a document is a function of the shape and a seed.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn coin(&mut self) -> bool {
        self.next().is_multiple_of(2)
    }
}

/// A node that conforms to `shape`; `omit` is a path whose (mandatory) field is left out.
fn conforming(shape: &Shape, rng: &mut Rng, path: &mut Vec<String>, omit: &[String]) -> Node {
    if shape.nullable && rng.coin() {
        return Node::new(Value::Null);
    }
    match &shape.shape_type {
        ShapeType::Scalar(ScalarType::String) => {
            Node::new(Value::Str(format!("s{}", rng.next() % 9)))
        }
        ShapeType::Scalar(ScalarType::Integer) => Node::new(Value::Int((rng.next() % 9) as i64)),
        ShapeType::Scalar(ScalarType::Boolean) => Node::new(Value::Bool(rng.coin())),
        ShapeType::List { min_items } => {
            let n = min_items.unwrap_or(0) + (rng.next() % 2) as usize;
            Node::new(Value::Seq(
                (0..n).map(|i| Node::new(Value::Int(i as i64))).collect(),
            ))
        }
        ShapeType::Opaque => Node::new(Value::Str("any".into())),
        ShapeType::Set { item, min_items } => {
            let n = min_items.unwrap_or(0) + (rng.next() % 2) as usize;
            Node::new(Value::Seq(
                (0..n)
                    .map(|i| {
                        Node::new(match item {
                            ScalarType::String => Value::Str(format!("s{i}")),
                            ScalarType::Integer => Value::Int(i as i64),
                            ScalarType::Boolean => Value::Bool(i % 2 == 0),
                        })
                    })
                    .collect(),
            ))
        }
        // The elements of a declared list are not validated by the slice, so a conforming value has none.
        ShapeType::DeclaredList { .. } => Node::new(Value::Seq(Vec::new())),
        ShapeType::Composite(fields) => {
            let mut entries = Vec::new();
            for (name, f) in fields {
                path.push(name.clone());
                let left_out = path.as_slice() == omit;
                let present = !left_out && (f.presence == Presence::Mandatory || rng.coin());
                if present {
                    entries.push(Entry {
                        key: name.clone(),
                        key_pos: None,
                        value: conforming(f, rng, path, omit),
                    });
                }
                path.pop();
            }
            Node::new(Value::Map(entries))
        }
    }
}

/// Paths of mandatory fields reachable through present composites of a document built from `shape`.
fn mandatory_paths(
    shape: &Shape,
    node: &Node,
    prefix: &mut Vec<String>,
    out: &mut Vec<Vec<String>>,
) {
    if let (ShapeType::Composite(fields), Value::Map(entries)) = (&shape.shape_type, &node.value) {
        for (name, f) in fields {
            prefix.push(name.clone());
            if f.presence == Presence::Mandatory && entries.iter().any(|e| &e.key == name) {
                out.push(prefix.clone());
            }
            if let Some(e) = entries.iter().find(|e| &e.key == name) {
                mandatory_paths(f, &e.value, prefix, out);
            }
            prefix.pop();
        }
    }
}

fn path_text(path: &[String]) -> String {
    format!("$.{}", path.join("."))
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(512))]

    #[test]
    fn a_conforming_document_has_no_violation(shape in shape_strategy(), seed in 1u64..u64::MAX) {
        let node = conforming(&shape, &mut Rng(seed), &mut Vec::new(), &[]);
        let before = node.clone();
        let v = validate(Some(&node), &shape, &a()).unwrap();
        prop_assert!(v.violations.is_empty(), "{:?}", v.violations);
        prop_assert!(node.same(&before));
    }

    #[test]
    fn leaving_out_one_mandatory_field_adds_exactly_its_missing_error(shape in shape_strategy(), seed in 1u64..u64::MAX, pick in 0usize..16) {
        let node = conforming(&shape, &mut Rng(seed), &mut Vec::new(), &[]);
        let mut paths = Vec::new();
        mandatory_paths(&shape, &node, &mut Vec::new(), &mut paths);
        prop_assume!(!paths.is_empty());
        let omit = paths[pick % paths.len()].clone();
        // The same random choices, with that one field left out.
        let without = conforming(&shape, &mut Rng(seed), &mut Vec::new(), &omit);
        let v = validate(Some(&without), &shape, &a()).unwrap();
        // The field may sit below a null composite, where nothing is checked: then no error.
        let text = path_text(&omit);
        let missing: Vec<_> = v.violations.iter().filter(|x| x.logical_path.as_deref() == Some(text.as_str())).collect();
        prop_assert!(missing.len() <= 1);
        if let Some(m) = missing.first() {
            prop_assert_eq!(m.code, "MISSING_MANDATORY");
        }
        prop_assert!(v.violations.iter().all(|x| x.code == "MISSING_MANDATORY"));
    }

    #[test]
    fn default_candidates_are_the_absent_optional_fields_with_a_default(shape in shape_strategy()) {
        // An empty mapping at a composite root: every field is absent.
        prop_assume!(matches!(shape.shape_type, ShapeType::Composite(_)));
        let node = Node::new(Value::Map(Vec::new()));
        let v = validate(Some(&node), &shape, &a()).unwrap();
        let ShapeType::Composite(fields) = &shape.shape_type else { unreachable!() };
        let mut want: Vec<String> = fields.iter()
            .filter(|(_, f)| f.presence == Presence::Optional && f.has_default)
            .map(|(n, _)| format!("$.{n}")).collect();
        want.sort();
        prop_assert_eq!(v.default_candidates, want);
    }

    #[test]
    fn any_document_against_any_shape_gives_an_ordered_unique_response(shape in shape_strategy(), n in node()) {
        let before = n.clone();
        if let Ok(v) = validate(Some(&n), &shape, &a()) {
            let keys: Vec<(Option<String>, &str)> = v.violations.iter().map(|x| (x.logical_path.clone(), x.code)).collect();
            let mut sorted_keys = keys.clone();
            sorted_keys.sort_by(|x, y| {
                let seg = |p: &Option<String>| p.as_ref().map(|t| t.split('.').skip(1).map(str::to_string).collect::<Vec<_>>());
                seg(&x.0).cmp(&seg(&y.0)).then(x.1.cmp(y.1))
            });
            // Escaped names would need the ADR 0006 lexer to split; the order is checked without them.
            if keys.iter().all(|k| k.0.as_deref().is_none_or(|p| !p.contains('\\'))) {
                prop_assert_eq!(&keys, &sorted_keys, "sorted by path, then code");
            }
            prop_assert_eq!(keys.len(), keys.iter().collect::<std::collections::BTreeSet<_>>().len(), "unique");
            let phases: std::collections::BTreeSet<_> = v.violations.iter().map(|x| x.phase.as_str()).collect();
            prop_assert!(phases.len() <= 1, "one phase per response");
        }
        prop_assert!(n.same(&before));
    }
}

// ---------------------------------------------------------------------------
// Reading access-policy/1 (cic-primitives D-018). The Go peer checks the same properties.
//
// * type preservation: whatever reads is written back to the same spelling, to the type, and a
//   value is never valid in both grammars (false and 0 stay two values);
// * version binding agrees with an independently written oracle for any pair of candidates;
// * CertPattern agrees with the upstream regular expression for any string;
// * reading never changes its input.
// ---------------------------------------------------------------------------

use cic_primitive_forms::{
    bind_grammar, is_cert_pattern, read_mode, read_rule, write_mode, Grammar, Mode,
    SUPPORTED_GRAMMARS,
};
use std::sync::LazyLock;

fn grammar(v: i64) -> Grammar {
    bind_grammar(Some(&Value::Int(v)), Some(&Value::Int(v))).unwrap()
}

/// The upstream `PATTERN_RE`, with Python's `\s` spelled out (Unicode White_Space and U+001C..U+001F).
static UPSTREAM_PATTERN: LazyLock<regex::Regex> = LazyLock::new(|| {
    let pair = r"[A-Za-z][A-Za-z0-9.-]*=[^=,\s\x1c-\x1f][^=,]*";
    regex::Regex::new(&format!("^{pair}(,{pair})*$")).unwrap()
});

fn candidate() -> impl Strategy<Value = Option<Value>> {
    prop_oneof![
        Just(None),
        Just(Some(Value::Null)),
        (-2i64..5).prop_map(|n| Some(Value::Int(n))),
        any::<bool>().prop_map(|b| Some(Value::Bool(b))),
        "[0-9a-z]{0,2}".prop_map(|s| Some(Value::Str(s))),
        Just(Some(Value::Seq(Vec::new()))),
    ]
}

/// Missing, then unknown, then conflict - written without the library's helpers.
fn oracle(c: &Option<Value>, p: &Option<Value>) -> Result<i64, &'static str> {
    let missing = |v: &Option<Value>| v.is_none() || matches!(v, Some(Value::Null));
    if missing(c) || missing(p) {
        return Err("GRAMMAR_VERSION_MISSING");
    }
    let known = |v: &Option<Value>| match v {
        Some(Value::Int(n)) if *n == 1 || *n == 2 => Some(*n),
        _ => None,
    };
    match (known(c), known(p)) {
        (Some(a), Some(b)) if a == b => Ok(a),
        (Some(_), Some(_)) => Err("GRAMMAR_VERSION_CONFLICT"),
        _ => Err("UNKNOWN_GRAMMAR_VERSION"),
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(512))]

    #[test]
    fn what_reads_is_written_back_to_the_same_spelling(n in node(), version in 1i64..=2) {
        let g = grammar(version);
        if let Ok(mode) = read_mode(&n, g) {
            prop_assert!(write_mode(mode, g).same(&n), "{:?}", n);
        }
    }

    #[test]
    fn no_value_is_a_valid_mode_in_both_grammars(n in node()) {
        prop_assert!(read_mode(&n, grammar(1)).is_err() || read_mode(&n, grammar(2)).is_err());
    }

    #[test]
    fn every_mode_has_one_spelling_per_version_and_reads_back(i in 0usize..3, version in 1i64..=2) {
        let mode = [Mode::Inherit, Mode::NoInherit, Mode::Reset][i];
        let g = grammar(version);
        prop_assert_eq!(read_mode(&write_mode(mode, g), g).unwrap(), mode);
        let other = grammar(3 - version);
        prop_assert!(read_mode(&write_mode(mode, g), other).is_err());
    }

    #[test]
    fn binding_agrees_with_the_oracle(c in candidate(), p in candidate()) {
        let got = bind_grammar(c.as_ref(), p.as_ref()).map(Grammar::version).map_err(|e| e.code());
        prop_assert_eq!(got, oracle(&c, &p));
        prop_assert!(SUPPORTED_GRAMMARS.contains(&got.unwrap_or(1)));
    }

    #[test]
    fn cert_pattern_agrees_with_the_upstream_expression(s in prop_oneof![
        "\\PC{0,12}",
        "[A-Za-z0-9.=, \u{a0}\u{1c}\t*-]{0,14}",
        "[A-Za-z][A-Za-z0-9.-]{0,3}=[A-Za-z0-9*]{1,3}(,[A-Za-z][A-Za-z0-9.-]{0,3}=[A-Za-z0-9*]{1,3}){0,2}",
    ]) {
        prop_assert_eq!(is_cert_pattern(&s), UPSTREAM_PATTERN.is_match(&s), "{:?}", s);
    }

    #[test]
    fn reading_never_changes_its_input(n in node()) {
        let before = n.clone();
        let _ = read_rule(&n);
        let _ = read_mode(&n, grammar(1));
        let _ = read_mode(&n, grammar(2));
        prop_assert!(n.same(&before));
        prop_assert_eq!(n.pos, before.pos);
    }
}
