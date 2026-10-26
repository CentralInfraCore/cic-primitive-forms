//! Dumps the outcome of `diff` for the generated inputs of `tools/parity/gen_diff.py`, for the Rust/Go/model differential of ADR 0008 (P4 step D3b).
//! `PARITY_DIFF_IN` is a JSON-lines file (the first line is the schema in the tree notation of the corpus), `PARITY_DIFF_OUT` receives one JSON line
//! per input. Without the two variables this test does nothing. The Go peer is `go/parity_diff_dump_test.go`; `tools/parity/compare_diff.py` compares.
//!
//! For each input four diffs: A to B (`ab`), B to A (`ba`), A to A (`aa`) and the same pair with every list and set in another order (`abs`), all in the
//! scope of the input; `ctx_b` makes the context of B a different one. An answer is `{"ok": canonical bytes}`, `{"err": [code, phase, location, path,
//! operand]}` or `{"other": kind}` for an error outside the contract.

use cic_primitive_forms::{
    bind_grammar, diff, Case, Context, DiffContext, DiffView, Elements, Error, Grammar, Location,
    Node, Presence, ScalarType, Shape, ShapeType, Value,
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

#[test]
fn dump_diff_outcomes() {
    let (Some(inp), Some(out)) = (
        std::env::var_os("PARITY_DIFF_IN"),
        std::env::var_os("PARITY_DIFF_OUT"),
    ) else {
        return;
    };
    let text = fs::read_to_string(inp).expect("the input file");
    let mut lines = text.lines().filter(|l| !l.trim().is_empty());
    let head: Json = serde_json::from_str(lines.next().expect("the tree line")).expect("json");
    let root = shape(
        ShapeType::Composite(dump_fields(&head["tree"], &[])),
        true,
        false,
    );
    let mut dumped = String::new();
    for l in lines {
        let case: Json = serde_json::from_str(l).expect("a JSON line");
        let scope: Option<Vec<String>> = case["scope"].as_array().map(|s| {
            s.iter()
                .map(|p| p.as_str().expect("scope path").to_string())
                .collect()
        });
        let ctx_b = if case["ctx_b"].as_i64() == Some(1) {
            "other"
        } else {
            ""
        };
        let (x, y) = (dump_doc(&case["a"]), dump_doc(&case["b"]));
        let (x2, y2) = (dump_doc(&case["a2"]), dump_doc(&case["b2"]));
        let row = json!({
            "id": case["id"],
            "ab": dump_diff(&x, &y, ctx_b, &root, scope.as_deref()),
            "ba": dump_diff(&y, &x, ctx_b, &root, scope.as_deref()),
            "aa": dump_diff(&x, &x, "", &root, scope.as_deref()),
            "abs": dump_diff(&x2, &y2, ctx_b, &root, scope.as_deref()),
        });
        dumped.push_str(&serde_json::to_string(&row).expect("json"));
        dumped.push('\n');
    }
    fs::write(out, dumped).expect("the output file");
}
