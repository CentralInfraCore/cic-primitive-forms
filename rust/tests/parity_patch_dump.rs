//! Dumps the outcome of `patch` for the generated inputs of `tools/parity/gen_patch.py`, for the Rust/Go/model differential of ADR 0013 (P4 step P4-3).
//! `PARITY_PATCH_IN` is a JSON-lines file (the first line is the schema in the tree notation of the corpus), `PARITY_PATCH_OUT` receives one JSON line per input.
//! Without the two variables this test does nothing. The Go peer is `go/parity_patch_dump_test.go`; `tools/parity/compare_patch.py` compares and checks the
//! promises of ADR 0013 on its own.
//!
//! For each input: the diff of A to B (`diff`), the patch of A by it (`patch`: the text, the replaced scalars, their spans before and after, or the refusal), the
//! patch by the empty diff (`empty`), the patch of the result by the same diff (`replay`), the diff of the result to B in the scope of the first (`rt`), and the
//! patch by the diff with the first previous value altered (`tamper`). An answer is `{"ok": ...}`, `{"err": [code, phase, location, path, operand]}` or
//! `{"other": kind}`.

use cic_primitive_forms::{
    bind_grammar, diff, parse, patch, Case, Context, DiffContext, DiffView, Elements, Error,
    Grammar, Location, Node, PatchDiff, PatchEvent, Presence, ScalarType, Shape, ShapeType, Value,
};
use serde_json::{json, Value as Json};
use std::fs;

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

fn scalar_type(name: &str) -> ScalarType {
    match name {
        "integer" => ScalarType::Integer,
        "boolean" => ScalarType::Boolean,
        _ => ScalarType::String,
    }
}

fn shape(shape_type: ShapeType, mandatory: bool, nullable: bool) -> Shape {
    Shape {
        shape_type,
        presence: if mandatory {
            Presence::Mandatory
        } else {
            Presence::Optional
        },
        nullable,
        has_default: false,
        default: None,
    }
}

fn key_of(v: &Json) -> Vec<String> {
    v.as_array()
        .expect("key")
        .iter()
        .map(|k| k.as_str().expect("key").to_string())
        .collect()
}

/// The tree notation of the corpus as a Shape. Only key fields are mandatory; opaque positions are nullable, as in the runners of corpus/diff.
fn dump_shape(spec: &Json, mandatory: bool) -> Shape {
    if spec.as_str() == Some("opaque") {
        return shape(ShapeType::Opaque, mandatory, true);
    }
    if let Some(s) = spec.get("scalar") {
        return shape(
            ShapeType::Scalar(scalar_type(s.as_str().expect("scalar"))),
            mandatory,
            false,
        );
    }
    if let Some(s) = spec.get("set") {
        return shape(
            ShapeType::Set {
                item: scalar_type(s.as_str().expect("set")),
                min_items: None,
            },
            mandatory,
            false,
        );
    }
    if let Some(c) = spec.get("composite") {
        return shape(ShapeType::Composite(dump_fields(c, &[])), mandatory, false);
    }
    let list = &spec["list"];
    let elements = if let Some(choice) = list.get("choice") {
        Elements {
            choice: true,
            cases: choice
                .as_array()
                .expect("choice")
                .iter()
                .map(|cs| {
                    let key = key_of(&cs["key"]);
                    Case {
                        name: cs["name"].as_str().expect("name").to_string(),
                        fields: dump_fields(&cs["fields"], &key),
                        key,
                    }
                })
                .collect(),
        }
    } else {
        let key = key_of(&list["key"]);
        Elements {
            choice: false,
            cases: vec![Case {
                name: String::new(),
                fields: dump_fields(&list["fields"], &key),
                key,
            }],
        }
    };
    shape(
        ShapeType::DeclaredList {
            min_items: None,
            elements,
        },
        mandatory,
        false,
    )
}

fn dump_fields(fields: &Json, key: &[String]) -> Vec<(String, Shape)> {
    let mut out: Vec<(String, Shape)> = fields
        .as_object()
        .expect("fields")
        .iter()
        .map(|(n, s)| (n.clone(), dump_shape(s, key.contains(n))))
        .collect();
    out.sort_by(|x, y| x.0.cmp(&y.0));
    out
}

