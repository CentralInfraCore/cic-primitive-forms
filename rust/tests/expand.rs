//! The shared document-level expand/collapse cases (`testdata/p1/expand.json`) against `expand` and `collapse`, the
//! Rust peer of `go/expand_test.go`. The schema is `compile.small-config` of `testdata/p1/schema.json`; the expected
//! texts spell `inherit` the way access grammar 1 does (the grammar the corpus pins).

use cic_primitive_forms::{
    bind_grammar, collapse, compile_schema, emit, expand, parse, pinned_grammar_version,
    sha256_hex, Error, Grammar, Node, SchemaInput, Shape, Value, BASIS_BINDING,
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
        let result = expand(&doc, &shape, &a(), g);
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
        let back = collapse(&expanded, &shape, &a(), g, &prov, None).unwrap();
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
            let plain = collapse(&expanded, &shape, &a(), g, &Default::default(), None).unwrap();
            assert!(
                plain.same(&parsed(id, text)),
                "{id}: without provenance\n got {}",
                emit(&plain)
            );
        }
        // a binding-attested position is never shortened
        let attested = |_: &str| BASIS_BINDING.to_string();
        let kept = collapse(&expanded, &shape, &a(), g, &prov, Some(&attested)).unwrap();
        assert!(
            kept.same(&expanded),
            "{id}: a binding_attested document must stay as it is"
        );
    }
}
