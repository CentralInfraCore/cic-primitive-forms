//! Dumps the outcome of `edit_scalar` for the generated inputs of `tools/parity/gen_edit.py`, for the Rust/Go differential of the six guarantees of
//! ADR 0012 (P3b step E4). `PARITY_EDIT_IN` is a JSON-lines file, `PARITY_EDIT_OUT` receives one JSON line per input. Without the two variables
//! this test does nothing. The Go peer is `go/parity_edit_dump_test.go`; `tools/parity/compare_edit.py` compares and checks the guarantees.
//!
//! For each input: the span of the scalar before the edit (`before`), the edit, the edit to the value the scalar already holds (`noop`), the edit back to it (`back`), and the two orders of the edit
//! and the edit of an independent position (`ab`, `ba`). An answer is the text with its span and whether anything changed, or the refusal (code,
//! phase, location, logical path of every violation), or the kind of an error outside the contract; the wording of a message is not compared.

use cic_primitive_forms::{
    edit_scalar, parse, source_span, Elements, Error, Location, Node, Presence, ScalarType, Shape,
    ShapeType, Value,
};
use cic_primitive_forms::{Case, Phase};
use serde_json::{json, Value as Json};
use std::fs;

fn shape(shape_type: ShapeType) -> Shape {
    Shape {
        shape_type,
        presence: Presence::Optional,
        nullable: false,
        has_default: false,
        default: None,
    }
}

fn root() -> Shape {
    let string = || shape(ShapeType::Scalar(ScalarType::String));
    let integer = || shape(ShapeType::Scalar(ScalarType::Integer));
    shape(ShapeType::Composite(vec![
        ("name".into(), string()),
        ("count".into(), integer()),
        (
            "enabled".into(),
            shape(ShapeType::Scalar(ScalarType::Boolean)),
        ),
        (
            "ip".into(),
            shape(ShapeType::Composite(vec![
                ("gateway".into(), string()),
                ("prefix".into(), integer()),
            ])),
        ),
        (
            "routes".into(),
            shape(ShapeType::DeclaredList {
                min_items: None,
                elements: Elements {
                    choice: false,
                    cases: vec![Case {
                        name: "route".into(),
                        fields: vec![("to".into(), string()), ("metric".into(), integer())],
                        key: vec!["to".into()],
                    }],
                },
            }),
        ),
        (
            "tags".into(),
            shape(ShapeType::Set {
                item: ScalarType::String,
                min_items: None,
            }),
        ),
    ]))
}

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

fn value(v: &Json) -> Node {
    let o = v.as_object().expect("a typed value");
    if let Some(s) = o.get("string") {
        return Node::new(Value::Str(s.as_str().expect("string").to_string()));
    }
    if let Some(i) = o.get("integer") {
        return Node::new(Value::Int(i.as_i64().expect("integer")));
    }
    if let Some(b) = o.get("boolean") {
        return Node::new(Value::Bool(b.as_bool().expect("boolean")));
    }
    if o.contains_key("map") {
        return Node::new(Value::Map(vec![]));
    }
    if o.contains_key("seq") {
        return Node::new(Value::Seq(vec![]));
    }
    Node::new(Value::Null)
}

fn phase(p: Phase) -> &'static str {
    match p {
        Phase::Parse => "parse",
        Phase::Recognize => "recognize",
        Phase::Validate => "validate",
        Phase::PathOf => "path_of",
        Phase::PathParse => "path_parse",
        Phase::PathFormat => "path_format",
        Phase::PathResolve => "path_resolve",
        Phase::Edit => "edit",
        Phase::Diff => "diff",
        Phase::Patch => "patch",
    }
}

/// A parsed tree as JSON: the kind and the value of a scalar, a mapping with its entries in source order, a sequence. Positions are not part of it.
fn tree(n: &Node) -> Json {
    match &n.value {
        Value::Null => json!({"null": true}),
        Value::Bool(b) => json!({"boolean": b}),
        Value::Int(i) => json!({"integer": i}),
        Value::Str(s) => json!({"string": s}),
        Value::Unsupported(s) => json!({"unsupported": s}),
        Value::Seq(items) => json!({"seq": items.iter().map(tree).collect::<Vec<_>>()}),
        Value::Map(entries) => {
            json!({"map": entries.iter().map(|e| json!([e.key, tree(&e.value)])).collect::<Vec<_>>()})
        }
    }
}

