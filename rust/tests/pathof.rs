//! `path_of` on addresses the corpus does not spell out (`testdata/p1/pathof.json`): the ends of an address, an element that is
//! not a record, a list field written expanded, an index past the end. The Go peer is `TestSharedPathOfCases`.

use cic_primitive_forms::{
    compile_schema, parse, path_of, sha256_hex, Error, SchemaInput, Shape, Step,
};
use serde_json::Value as J;

fn repo_root() -> std::path::PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    )
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

#[test]
fn the_shared_path_of_cases() {
    let file: J = serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("testdata/p1/pathof.json")).unwrap(),
    )
    .unwrap();
    let a: Vec<String> = [
        "value",
        "access",
        "modify",
        "inherit",
        "default_injection",
        "conformance",
    ]
    .map(String::from)
    .to_vec();
    let shape = small_config();
    for c in file["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let at: Vec<Step> = c["at"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| match s.as_i64() {
                // a negative index can never address an element
                Some(n) => Step::Index(usize::try_from(n).unwrap_or(usize::MAX)),
                None => Step::Field(s.as_str().unwrap().to_string()),
            })
            .collect();
        let doc = parse(c["yaml"].as_str().unwrap()).unwrap();
        let got = path_of(&doc, &shape, &a, &at);
        if c.get("unsupported").and_then(J::as_bool) == Some(true) {
            assert!(matches!(got, Err(Error::Unsupported(_))), "{id}: {got:?}");
        } else if let Some(code) = c.get("error").and_then(J::as_str) {
            match got {
                Err(Error::Contract(vs)) => {
                    assert_eq!(vs[0].code, code, "{id}");
                    assert_eq!(vs[0].phase.as_str(), "path_of", "{id}");
                    assert_eq!(
                        vs[0].logical_path.as_deref(),
                        c["error_path"].as_str(),
                        "{id}"
                    );
                }
                other => panic!("{id}: {other:?}"),
            }
        } else {
            assert_eq!(got.as_deref(), Ok(c["path"].as_str().unwrap()), "{id}");
        }
    }
}
