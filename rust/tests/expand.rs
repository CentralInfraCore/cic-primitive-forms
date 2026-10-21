//! The shared document-level expand/collapse cases (`testdata/p1/expand.json`) against `expand` and `collapse`, the
//! Rust peer of `go/expand_test.go`. The schema is `compile.small-config` of `testdata/p1/schema.json`; the expected
//! texts spell `inherit` the way access grammar 1 does (the grammar the corpus pins).

use cic_primitive_forms::{
    bind_grammar, collapse, compile_schema, emit, expand, parse, pinned_grammar_version,
    sha256_hex, Context, DocOrigin, Error, Grammar, Node, SchemaInput, Shape, Value,
};
use serde_json::Value as J;

fn repo_root() -> std::path::PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    )
}

fn grammar() -> Grammar {
    let root = std::env::var_os("CIC_PRIMITIVES_ROOT").expect("CIC_PRIMITIVES_ROOT");
    let bytes =
        std::fs::read(std::path::Path::new(&root).join("schemas/atomic/access.yaml")).unwrap();
    let pins = std::fs::read_to_string(repo_root().join("corpus/pins.yaml")).unwrap();
    let sha = pins
        .lines()
        .skip_while(|l| !l.contains("path: schemas/atomic/access.yaml"))
        .find_map(|l| {
            l.split("sha256: ")
                .nth(1)
                .map(|s| s.trim().trim_end_matches('}').to_string())
        })
        .expect("the pin lists access.yaml");
    let pinned = pinned_grammar_version(&bytes, &sha).expect("the pinned schema verifies");
    bind_grammar(Some(&Value::Int(1)), pinned.as_ref()).expect("the context agrees with the pin")
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
    .map(String::from)
    .to_vec()
}

fn small_config() -> Shape {
    let file: J = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("testdata/p1/schema.json")).unwrap(),
    )
    .unwrap();
    let case = file["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "compile.small-config")
        .expect("compile.small-config");
    let text = case["schema"].as_str().unwrap();
    compile_schema(&SchemaInput {
        bytes: text.as_bytes(),
        sha256: &sha256_hex(text.as_bytes()),
        grammar: file["grammar"].as_str().unwrap(),
        surface: "config_surface",
    })
    .unwrap()
    .root
}

fn parsed(id: &str, text: &str) -> Node {
    parse(text).unwrap_or_else(|e| panic!("{id}: {e:?}"))
}

#[test]
fn the_shared_expand_collapse_cases() {
    let file: J = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("testdata/p1/expand.json")).unwrap(),
    )
    .unwrap();
    let (shape, g) = (small_config(), grammar());
    for c in file["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let doc = parsed(id, c["yaml"].as_str().unwrap());
        let before = doc.clone();
        let result = expand(&doc, &shape, &a(), g, &Context::default());
        if let Some(want) = c.get("error") {
            match result {
                Err(Error::Contract(vs)) => {
                    assert_eq!(vs[0].code, want["code"].as_str().unwrap(), "{id}");
                    assert_eq!(vs[0].logical_path.as_deref(), want["path"].as_str(), "{id}");
                }
                other => panic!("{id}: {other:?}"),
            }
            continue;
        }
        let (expanded, prov) = result.unwrap_or_else(|e| panic!("{id}: {e:?}"));
        assert!(doc.same(&before), "{id}: expand changed its input");
        let want = parsed(id, c["expanded_yaml"].as_str().unwrap());
        assert!(
            expanded.same(&want),
            "{id}: expanded\n got {}\nwant {}",
            emit(&expanded),
            emit(&want)
        );
        let want_prov = c["provenance"].as_object().unwrap();
        assert_eq!(prov.len(), want_prov.len(), "{id}: {prov:?}");
        for (path, authored) in want_prov {
            assert_eq!(
                prov.get(path).map(|o| o.authored_expanded),
                authored.as_bool(),
                "{id}: provenance of {path}"
            );
        }
        // the round trip: what was authored comes back, with the provenance
        let back = collapse(&expanded, &shape, &a(), g, &prov, &Context::default()).unwrap();
        assert!(
            back.same(&doc),
            "{id}: collapse(expand(x)) != x\n got {}\nwant {}",
            emit(&back),
            emit(&doc)
        );
        // without the provenance only the law decides
        if let Some(text) = c
            .get("collapse_without_provenance_yaml")
            .and_then(J::as_str)
        {
            let plain = collapse(
                &expanded,
                &shape,
                &a(),
                g,
                &Default::default(),
                &Context::default(),
            )
            .unwrap();
            assert!(
                plain.same(&parsed(id, text)),
                "{id}: without provenance\n got {}",
                emit(&plain)
            );
        }
        // every position attested by the binding is never shortened
        let all = Context {
            origin: DocOrigin::Materialized,
            attestation: Some("att".into()),
            attested_positions: Some(prov.keys().cloned().collect()),
        };
        let kept = collapse(&expanded, &shape, &a(), g, &prov, &all).unwrap();
        assert!(
            kept.same(&expanded),
            "{id}: a document attested everywhere must stay as it is"
        );
    }
}

