//! Checks the shared flat-materialization cases against the engine (Rust): for each accepted case the engine's `MaterializedObject::try_new` accepts the
//! fields of the expected projection under the expected key set, and `materialized_object_digest` gives the expected `output_digest`. The expected values are
//! computed by `tools/p5/materialize_cases.py`, so a green run means the engine and that script agree, and the library's own tests (which compare against the
//! same file) make the library agree with both.
//!
//! The negative checks are what make a green run mean something: a key set with one key missing, or one key more, must be refused with `E_INCOMPLETE_OBJECT`,
//! and a changed value, capability or provenance must change the digest.

use std::collections::{BTreeMap, BTreeSet};

use cic_primitive_engine::reader::{Map, Value};
use cic_primitive_engine::{
    code, materialized_object_digest, verify_receipt, Capability, FieldEvidence, IntentEvidence,
    MaterializedField, MaterializedObject, PathBinding, Receipt,
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

/// A JSON value as the engine's reader value (the receipt document and the authored input are plain JSON).
fn to_value(j: &J) -> Value {
    match j {
        J::Object(m) => Value::Map(
            Map::from_pairs(m.iter().map(|(k, v)| (k.clone(), to_value(v))))
                .expect("no repeated key"),
        ),
        J::Array(a) => Value::Seq(a.iter().map(to_value).collect()),
        other => value(other),
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

/// The generated receipt differential (tools/parity/compare_receipt.py): the engine is the third reader of the RECEIPTS each language wrote for the generated cases. For
/// every accepted case the engine builds its own object from the projection the language wrote, under the key set the GENERATOR declared, and `verify_receipt` has to
/// accept the receipt, the authored input and the binding the language gave, with the engine identity of that language. Then the receipt is altered in ways that the
/// engine's contract refuses, and each has to be refused with the code and the rule written here: the input digest, the output digest, the schema version, the evidence
/// value, an evidence entry removed, and a path with no binding. The comparison with the other language and with the Python reference is compare_receipt.py.
fn receipt_differential(input: &str, output: &str, identity: &str) {
    let answers: BTreeMap<u64, J> = std::fs::read_to_string(output)
        .unwrap()
        .lines()
        .map(|l| {
            let a: J = serde_json::from_str(l).unwrap();
            (a["id"].as_u64().unwrap(), a["r"].clone())
        })
        .collect();
    let zero = J::String(format!("sha256:{}", "0".repeat(64)));
    let (mut accepted, mut refused) = (0, 0);
    for line in std::fs::read_to_string(input).unwrap().lines() {
        let case: J = serde_json::from_str(line).unwrap();
        let id = case["id"].as_u64().unwrap();
        let r = &answers[&id];
        if r[0] != "ok" {
            continue;
        }
        let projection: J = serde_json::from_str(r[5].as_str().unwrap()).unwrap();
        let keys: BTreeSet<String> = case["keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|k| k.as_str().unwrap().to_string())
            .collect();
        let receipt: J = serde_json::from_str(r[1].as_str().unwrap()).unwrap();
        assert_eq!(
            receipt["materialization"]["engine_identity"], identity,
            "#{id}: the engine identity of the language"
        );
        let authored: J = serde_json::from_str(r[2].as_str().unwrap()).unwrap();
        let binding: BTreeMap<String, String> = r[3]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        let verify = |receipt: &J,
                      binding: &BTreeMap<String, String>|
         -> Result<(), (&'static str, &'static str)> {
            let object =
                MaterializedObject::try_new(fields_of(&projection), &keys).expect("complete");
            let receipt = Receipt::from_value(&to_value(receipt)).map_err(|e| (e.code, e.rule))?;
            let binding: PathBinding = binding.clone();
            verify_receipt(&receipt, &object, &to_value(&authored), &binding)
                .map_err(|e| (e.code, e.rule))
        };
        assert_eq!(
            verify(&receipt, &binding),
            Ok(()),
            "#{id}: the engine verifier on the receipt the language wrote"
        );
        accepted += 1;
        let mut expect = |what: &str,
                          got: Result<(), (&'static str, &'static str)>,
                          want: (&'static str, &'static str)| {
            assert_eq!(got, Err(want), "#{id}: {what}");
            refused += 1;
        };
        let mut r1 = receipt.clone();
        body(&mut r1)["input_digest"] = zero.clone();
        expect(
            "a changed input digest",
            verify(&r1, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-INPUT"),
        );
        let mut r1 = receipt.clone();
        body(&mut r1)["output_digest"] = zero.clone();
        expect(
            "a changed output digest",
            verify(&r1, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-OUTPUT"),
        );
        let mut r1 = receipt.clone();
        body(&mut r1)["schema_version"] = J::String(String::new());
        expect(
            "an empty schema version",
            verify(&r1, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-SHAPE"),
        );
        if receipt["materialization"]["applied_defaults"]
            .as_array()
            .unwrap()
            .is_empty()
        {
            continue;
        }
        let mut r1 = receipt.clone();
        body(&mut r1)["applied_defaults"][0]["value_digest"] = zero.clone();
        expect(
            "a changed value digest",
            verify(&r1, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-EVIDENCE-VALUE"),
        );
        let mut r1 = receipt.clone();
        body(&mut r1)["applied_defaults"]
            .as_array_mut()
            .unwrap()
            .remove(0);
        expect(
            "an evidence entry removed",
            verify(&r1, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-EVIDENCE-MEMBERSHIP"),
        );
        let mut b = binding.clone();
        let first = b.keys().next().cloned().expect("a binding");
        b.remove(&first);
        expect(
            "a path with no binding",
            verify(&receipt, &b),
            (code::RECEIPT_UNBOUND, "R-RECEIPT-PATH-BINDING"),
        );
    }
    assert!(accepted > 0, "no accepted receipt was checked");
    println!("engine (rust): {accepted} generated receipts of {identity} accepted by verify_receipt, {refused} altered ones refused with the engine's code and rule");
}

/// The engine's verifier is the gate of the receipts of this library (P5-3): for every accepted flat case the receipt in `testdata/p5/receipt.json`, with the
/// identity of the Rust implementation in its place, is accepted by `verify_receipt` against the object built from the shared projection and the authored
/// input; and each deliberate alteration of it, of the object, of the input or of the binding is refused with the code and the rule the engine gives it.
/// The expected refusals are written here, apart from the library, from the engine's contract (docs/RECEIPT-SCHEMA.md, docs/RECEIPT-VERIFIER.md).
fn body(r: &mut J) -> &mut J {
    &mut r["materialization"]
}

fn receipts(root: &str) {
    let materialize: J = serde_json::from_str(
        &std::fs::read_to_string(format!("{root}/testdata/p5/materialize.json")).unwrap(),
    )
    .unwrap();
    let file: J = serde_json::from_str(
        &std::fs::read_to_string(format!("{root}/testdata/p5/receipt.json")).unwrap(),
    )
    .unwrap();
    let token = file["engine_identity_token"].as_str().unwrap();
    type Outcome = Result<(), (&'static str, &'static str)>;
    let verify = |receipt: &J,
                  authored: &J,
                  projection: &J,
                  keys: &BTreeSet<String>,
                  binding: &BTreeMap<String, String>|
     -> Outcome {
        let object = MaterializedObject::try_new(fields_of(projection), keys).expect("complete");
        let receipt = Receipt::from_value(&to_value(receipt)).map_err(|e| (e.code, e.rule))?;
        let binding: PathBinding = binding.clone();
        verify_receipt(&receipt, &object, &to_value(authored), &binding)
            .map_err(|e| (e.code, e.rule))
    };
    let (mut accepted, mut refused) = (0, 0);
    for c in file["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let mc = materialize["cases"]
            .as_array()
            .unwrap()
            .iter()
            .find(|x| x["id"] == c["id"])
            .unwrap();
        let projection: J =
            serde_json::from_str(mc["expect"]["projection"].as_str().unwrap()).unwrap();
        let keys: BTreeSet<String> = mc["expect"]["expected_keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|k| k.as_str().unwrap().to_string())
            .collect();
        let document = c["document"]
            .as_str()
            .unwrap()
            .replace(token, "cic-primitive-forms-rust");
        let receipt: J = serde_json::from_str(&document).unwrap();
        let authored: J = serde_json::from_str(c["authored_input"].as_str().unwrap()).unwrap();
        let binding: BTreeMap<String, String> = c["binding"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        assert_eq!(
            verify(&receipt, &authored, &projection, &keys, &binding),
            Ok(()),
            "{id}: the engine verifier on the receipt of the library"
        );
        accepted += 1;

        let mut expect = |what: &str, got: Outcome, want: (&'static str, &'static str)| {
            assert_eq!(got, Err(want), "{id}: {what}");
            refused += 1;
        };
        let mut r = receipt.clone();
        body(&mut r)["receipt_schema_version"] = J::from(2);
        expect(
            "a version the verifier does not support",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-VERSION"),
        );
        let mut r = receipt.clone();
        body(&mut r)["input_digest"] = J::String(format!("sha256:{}", "0".repeat(64)));
        expect(
            "a changed input digest",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-INPUT"),
        );
        let mut a = authored.clone();
        a["type"] = J::String("another".into());
        expect(
            "another authored input",
            verify(&receipt, &a, &projection, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-INPUT"),
        );
        let mut r = receipt.clone();
        body(&mut r)["output_digest"] = J::String(format!("sha256:{}", "0".repeat(64)));
        expect(
            "a changed output digest",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-OUTPUT"),
        );
        let mut p = projection.clone();
        p["fields"][0]["value"] = J::String("tampered".into());
        expect(
            "another object",
            verify(&receipt, &authored, &p, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-OUTPUT"),
        );
        let mut r = receipt.clone();
        body(&mut r)["schema_version"] = J::String(String::new());
        expect(
            "an empty schema version",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-SHAPE"),
        );
        let mut r = receipt.clone();
        body(&mut r)["schema_digest"] = J::String("sha256:ABC".into());
        expect(
            "a schema digest of a wrong form",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-DIGEST-FORMAT"),
        );
        let mut r = receipt.clone();
        body(&mut r)
            .as_object_mut()
            .unwrap()
            .remove("engine_version");
        expect(
            "a missing field",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-SHAPE"),
        );

        let n = receipt["materialization"]["applied_defaults"]
            .as_array()
            .unwrap()
            .len();
        if n == 0 {
            // a receipt that lists nothing for an object that has defaulted fields cannot be made from this case; see the cases with defaults
            continue;
        }
        let mut r = receipt.clone();
        body(&mut r)["applied_defaults"][0]["value_digest"] =
            J::String(format!("sha256:{}", "0".repeat(64)));
        expect(
            "a changed value digest",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-EVIDENCE-VALUE"),
        );
        let mut r = receipt.clone();
        body(&mut r)["applied_defaults"][0]["rule"] = J::String("other".into());
        expect(
            "a rule that is not the literal",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-EVIDENCE-FORM"),
        );
        let mut r = receipt.clone();
        body(&mut r)["applied_defaults"]
            .as_array_mut()
            .unwrap()
            .remove(0);
        expect(
            "an evidence entry removed",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-EVIDENCE-MEMBERSHIP"),
        );
        let mut r = receipt.clone();
        let first = body(&mut r)["applied_defaults"][0].clone();
        body(&mut r)["applied_defaults"]
            .as_array_mut()
            .unwrap()
            .push(first);
        expect(
            "an evidence entry repeated",
            verify(&r, &authored, &projection, &keys, &binding),
            (code::INVALID_RECEIPT, "R-RECEIPT-ORDER"),
        );
        if n > 1 {
            let mut r = receipt.clone();
            body(&mut r)["applied_defaults"]
                .as_array_mut()
                .unwrap()
                .swap(0, 1);
            expect(
                "two evidence entries swapped",
                verify(&r, &authored, &projection, &keys, &binding),
                (code::INVALID_RECEIPT, "R-RECEIPT-ORDER"),
            );
        }
        let mut b = binding.clone();
        b.remove("$.enabled");
        let want_unbound = (code::RECEIPT_UNBOUND, "R-RECEIPT-PATH-BINDING");
        if binding.contains_key("$.enabled") {
            expect(
                "a path with no binding",
                verify(&receipt, &authored, &projection, &keys, &b),
                want_unbound,
            );
        }
        let mut pr = projection.clone();
        for f in pr["fields"].as_array_mut().unwrap() {
            if f["provenance"] == "schema_default" {
                f["provenance"] = J::String("authored".into());
            }
        }
        // the object then says that no field was defaulted, and its digest no longer matches the receipt: the output binding is what is seen first
        expect(
            "an object whose provenance was changed",
            verify(&receipt, &authored, &pr, &keys, &binding),
            (code::RECEIPT_MISMATCH, "R-RECEIPT-BINDS-OUTPUT"),
        );
    }
    assert_eq!(accepted, 7, "the accepted receipts");
    println!("engine (rust): {accepted} receipts accepted by verify_receipt, {refused} altered ones refused with the engine's code and rule");
}

fn main() {
    let mut args = std::env::args().skip(1);
    let first = args
        .next()
        .expect("usage: engine-harness-rust <forms root> | differential <in> <out>");
    if first == "receipt-differential" {
        let input = args.next().expect("the generated inputs");
        let output = args.next().expect("the receipt dump of one language");
        let identity = args.next().expect("the engine identity of that language");
        receipt_differential(&input, &output, &identity);
        return;
    }
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
    assert_eq!(checked, 7, "the accepted cases");
    println!("engine (rust): {checked} accepted cases agree with the engine's try_new and digest");
    receipts(&root);
}