/// A plain JSON tree as a position-less Node; `{"__u": text}` is an unsupported number.
fn dump_doc(v: &Json) -> Node {
    match v {
        Json::Null => Node::new(Value::Null),
        Json::Bool(b) => Node::new(Value::Bool(*b)),
        Json::Number(n) => n.as_i64().map_or_else(
            || Node::new(Value::Unsupported(n.to_string())),
            |i| Node::new(Value::Int(i)),
        ),
        Json::String(s) => Node::new(Value::Str(s.clone())),
        Json::Array(items) => Node::new(Value::Seq(items.iter().map(dump_doc).collect())),
        Json::Object(m) => match (m.len(), m.get("__u")) {
            (1, Some(u)) => Node::new(Value::Unsupported(u.as_str().expect("text").to_string())),
            _ => Node::new(Value::Map(
                m.iter()
                    .map(|(k, v)| cic_primitive_forms::Entry {
                        key: k.clone(),
                        key_pos: None,
                        value: dump_doc(v),
                    })
                    .collect(),
            )),
        },
    }
}

fn grammar() -> Grammar {
    let one = Value::Int(1);
    bind_grammar(Some(&one), Some(&one)).expect("grammar 1")
}

fn dump_diff(x: &Node, y: &Node, ctx_b: &str, root: &Shape, scope: Option<&[String]>) -> Json {
    let view = |doc, schema: &str| DiffView {
        doc,
        context: DiffContext {
            schema: schema.to_string(),
            ..DiffContext::default()
        },
        trust: Context::default(),
    };
    match diff(&view(x, ""), &view(y, ctx_b), root, &a(), grammar(), scope) {
        Ok(r) => json!({"ok": r.canonical()}),
        Err(Error::Contract(vs)) => {
            let v = &vs[0];
            let loc = match v.location {
                Location::At(p) => format!("{}:{}:{}", p.line, p.column, p.offset),
                Location::NotApplicable => "not_applicable".to_string(),
                Location::Unavailable => "unavailable".to_string(),
            };
            json!({"err": [v.code, v.phase.as_str(), loc, v.logical_path.clone().unwrap_or_default(), v.operand.unwrap_or("")]})
        }
        Err(Error::Unsupported(_)) => json!({"other": "1"}),
        Err(_) => json!({"other": "x"}),
    }
}

fn patch_err(e: &Error) -> Json {
    match e {
        Error::Contract(vs) => {
            let v = &vs[0];
            let loc = match v.location {
                Location::At(p) => format!("{}:{}:{}", p.line, p.column, p.offset),
                Location::NotApplicable => "not_applicable".to_string(),
                Location::Unavailable => "unavailable".to_string(),
            };
            json!({"err": [v.code, v.phase.as_str(), loc, v.logical_path.clone().unwrap_or_default(), v.operand.unwrap_or("")]})
        }
        _ => json!({"other": "1"}),
    }
}

fn style(s: &cic_primitive_forms::SourceSpan) -> Json {
    json!([s.start, s.end, s.style.as_str()])
}

