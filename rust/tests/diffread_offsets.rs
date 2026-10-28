//! The reader of the canonical bytes stops at the first byte it refuses, and says which reason and at which offset; the offset is never beyond the end of the
//! input. The inputs, reasons and offsets are in testdata/p1/diffread-offsets.tsv, which the Go test (go/diffread_offsets_test.go) reads too: the two readers
//! were written apart and have to agree on every line.

use cic_primitive_forms::read_diff;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

#[test]
fn the_reader_stops_at_the_reason_and_offset_of_the_shared_table() {
    let text =
        std::fs::read_to_string(repo_root().join("testdata/p1/diffread-offsets.tsv")).unwrap();
    let mut checked = 0;
    for line in text
        .lines()
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
    {
        let f: Vec<&str> = line.split('\t').collect();
        assert_eq!(f.len(), 4, "a line of the table: {line:?}");
        let input: Vec<u8> = (0..f[0].len() / 2)
            .map(|k| u8::from_str_radix(&f[0][2 * k..2 * k + 2], 16).unwrap())
            .collect();
        checked += 1;
        match read_diff(&input) {
            Ok(_) => assert_eq!(f[1], "ok", "{}: read, want {} at {}", f[3], f[1], f[2]),
            Err(e) => {
                assert_eq!(e.reason, f[1], "{}: {} at {}", f[3], e.reason, e.offset);
                assert_eq!(
                    e.offset.to_string(),
                    f[2],
                    "{}: {} at {}",
                    f[3],
                    e.reason,
                    e.offset
                );
            }
        }
    }
    assert!(checked >= 50, "only {checked} lines were checked");
}