fn answer(r: &Result<cic_primitive_forms::EditResult, Error>) -> Json {
    match r {
        Ok(e) => {
            json!({"ok": true, "text": e.text, "span": [e.span.start, e.span.end, e.span.style.as_str()], "changed": e.changed})
        }
        Err(Error::Contract(vs)) => {
            let errs: Vec<Json> = vs
                .iter()
                .map(|v| {
                    let loc = match v.location {
                        Location::At(p) => format!("{}:{}:{}", p.line, p.column, p.offset),
                        Location::NotApplicable => "not_applicable".to_string(),
                        Location::Unavailable => "unavailable".to_string(),
                    };
                    json!([
                        v.code,
                        phase(v.phase),
                        loc,
                        v.logical_path.clone().unwrap_or_default()
                    ])
                })
                .collect();
            json!({"ok": false, "err": errs})
        }
        Err(Error::Unsupported(_)) => json!({"ok": false, "other": "unsupported"}),
        Err(Error::Syntax(_)) => json!({"ok": false, "other": "syntax"}),
        Err(Error::Limit(_)) => json!({"ok": false, "other": "limit"}),
        Err(_) => json!({"ok": false, "other": "other"}),
    }
}

fn run(case: &Json) -> Json {
    let root = root();
    let text = case["yaml"].as_str().expect("yaml");
    let path = case["path"].as_str().expect("path");
    let new = value(&case["value"]);
    let edit = edit_scalar(text, &root, path, &a(), &new);
    let orig = case.get("orig").filter(|o| !o.is_null()).map(value);
    let noop = orig
        .as_ref()
        .map(|o| answer(&edit_scalar(text, &root, path, &a(), o)));
    let back = match (&edit, &orig) {
        (Ok(e), Some(o)) => match case.get("back_path") {
            Some(Json::Null) => None, // the new key needs the escapes of the path text: not edited back
            other => {
                let back_path = other.and_then(Json::as_str).unwrap_or(path);
                Some(answer(&edit_scalar(&e.text, &root, back_path, &a(), o)))
            }
        },
        _ => None,
    };
    let (mut ab, mut ba) = (None, None);
    if let Some(other) = case.get("other").filter(|o| !o.is_null()) {
        let (p2, v2) = (
            other["path"].as_str().expect("path"),
            value(&other["value"]),
        );
        let order = |p1: &str, v1: &Node, p2: &str, v2: &Node| {
            edit_scalar(text, &root, p1, &a(), v1)
                .and_then(|r1| edit_scalar(&r1.text, &root, p2, &a(), v2))
        };
        ab = Some(answer(&order(path, &new, p2, &v2)));
        ba = Some(answer(&order(p2, &v2, path, &new)));
    }
    let before = source_span(text, &root, path, &a())
        .ok()
        .map(|s| json!([s.start, s.end, s.style.as_str()]));
    // the parsed trees of the original and of the result (guarantees 3 and 4: the old tree with one value replaced, the forms kept)
    let (tree_before, tree_after) = match &edit {
        Ok(e) => (
            parse(text).ok().map(|n| tree(&n)),
            parse(&e.text).ok().map(|n| tree(&n)),
        ),
        Err(_) => (None, None),
    };
    json!({"id": case["id"], "before": before, "edit": answer(&edit), "tree_before": tree_before, "tree_after": tree_after, "noop": noop, "back": back, "ab": ab, "ba": ba})
}

#[test]
fn dump_edit_outcomes() {
    let (Some(inp), Some(out)) = (
        std::env::var_os("PARITY_EDIT_IN"),
        std::env::var_os("PARITY_EDIT_OUT"),
    ) else {
        return;
    };
    let mut lines = String::new();
    for l in fs::read_to_string(inp)
        .expect("the input file")
        .lines()
        .filter(|l| !l.trim().is_empty())
    {
        let case: Json = serde_json::from_str(l).expect("a JSON line");
        lines.push_str(&serde_json::to_string(&run(&case)).expect("json"));
        lines.push('\n');
    }
    fs::write(out, lines).expect("the output file");
}