#[test]
fn dump_patch_outcomes() {
    let (Some(inp), Some(out)) = (
        std::env::var_os("PARITY_PATCH_IN"),
        std::env::var_os("PARITY_PATCH_OUT"),
    ) else {
        return;
    };
    let text_in = fs::read_to_string(inp).expect("the input file");
    let mut lines = text_in.lines().filter(|l| !l.trim().is_empty());
    let head: Json = serde_json::from_str(lines.next().expect("the tree line")).expect("json");
    let root = shape(
        ShapeType::Composite(dump_fields(&head["tree"], &[])),
        true,
        false,
    );
    let ctx = DiffContext::default();
    let mut dumped = String::new();
    for l in lines {
        let case: Json = serde_json::from_str(l).expect("a JSON line");
        let text = case["yaml"].as_str().expect("yaml");
        let scope: Option<Vec<String>> = case["scope"].as_array().map(|s| {
            s.iter()
                .map(|p| p.as_str().expect("scope path").to_string())
                .collect()
        });
        let b = dump_doc(&case["b"]);
        let mut row = serde_json::Map::new();
        row.insert("id".into(), case["id"].clone());
        match parse(text) {
            Err(e) => {
                row.insert("diff".into(), patch_err(&e));
                row.insert("patch".into(), patch_err(&e));
            }
            Ok(doc) => {
                row.insert(
                    "diff".into(),
                    dump_diff(&doc, &b, "", &root, scope.as_deref()),
                );
                let view = |d| DiffView {
                    doc: d,
                    context: DiffContext::default(),
                    trust: Context::default(),
                };
                match diff(
                    &view(&doc),
                    &view(&b),
                    &root,
                    &a(),
                    grammar(),
                    scope.as_deref(),
                ) {
                    Err(_) => {
                        // no events to apply: patch a fixed one, which an invalid A must refuse by its own earlier phase
                        let one = PatchDiff {
                            version: 1,
                            changes: vec![PatchEvent {
                                kind: cic_primitive_forms::EventKind::ValueChange,
                                path: "$.name".into(),
                                previous: Some(Node::new(Value::Str("x".into()))),
                                next: Some(Node::new(Value::Str("y".into()))),
                            }],
                        };
                        row.insert(
                            "patch".into(),
                            match patch(text, &root, &a(), grammar(), &ctx, &ctx, &one) {
                                Err(e) => patch_err(&e),
                                Ok(_) => json!({"skipped": true}),
                            },
                        );
                    }
                    Ok(res) => {
                        let events: Vec<PatchEvent> =
                            res.changes.iter().map(PatchEvent::from).collect();
                        let pd = PatchDiff {
                            version: 1,
                            changes: events.clone(),
                        };
                        if let Some(first) = events.first() {
                            // the first event with another kind and the same two values: the patch must still refuse the whole diff
                            let mut swapped = events.clone();
                            swapped[0] = PatchEvent {
                                kind: cic_primitive_forms::EventKind::PolicyDeclarationChange,
                                ..first.clone()
                            };
                            row.insert(
                                "kindswap".into(),
                                match patch(
                                    text,
                                    &root,
                                    &a(),
                                    grammar(),
                                    &ctx,
                                    &ctx,
                                    &PatchDiff {
                                        version: 1,
                                        changes: swapped,
                                    },
                                ) {
                                    Ok(_) => json!({"ok": ""}),
                                    Err(e) => patch_err(&e),
                                },
                            );
                        }
                        match patch(text, &root, &a(), grammar(), &ctx, &ctx, &pd) {
                            Err(e) => {
                                row.insert("patch".into(), patch_err(&e));
                            }
                            Ok(p) => {
                                let spans: Vec<Json> = p
                                    .spans
                                    .iter()
                                    .map(|s| json!([s.path, style(&s.before), style(&s.after)]))
                                    .collect();
                                row.insert("patch".into(), json!({"ok": {"text": p.text, "replaced": p.replaced, "spans": spans}}));
                                row.insert(
                                    "replay".into(),
                                    match patch(&p.text, &root, &a(), grammar(), &ctx, &ctx, &pd) {
                                        Ok(r2) => json!({"ok": r2.text}),
                                        Err(e) => patch_err(&e),
                                    },
                                );
                                if let Ok(doc2) = parse(&p.text) {
                                    row.insert(
                                        "rt".into(),
                                        dump_diff(&doc2, &b, "", &root, scope.as_deref()),
                                    );
                                }
                                if let Some(first) = events.first() {
                                    let mut tampered = events.clone();
                                    tampered[0] = PatchEvent {
                                        previous: Some(Node::new(Value::Str("tampered".into()))),
                                        ..first.clone()
                                    };
                                    row.insert(
                                        "tamper".into(),
                                        match patch(
                                            text,
                                            &root,
                                            &a(),
                                            grammar(),
                                            &ctx,
                                            &ctx,
                                            &PatchDiff {
                                                version: 1,
                                                changes: tampered,
                                            },
                                        ) {
                                            Ok(r3) => json!({"ok": r3.text}),
                                            Err(e) => patch_err(&e),
                                        },
                                    );
                                }
                            }
                        }
                        row.insert(
                            "empty".into(),
                            match patch(
                                text,
                                &root,
                                &a(),
                                grammar(),
                                &ctx,
                                &ctx,
                                &PatchDiff {
                                    version: 1,
                                    changes: vec![],
                                },
                            ) {
                                Ok(r4) => json!({"ok": r4.text}),
                                Err(e) => patch_err(&e),
                            },
                        );
                    }
                }
            }
        }
        dumped.push_str(&serde_json::to_string(&Json::Object(row)).expect("json"));
        dumped.push('\n');
    }
    fs::write(out, dumped).expect("the output file");
}
