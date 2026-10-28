//! `read_diff` (the canonical bytes of ADR 0008 read back into events): what is not exactly the canonical writing, or not a diff, is refused with its reason; a
//! diff of another version is read for its version only; no mutation of a valid diff is accepted unless it is written back as it is. The corpus-based tests
//! (every canonical string reads and writes back to itself; the patch gives the same answer from the bytes as from the events of a vector) are in
//! tests/corpus.rs. The Go peer is go/diffread_test.go.

use cic_primitive_forms::{read_diff, DiffResult};

fn reason(input: &[u8]) -> &'static str {
    match read_diff(input) {
        Ok(_) => "",
        Err(e) => e.reason,
    }
}

const ADDED: &str = r#"{"kind":"added","next":{"access":"inherit","conformance":"implemented","default_injection":null,"inherit":true,"modify":"inherit","value":1},"path":"$.mtu"}"#;
const VC: &str = r#"{"kind":"value_change","next":2,"path":"$.mtu","previous":1}"#;

fn wrap(changes: &str) -> String {
    format!(r#"{{"changes":[{changes}],"representation":[],"version":1}}"#)
}

#[test]
fn what_is_not_exactly_the_canonical_writing_is_refused() {
    let bs = "\\";
    let esc = |body: &str| {
        wrap(&format!(
            r#"{{"kind":"value_change","next":"{body}","path":"$.mtu","previous":"x"}}"#
        ))
    };
    let rep =
        |entries: &str| format!(r#"{{"changes":[],"representation":[{entries}],"version":1}}"#);
    let entry = |next: &str, path: &str, previous: &str| {
        format!(
            r#"{{"kind":"representation_change","next":"{next}","path":"{path}","previous":"{previous}"}}"#
        )
    };
    let cases: Vec<(&str, Vec<u8>, &str)> = vec![
        ("the empty input", b"".to_vec(), "syntax"),
        ("not UTF-8", [wrap("").as_bytes(), &[0xff]].concat(), "not_utf8"),
        ("a byte order mark", [&[0xef, 0xbb, 0xbf][..], wrap("").as_bytes()].concat(), "syntax"),
        ("trailing bytes", format!("{}x", wrap("")).into_bytes(), "syntax"),
        ("a trailing newline", format!("{}\n", wrap("")).into_bytes(), "syntax"),
        ("whitespace between tokens", br#"{"changes": [],"representation":[],"version":1}"#.to_vec(), "syntax"),
        ("a truncated document", br#"{"changes":[],"representation":[]"#.to_vec(), "syntax"),
        ("not an object", b"[]".to_vec(), "shape"),
        ("a bare number", b"1".to_vec(), "shape"),
        ("keys out of order", br#"{"version":1,"changes":[],"representation":[]}"#.to_vec(), "not_canonical"),
        ("a duplicate key", br#"{"changes":[],"changes":[],"representation":[],"version":1}"#.to_vec(), "duplicate_key"),
        ("a float", wrap(r#"{"kind":"value_change","next":2.5,"path":"$.mtu","previous":1}"#).into_bytes(), "unsupported_number"),
        ("an exponent", wrap(r#"{"kind":"value_change","next":1e3,"path":"$.mtu","previous":1}"#).into_bytes(), "unsupported_number"),
        ("an integer beyond 64 bits", wrap(r#"{"kind":"value_change","next":9223372036854775808,"path":"$.mtu","previous":1}"#).into_bytes(), "unsupported_number"),
        ("a leading zero", wrap(r#"{"kind":"value_change","next":02,"path":"$.mtu","previous":1}"#).into_bytes(), "unsupported_number"),
        ("minus zero", wrap(r#"{"kind":"value_change","next":-0,"path":"$.mtu","previous":1}"#).into_bytes(), "unsupported_number"),
        ("an escaped slash", esc(&format!("a{bs}/b")).into_bytes(), "not_canonical"),
        ("an escaped non-ASCII character", esc(&format!("{bs}u00e9")).into_bytes(), "not_canonical"),
        ("an uppercase unicode escape", esc(&format!("{bs}u001F")).into_bytes(), "not_canonical"),
        ("a long escape of a control character that has a short one", esc(&format!("{bs}u000a")).into_bytes(), "not_canonical"),
        ("a lone surrogate", esc(&format!("{bs}ud800")).into_bytes(), "syntax"),
        ("a lone low surrogate", esc(&format!("{bs}udc00")).into_bytes(), "syntax"),
        ("a high surrogate not followed by a low one", esc(&format!("{bs}ud800{bs}u0041")).into_bytes(), "syntax"),
        ("a raw control character in a string", wrap("{\"kind\":\"value_change\",\"next\":\"a\nb\",\"path\":\"$.mtu\",\"previous\":\"x\"}").into_bytes(), "syntax"),
        ("an unknown event kind", wrap(r#"{"kind":"renamed","path":"$.mtu"}"#).into_bytes(), "shape"),
        ("a missing key", wrap(r#"{"kind":"value_change","next":2,"path":"$.mtu"}"#).into_bytes(), "shape"),
        ("an extra key", wrap(r#"{"extra":1,"kind":"value_change","next":2,"path":"$.mtu","previous":1}"#).into_bytes(), "shape"),
        ("a path that is not a string", wrap(r#"{"kind":"value_change","next":2,"path":1,"previous":1}"#).into_bytes(), "shape"),
        ("an event that is not an object", wrap("1").into_bytes(), "shape"),
        ("a missing top-level key", br#"{"changes":[],"version":1}"#.to_vec(), "shape"),
        ("an extra top-level key", br#"{"changes":[],"extra":0,"representation":[],"version":1}"#.to_vec(), "shape"),
        ("changes that are not a list", br#"{"changes":{},"representation":[],"version":1}"#.to_vec(), "shape"),
        ("a version that is not an integer", br#"{"changes":[],"representation":[],"version":"1"}"#.to_vec(), "shape"),
        ("a missing version", br#"{"changes":[],"representation":[]}"#.to_vec(), "shape"),
        ("a negative version", br#"{"changes":[],"representation":[],"version":-1}"#.to_vec(), "shape"),
        ("a path that is not canonical", wrap(r#"{"kind":"value_change","next":2,"path":"mtu","previous":1}"#).into_bytes(), "path"),
        ("the root as a path", wrap(r#"{"kind":"value_change","next":2,"path":"$","previous":1}"#).into_bytes(), "path"),
        ("a path with a leading zero in a key", wrap(r#"{"kind":"value_change","next":2,"path":"$.a[{k=01}]","previous":1}"#).into_bytes(), "path"),
        ("two events out of the order of their paths", wrap(&format!(r#"{{"kind":"value_change","next":2,"path":"$.name","previous":1}},{VC}"#)).into_bytes(), "order"),
        ("the same path and kind twice", wrap(&format!("{VC},{VC}")).into_bytes(), "order"),
        ("two kinds at one path in the wrong order", wrap(&format!(r#"{VC},{{"changed":{{"access":{{"next":"a","previous":"b"}}}},"kind":"policy_declaration_change","path":"$.mtu"}}"#)).into_bytes(), "order"),
        ("a value change that changes nothing", wrap(r#"{"kind":"value_change","next":1,"path":"$.mtu","previous":1}"#).into_bytes(), "no_change"),
        ("a declaration change with nothing changed", wrap(r#"{"changed":{"access":{"next":"a","previous":"a"}},"kind":"policy_declaration_change","path":"$.mtu"}"#).into_bytes(), "no_change"),
        ("a declaration change with no field", wrap(r#"{"changed":{},"kind":"policy_declaration_change","path":"$.mtu"}"#).into_bytes(), "shape"),
        ("a declaration field without both sides", wrap(r#"{"changed":{"access":{"next":"a"}},"kind":"policy_declaration_change","path":"$.mtu"}"#).into_bytes(), "shape"),
        ("a set change with nothing in it", wrap(r#"{"added":[],"kind":"set_change","path":"$.ip.addresses","removed":[]}"#).into_bytes(), "no_change"),
        ("a set change not sorted", wrap(r#"{"added":["b","a"],"kind":"set_change","path":"$.ip.addresses","removed":[]}"#).into_bytes(), "shape"),
        ("a set change with a repeat", wrap(r#"{"added":["a","a"],"kind":"set_change","path":"$.ip.addresses","removed":[]}"#).into_bytes(), "shape"),
        ("an element in both sides of a set change", wrap(r#"{"added":["a"],"kind":"set_change","path":"$.ip.addresses","removed":["a"]}"#).into_bytes(), "shape"),
        ("a set element that is not a scalar", wrap(r#"{"added":[[]],"kind":"set_change","path":"$.ip.addresses","removed":[]}"#).into_bytes(), "shape"),
        ("a representation entry of the wrong kind", rep(r#"{"kind":"other","next":"short","path":"$.mtu","previous":"expanded"}"#).into_bytes(), "shape"),
        ("a representation entry with a value that is not a writing", rep(&entry("long", "$.mtu", "expanded")).into_bytes(), "shape"),
        ("a representation entry that changes nothing", rep(&entry("short", "$.mtu", "short")).into_bytes(), "no_change"),
        ("representation entries out of order", rep(&format!("{},{}", entry("short", "$.name", "expanded"), entry("short", "$.mtu", "expanded"))).into_bytes(), "order"),
        ("the same representation path twice", rep(&format!("{},{}", entry("short", "$.mtu", "expanded"), entry("expanded", "$.mtu", "short"))).into_bytes(), "order"),
        ("an added fragment with a float inside", wrap(r#"{"kind":"added","next":{"value":1.5},"path":"$.mtu"}"#).into_bytes(), "unsupported_number"),
        ("an event with a shape defect and a bad path names the shape", wrap(r#"{"changed":{},"kind":"policy_declaration_change","path":"mtu"}"#).into_bytes(), "shape"),
    ];
    for (name, input, want) in cases {
        assert_eq!(
            reason(&input),
            want,
            "{name}: {}",
            String::from_utf8_lossy(&input)
        );
    }
    // a type-different value change is a change, and the examples that must be accepted are accepted
    assert_eq!(
        reason(
            wrap(r#"{"kind":"value_change","next":false,"path":"$.mtu","previous":0}"#).as_bytes()
        ),
        ""
    );
    for ok in [wrap(""), wrap(ADDED), wrap(VC)] {
        assert_eq!(reason(ok.as_bytes()), "", "{ok}");
    }
}

#[test]
fn nesting_is_bounded() {
    let deep = "[".repeat(100);
    let input = format!(
        r#"{{"changes":[{{"kind":"added","next":{deep},"path":"$.mtu"}}],"representation":[],"version":1}}"#
    );
    assert_eq!(reason(input.as_bytes()), "depth");
}

#[test]
fn a_diff_of_another_version_is_read_for_its_version_only() {
    let got = read_diff(br#"{"version":2}"#).expect("version 2");
    assert!(got.version == 2 && got.changes.is_empty() && got.representation.is_empty());
    assert_eq!(reason(br#"{"version": 2}"#), "syntax");
    assert_eq!(reason(br#"{"b":1,"version":2,"a":0}"#), "not_canonical");
}

#[test]
fn the_patch_takes_the_events_and_not_the_representation() {
    let bytes = format!(
        r#"{{"changes":[{VC}],"representation":[{{"kind":"representation_change","next":"short","path":"$.name","previous":"expanded"}}],"version":1}}"#
    );
    let got = read_diff(bytes.as_bytes()).expect("a diff");
    assert_eq!((got.changes.len(), got.representation.len()), (1, 1));
    let patch = got.patch_diff();
    assert_eq!((patch.version, patch.changes.len()), (1, 1));
}

#[test]
fn no_mutation_of_a_valid_diff_is_accepted_unless_it_is_written_back_as_it_is() {
    let seeds = [
        wrap(&format!("{ADDED},{VC}")),
        r#"{"changes":[{"added":["b"],"kind":"set_change","path":"$.ip.addresses","removed":["a"]},{"changed":{"access":{"next":["OU=a"],"previous":"inherit"}},"kind":"policy_declaration_change","path":"$.mtu"}],"representation":[{"kind":"representation_change","next":"expanded","path":"$.name","previous":"short"}],"version":1}"#.to_string(),
    ];
    let mut checked = 0;
    for seed in &seeds {
        for i in 0..seed.len() {
            for repl in [b' ', b'x', b'0', b'}'] {
                let mut m = seed.clone().into_bytes();
                m[i] = repl;
                checked += 1;
                if let Ok(got) = read_diff(&m) {
                    if got.version == 1 {
                        let again = DiffResult {
                            changes: got.changes,
                            representation: got.representation,
                        }
                        .canonical();
                        assert_eq!(
                            again.as_bytes(),
                            &m[..],
                            "accepted bytes that are not written back as they are"
                        );
                    }
                }
            }
        }
    }
    assert!(checked > 700, "only {checked} mutations");
}