fn context_of(j: &J) -> Context {
    Context {
        origin: match j.get("origin").and_then(J::as_str) {
            None | Some("authored") => DocOrigin::Authored,
            Some("materialized") => DocOrigin::Materialized,
            // an origin that is neither is a contradiction the Go peer spells as a string; here it maps to a context
            // that cannot be consistent: authored with an attestation
            Some(_) => {
                return Context {
                    attestation: Some("x".into()),
                    ..Context::default()
                }
            }
        },
        attestation: j.get("attestation").and_then(J::as_str).map(String::from),
        attested_positions: j
            .get("attested_positions")
            .and_then(J::as_array)
            .map(|a| a.iter().map(|p| p.as_str().unwrap().to_string()).collect()),
    }
}

#[test]
fn the_shared_trusted_context() {
    let file: J = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("testdata/p1/expand.json")).unwrap(),
    )
    .unwrap();
    let (shape, g) = (small_config(), grammar());
    let doc = parsed("context", "type: physical\nmtu: 1500\n");
    for c in file["contexts"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let ctx = context_of(&c["context"]);
        let results = [
            expand(&doc, &shape, &a(), g, &ctx).map(|_| ()),
            collapse(&doc, &shape, &a(), g, &Default::default(), &ctx).map(|_| ()),
        ];
        for r in results {
            match (c.get("ok").and_then(J::as_bool), r) {
                (Some(true), Ok(())) => {}
                (None, Err(Error::Contract(vs))) => {
                    assert_eq!(vs[0].code, c["error"].as_str().unwrap(), "{id}");
                    let phase = c.get("phase").and_then(J::as_str).unwrap_or("validate");
                    assert_eq!(vs[0].phase.as_str(), phase, "{id}");
                    assert_eq!(
                        vs[0].logical_path.as_deref(),
                        c.get("path").and_then(J::as_str),
                        "{id}"
                    );
                    assert_eq!(
                        vs[0].location,
                        cic_primitive_forms::Location::NotApplicable,
                        "{id}"
                    );
                }
                (want, got) => panic!("{id}: {want:?} {got:?}"),
            }
        }
    }
    for c in file["materialized"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let ctx = context_of(&c["context"]);
        let d = parsed(id, c["yaml"].as_str().unwrap());
        let (expanded, prov) = expand(&d, &shape, &a(), g, &ctx).unwrap();
        let got = collapse(&expanded, &shape, &a(), g, &prov, &ctx).unwrap();
        let want = parsed(id, c["collapsed_yaml"].as_str().unwrap());
        assert!(
            got.same(&want),
            "{id}: got {}\nwant {}",
            emit(&got),
            emit(&want)
        );
    }
}
