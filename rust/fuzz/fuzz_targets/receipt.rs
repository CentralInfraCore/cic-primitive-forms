//! Fuzzing of `receipt_flat_fixture` (P5-3). The property is in `../receipt_check.rs`, shared with the test that replays the seeds and hands the receipts to the pinned
//! engine's verifier (`rust/tests/receipt_fuzz_seeds.rs`); the Go peer is `FuzzReceipt` in go/receipt_fuzz_test.go. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

#[path = "../receipt_check.rs"]
mod receipt_check;

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && data.starts_with(b"CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let _ = receipt_check::run(data);
});
