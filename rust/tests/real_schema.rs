//! The pinned real schemas of the registry (`corpus/pins.yaml`, `schemas.*`) against `compile_schema`, the
//! Rust peer of `go/real_schema_test.go`. The files are CC BY-NC-SA 4.0 and are not vendored: they are read
//! from `CIC_REGISTRY_ROOT`, a checkout of cic-schema-registry at the pinned commit, and the SHA-256 of
//! each must equal its pin before anything is compiled. The expectations (`testdata/p1/real-schemas.json`)
//! hold codes and paths only.
//!
//! Two outcomes are pinned down. `network-interface.yaml`: `config_surface` does not compile, because 22
//! fields declare a default without `optional`, which shape.yaml makes invalid; the library refuses the
//! schema and names every field, it does not guess that `optional` was meant. `network-space.yaml`, the
//! provider-neutral VCN/VPC/VNet space: `config_surface` compiles, and an authored VCN intent is validated
//! against it.

use cic_primitive_forms::{
    compile_schema, parse, sha256_hex, validate, CompiledSchema, Error, SchemaInput,
};
use serde_json::Value as J;

/// The repository root; `CIC_FORMS_ROOT` overrides it because the mutation and branch tools run the tests in a
/// copy of the crate (as in `corpus.rs`).
fn repo_root() -> std::path::PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    )
}

fn pair(v: &J) -> (String, String) {
    let a = v.as_array().unwrap();
    (a[0].as_str().unwrap().into(), a[1].as_str().unwrap().into())
}

fn pinned_file(pin: &str) -> (Vec<u8>, String) {
    let root = std::env::var_os("CIC_REGISTRY_ROOT").expect(
        "CIC_REGISTRY_ROOT must point at a checkout of CentralInfraCore/cic-schema-registry at the pinned commit \
         (corpus/pins.yaml, schemas.*)",
    );
    let pins = std::fs::read_to_string(repo_root().join("corpus/pins.yaml")).unwrap();
    // A line scan: the library has no YAML reader that may stand in for the test's own source of truth,
    // and the two values are plain.
    let block = pins
        .split(&format!("  {pin}:\n"))
        .nth(1)
        .expect("pin")
        .to_string();
    let field = |name: &str| -> String {
        block
            .lines()
            .find_map(|l| {
                l.trim()
                    .strip_prefix(&format!("{name}: "))
                    .map(str::to_string)
            })
            .unwrap_or_else(|| panic!("{name} of {pin}"))
    };
    let (path, sha) = (field("path"), field("sha256"));
    let data = std::fs::read(std::path::Path::new(&root).join(&path)).unwrap();
    assert_eq!(sha256_hex(&data), sha, "{path} is not the pinned file");
    let grammar = pins
        .lines()
        .skip_while(|l| !l.starts_with("grammar:"))
        .nth(1)
        .unwrap()
        .trim()
        .trim_end_matches(':')
        .to_string();
    (data, grammar)
}

fn compile_real(pin: &str, surface: &str) -> Result<CompiledSchema, Error> {
    let (data, grammar) = pinned_file(pin);
    compile_schema(&SchemaInput {
        bytes: &data,
        sha256: &sha256_hex(&data),
        grammar: &grammar,
        surface,
    })
}

fn expectations() -> J {
    serde_json::from_str(
        &std::fs::read_to_string(repo_root().join("testdata/p1/real-schemas.json")).unwrap(),
    )
    .unwrap()
}

#[test]
fn network_interface_config_surface_is_refused_with_every_defect_named() {
    let want = &expectations()["refused"];
    let err = compile_real(
        want["pin"].as_str().unwrap(),
        want["surface"].as_str().unwrap(),
    )
    .expect_err("config_surface");
    assert_eq!(err.code(), want["error"].as_str().unwrap());
    let got: Vec<(String, String)> = match &err {
        Error::Schema(p) => p
            .iter()
            .map(|p| (p.code.to_string(), p.path.clone()))
            .collect(),
        other => panic!("{other:?}"),
    };
    let expect: Vec<(String, String)> = want["problems"]
        .as_array()
        .unwrap()
        .iter()
        .map(pair)
        .collect();
    assert_eq!(got, expect);
}

#[test]
fn state_surface_is_not_validated_as_authoring() {
    let want = &expectations()["state_refused"];
    let err =
        compile_real(want["pin"].as_str().unwrap(), "state_surface").expect_err("state_surface");
    assert_eq!(err.code(), want["error"].as_str().unwrap());
}

#[test]
fn network_space_config_surface_compiles_and_validates_a_vcn_intent() {
    let want = &expectations()["accepted"];
    let c = compile_real(
        want["pin"].as_str().unwrap(),
        want["surface"].as_str().unwrap(),
    )
    .unwrap();
    assert_eq!(c.kind, want["kind"].as_str().unwrap());
    assert_eq!(c.namespace, want["namespace"].as_str().unwrap());
    assert_eq!(c.uses_item_choice, want["uses_item_choice"]);
    let contracts: Vec<&str> = want["unevaluated_contracts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap())
        .collect();
    assert_eq!(c.unevaluated_contracts, contracts);
    assert_eq!(
        c.declared_validated_by,
        want["declared_validated_by"].as_str().unwrap()
    );
    let access: Vec<String> = [
        "value",
        "access",
        "modify",
        "inherit",
        "default_injection",
        "conformance",
    ]
    .map(String::from)
    .to_vec();
    for d in want["documents"].as_array().unwrap() {
        let id = d["id"].as_str().unwrap();
        let node = parse(d["yaml"].as_str().unwrap()).unwrap();
        let result = validate(Some(&node), &c.root, &access);
        if d.get("unsupported").and_then(J::as_bool) == Some(true) {
            assert!(matches!(result, Err(Error::Unsupported(_))), "{id}");
            continue;
        }
        let v = result.unwrap();
        let got: Vec<(String, String)> = v
            .violations
            .iter()
            .map(|x| {
                (
                    x.code.to_string(),
                    x.logical_path.clone().unwrap_or_default(),
                )
            })
            .collect();
        let want_v: Vec<(String, String)> = d["violations"]
            .as_array()
            .unwrap()
            .iter()
            .map(pair)
            .collect();
        assert_eq!(got, want_v, "{id}");
        let cands: Vec<String> = d["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s.as_str().unwrap().to_string())
            .collect();
        assert_eq!(v.default_candidates, cands, "{id}");
    }
}

#[test]
fn another_byte_is_a_digest_mismatch_not_a_schema() {
    let pin = expectations()["accepted"]["pin"]
        .as_str()
        .unwrap()
        .to_string();
    let (mut data, grammar) = pinned_file(&pin);
    let sum = sha256_hex(&data);
    data.push(b'\n');
    let err = compile_schema(&SchemaInput {
        bytes: &data,
        sha256: &sum,
        grammar: &grammar,
        surface: "config_surface",
    })
    .expect_err("digest");
    assert_eq!(err.code(), "SCHEMA_DIGEST_MISMATCH");
}
