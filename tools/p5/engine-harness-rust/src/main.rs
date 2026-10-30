//! Checks the shared flat-materialization cases against the engine (Rust): for each accepted case the engine's `MaterializedObject::try_new` accepts the
//! fields of the expected projection under the expected key set, and `materialized_object_digest` gives the expected `output_digest`. The expected values are
//! computed by `tools/p5/materialize_cases.py`, so a green run means the engine and that script agree, and the library's own tests (which compare against the
//! same file) make the library agree with both.
//!
//! The negative checks are what make a green run mean something: a key set with one key missing, or one key more, must be refused with `E_INCOMPLETE_OBJECT`,
//! and a changed value, capability or provenance must change the digest.

use std::collections::{BTreeMap, BTreeSet};

use cic_primitive_engine::reader::Value;
use cic_primitive_engine::{
    code, materialized_object_digest, Capability, FieldEvidence, IntentEvidence, MaterializedField,
    MaterializedObject,
};
use serde_json::Value as J;

fn value(j: &J) -> Value {
    match j {
        J::Null => Value::Null,
        J::Bool(b) => Value::Bool(*b),
        J::Number(n) => Value::Int(n.as_i64().expect("an i64")),
        J::String(s) => Value::Str(s.clone()),
        other => panic!("a value outside the flat range: {other}"),
    }
}

fn field(capability: &str, provenance: &str, v: &J) -> MaterializedField {
    let capability = match capability {
        "implemented" => Capability::Implemented,
        "not_implemented" => Capability::NotImplemented,
        "deprecated" => Capability::Deprecated,
        other => panic!("capability {other}"),
    };
    let v = value(v);
    let evidence = match provenance {
        "authored" => IntentEvidence::Authored(Some(v)),
        "schema_default" => IntentEvidence::SchemaDefault(v),
        other => panic!("provenance {other}"),
    };
    MaterializedField {
        capability,
        evidence: FieldEvidence::Intent(evidence),
    }
}

fn fields_of(projection: &J) -> BTreeMap<String, MaterializedField> {
    projection["fields"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| {
            (
                f["key"].as_str().unwrap().to_string(),
                field(
                    f["capability"].as_str().unwrap(),
                    f["provenance"].as_str().unwrap(),
                    &f["value"],
                ),
            )
        })
        .collect()
}

fn digest_of(fields: BTreeMap<String, MaterializedField>, keys: &BTreeSet<String>) -> String {
    let obj = MaterializedObject::try_new(fields, keys).expect("complete");
    materialized_object_digest(&obj).expect("digest")
}

/// The generated differential (tools/parity/gen_materialize.py): for every case a language accepted, the engine builds its own object from the projection that language
/// wrote, under the key set the GENERATOR declared (not the one the language reports), and its digest must be the one the language gave. A key that the language lost
/// from its fields would make `try_new` refuse; a key it lost from its own expected keys is seen by the comparison, and here by the key set being the generator's.
fn differential(input: &str, output: &str) {
    let answers: BTreeMap<u64, J> = std::fs::read_to_string(output)
        .unwrap()
        .lines()
        .map(|l| {
            let a: J = serde_json::from_str(l).unwrap();
            (a["id"].as_u64().unwrap(), a["r"].clone())
        })
        .collect();
    let mut checked = 0;
    for line in std::fs::read_to_string(input).unwrap().lines() {
        let case: J = serde_json::from_str(line).unwrap();
        let id = case["id"].as_u64().unwrap();
        let r = &answers[&id];
        if r[0] != "ok" {
            continue;
        }
        let projection: J = serde_json::from_str(r[2].as_str().unwrap()).unwrap();
        let keys: BTreeSet<String> = case["keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|k| k.as_str().unwrap().to_string())
            .collect();
        let got = digest_of(fields_of(&projection), &keys);
        assert_eq!(
            got,
            r[3].as_str().unwrap(),
            "#{id}: the engine's digest of what the library wrote"
        );
        let mut fewer = keys.clone();
        if let Some(dropped) = fewer.iter().next().cloned() {
            fewer.remove(&dropped);
            let err = MaterializedObject::try_new(fields_of(&projection), &fewer)
                .expect_err("a key less");
            assert_eq!(err.code, code::INCOMPLETE_OBJECT, "#{id}: one key less");
        }
        checked += 1;
    }
    assert!(checked > 0, "no accepted case was checked");
    println!("engine (rust): {checked} generated accepted cases agree with the engine's try_new and digest");
}

fn main() {
    let mut args = std::env::args().skip(1);
    let first = args
        .next()
        .expect("usage: engine-harness-rust <forms root> | differential <in> <out>");
    if first == "differential" {
        let input = args.next().expect("the generated inputs");
        let output = args.next().expect("the dump of one language");
        differential(&input, &output);
        return;
    }
    let root = first;
    let text = std::fs::read_to_string(format!("{root}/testdata/p5/materialize.json")).unwrap();
    let file: J = serde_json::from_str(&text).unwrap();
    let mut checked = 0;
    for c in file["cases"].as_array().unwrap() {
        let expect = &c["expect"];
        if expect.get("refused").is_some() {
            continue;
        }
        let id = c["id"].as_str().unwrap();
        let projection: J = serde_json::from_str(expect["projection"].as_str().unwrap()).unwrap();
        let keys: BTreeSet<String> = expect["expected_keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|k| k.as_str().unwrap().to_string())
            .collect();
        let want = expect["output_digest"].as_str().unwrap();
        let got = digest_of(fields_of(&projection), &keys);
        assert_eq!(got, want, "{id}: the engine's digest");

        let mut more = keys.clone();
        more.insert("~extra".to_string());
        let err = MaterializedObject::try_new(fields_of(&projection), &more).expect_err(id);
        assert_eq!(err.code, code::INCOMPLETE_OBJECT, "{id}: one key more");
        let mut fewer = keys.clone();
        let dropped = fewer.iter().next().unwrap().clone();
        fewer.remove(&dropped);
        let err = MaterializedObject::try_new(fields_of(&projection), &fewer).expect_err(id);
        assert_eq!(err.code, code::INCOMPLETE_OBJECT, "{id}: one key less");

        let mut tampered = projection.clone();
        tampered["fields"][0]["value"] = J::String("tampered".into());
        assert_ne!(digest_of(fields_of(&tampered), &keys), want, "{id}: value");
        let mut tampered = projection.clone();
        let cap = tampered["fields"][0]["capability"]
            .as_str()
            .unwrap()
            .to_string();
        tampered["fields"][0]["capability"] = J::String(
            if cap == "deprecated" {
                "implemented"
            } else {
                "deprecated"
            }
            .into(),
        );
        assert_ne!(
            digest_of(fields_of(&tampered), &keys),
            want,
            "{id}: capability"
        );
        let mut tampered = projection.clone();
        let prov = tampered["fields"][0]["provenance"]
            .as_str()
            .unwrap()
            .to_string();
        tampered["fields"][0]["provenance"] = J::String(
            if prov == "authored" {
                "schema_default"
            } else {
                "authored"
            }
            .into(),
        );
        assert_ne!(
            digest_of(fields_of(&tampered), &keys),
            want,
            "{id}: provenance"
        );
        checked += 1;
    }
    assert_eq!(checked, 6, "the accepted cases");
    println!("engine (rust): {checked} accepted cases agree with the engine's try_new and digest");
}
