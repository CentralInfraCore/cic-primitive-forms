//! Dumps the outcome of `read_diff` and `read_diff_version` for the generated inputs of `tools/parity/gen_diffread.py`, for the Rust/Go differential of the reader
//! of the canonical bytes of a diff. `PARITY_DIFFREAD_IN` is a JSON-lines file ({"id", "tag", "hex"}), `PARITY_DIFFREAD_OUT` receives one JSON line per input. Without
//! the two variables this test does nothing. The Go peer is `go/parity_diffread_dump_test.go`; `tools/parity/compare_diffread.py` compares and checks the promises
//! of the reader on its own.
//!
//! For each input `r` is `["ok", the diff written back, number of events, number of representation entries]` or `["err", reason, offset]`, and `v` is
//! `["ok", version]` or `["err", reason, offset]`.

use cic_primitive_forms::{read_diff, read_diff_version, DiffResult};
use serde_json::{json, Value as Json};
use std::fs;

#[test]
fn dump_diffread_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_DIFFREAD_IN"),
        std::env::var_os("PARITY_DIFFREAD_OUT"),
    ) else {
        return;
    };
    let mut out = String::new();
    for line in fs::read_to_string(input).expect("input").lines() {
        let case: Json = serde_json::from_str(line).expect("a JSON line");
        let hex = case["hex"].as_str().expect("hex");
        let bytes: Vec<u8> = (0..hex.len() / 2)
            .map(|k| u8::from_str_radix(&hex[2 * k..2 * k + 2], 16).expect("hex"))
            .collect();
        let r = match read_diff(&bytes) {
            Ok(d) => {
                let (events, entries) = (d.changes.len(), d.representation.len());
                let written = DiffResult {
                    changes: d.changes,
                    representation: d.representation,
                }
                .canonical();
                json!(["ok", written, events, entries])
            }
            Err(e) => json!(["err", e.reason, e.offset]),
        };
        let v = match read_diff_version(&bytes) {
            Ok(n) => json!(["ok", n]),
            Err(e) => json!(["err", e.reason, e.offset]),
        };
        out += &json!({"id": case["id"], "r": r, "v": v}).to_string();
        out.push('\n');
    }
    fs::write(output, out).expect("output");
}
