//! Fuzzing of `read_diff` (P4, after #107 and the differential of #113). The Go peer is `FuzzReadDiff` in go/fuzz_test.go.
//!
//! The input is the bytes handed to the reader. The reader never panics; a refusal names a reason of the closed set and an offset that is not beyond the input,
//! and `read_diff_version` refuses with the same reason and offset when it refuses at all, and names a version other than 1 exactly when `read_diff` refuses with
//! the reason `version`. A diff that is read is valid UTF-8, has version 1, is written back as the bytes it was read from, reads again to the same writing, and
//! gives the patch as many events as it has changes. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{read_diff, read_diff_version, DiffResult};
use libfuzzer_sys::fuzz_target;

const REASONS: [&str; 11] = [
    "not_utf8",
    "syntax",
    "depth",
    "duplicate_key",
    "unsupported_number",
    "shape",
    "path",
    "order",
    "no_change",
    "not_canonical",
    "version",
];

fuzz_target!(|data: &[u8]| {
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && data.starts_with(b"CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let version = read_diff_version(data);
    match read_diff(data) {
        Err(e) => {
            assert!(REASONS.contains(&e.reason), "reason {}", e.reason);
            assert!(e.offset <= data.len(), "offset {} of {}", e.offset, data.len());
            match version {
                Err(v) => assert_eq!((v.reason, v.offset), (e.reason, e.offset)),
                Ok(n) => assert_eq!(n != 1, e.reason == "version", "version {n}, reason {}", e.reason),
            }
        }
        Ok(d) => {
            assert_eq!(version.ok(), Some(1));
            assert_eq!(d.version, 1);
            let patch = d.patch_diff();
            assert_eq!(patch.changes.len(), d.changes.len());
            let written = DiffResult {
                changes: d.changes,
                representation: d.representation,
            }
            .canonical();
            assert_eq!(written.as_bytes(), data, "not written back as it was read");
            let again = read_diff(written.as_bytes()).expect("the writing of a diff reads again");
            let twice = DiffResult {
                changes: again.changes,
                representation: again.representation,
            }
            .canonical();
            assert_eq!(twice, written);
        }
    }
});
