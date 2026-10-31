//! Replays the seeds of the `receipt` fuzz target through its property (P5-3, `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 19), in every `cargo test`, so that a defect the
//! seeds show is red here and not only in the 60 seconds of the fuzz job. The seeds are `rust/fuzz/corpus/receipt/*`, derived by `tools/p5/receipt_fuzz_seeds.py` from the files
//! the receipt already has. The Go peer is `TestReceiptFuzzSeeds` in `go/receipt_fuzz_test.go`.
//!
//! With `CIC_FUZZ_RECEIPT_DUMP_DIR` set it also writes `rust.in.jsonl` (the id, the declared names) and `rust.jsonl` (the receipt each seed and each mutation made, or the way a
//! seed ended) there, and `rust.trace` (the end of every mutation), for `tools/parity/compare_receipt_fuzz.py` and the engine harnesses: `receipt-differential rust.in.jsonl rust.jsonl cic-primitive-forms-rust` runs the pinned engine's own verifier on every receipt the seeds made and
//! refuses the altered ones.

#[path = "../fuzz/receipt_check.rs"]
mod check;

use check::Ended;
use serde_json::{json, Value as J};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

/// The deterministic mutation round, the same algorithm in the Go peer: xorshift64* from a fixed state, 1 to 4 mutations of a seed, a byte changed, a token of the
/// dictionary inserted, a byte deleted, the flags or the kind byte set. It gives the property thousands of inputs in every `cargo test`, and the engine the receipts they made.
const ROUNDS: usize = 5_000;
const DUMPED: usize = 1_500;
/// The id of the receipt a mutation made is this plus its number in the round, so that the dumps of the two languages line up.
const MUTATION_ID: usize = 1_000_000;
const DICTIONARY: [&[u8]; 16] = [
    b"\n",
    b": ",
    b"- ",
    b"~",
    b"\"",
    b"\\",
    b"{",
    b"}",
    b"[",
    b"]",
    b"type: t\n",
    b"mtu: 1\n",
    b"enabled: true\n",
    b"label: x\n",
    b"maybe: ~\n",
    b"a: 1\n",
];

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
}

fn mutated(seeds: &[(String, Vec<u8>)], rng: &mut Rng, n: usize) -> Vec<u8> {
    let mut data = seeds[n % seeds.len()].1.clone();
    for _ in 0..1 + rng.next() % 4 {
        let pos = |rng: &mut Rng, len: usize| {
            if len == 0 {
                0
            } else {
                (rng.next() % len as u64) as usize
            }
        };
        match rng.next() % 5 {
            0 => {
                let at = pos(rng, data.len());
                let byte = (rng.next() & 0xff) as u8;
                if at < data.len() {
                    data[at] = byte;
                }
            }
            1 => {
                let at = pos(rng, data.len() + 1).min(data.len());
                let token = DICTIONARY[(rng.next() % DICTIONARY.len() as u64) as usize];
                data.splice(at..at, token.iter().copied());
            }
            2 => {
                let at = pos(rng, data.len());
                if at < data.len() {
                    data.remove(at);
                }
            }
            3 => {
                let flags = (rng.next() % 128) as u8;
                if !data.is_empty() {
                    data[0] = flags;
                }
            }
            _ => {
                let kind = (rng.next() % 27) as u8;
                if data.len() > 1 {
                    data[1] = kind;
                }
            }
        }
    }
    data
}

fn seeds() -> Vec<(String, Vec<u8>)> {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fuzz/corpus/receipt");
    let mut all: Vec<(String, Vec<u8>)> = fs::read_dir(dir)
        .expect("the seed corpus")
        .map(|e| {
            let e = e.expect("an entry");
            (
                e.file_name().to_string_lossy().into_owned(),
                fs::read(e.path()).expect("a seed"),
            )
        })
        .collect();
    all.sort();
    all
}

#[test]
fn the_seeds_hold_the_property_and_reach_every_end() {
    let mut ends: BTreeMap<String, usize> = BTreeMap::new();
    let mut defaults = std::collections::BTreeSet::new();
    let (mut input, mut output) = (String::new(), String::new());
    for (id, (name, data)) in seeds().iter().enumerate() {
        let (ended, made) = check::run(data);
        *ends.entry(format!("{ended:?}")).or_default() += 1;
        input += &json!({"id": id, "keys": check::declared()}).to_string();
        input.push('\n');
        let r = match made {
            Some(m) => {
                defaults.insert(m.defaults);
                // the receipt is the 15 fields of the contract, as JSON
                let doc: J = serde_json::from_str(&m.document).expect("JSON");
                assert_eq!(doc.as_object().expect("an object").len(), 1, "{name}");
                assert_eq!(
                    doc["materialization"].as_object().expect("the body").len(),
                    15,
                    "{name}"
                );
                json!([
                    "ok",
                    m.document,
                    m.authored_input,
                    m.binding,
                    m.expected_keys,
                    m.projection
                ])
            }
            None => json!(["err", format!("{ended:?}"), ""]),
        };
        output += &json!({"id": id, "r": r}).to_string();
        output.push('\n');
    }
    // the seeds reach every end the property tells apart, and receipts with no default, one and several
    for end in [
        Ended::Left,
        Ended::Identity,
        Ended::Version,
        Ended::Release,
        Ended::Engine,
        Ended::Document,
        Ended::Capability,
        Ended::Repeated,
        Ended::Segment,
        Ended::Receipt,
    ] {
        if end == Ended::Left {
            continue; // a seed is never left: every seed is UTF-8 and parses
        }
        assert!(
            ends.contains_key(&format!("{end:?}")),
            "no seed ends as {end:?}: {ends:?}"
        );
    }
    assert!(!ends.contains_key("Left"), "a seed that is left: {ends:?}");
    assert!(
        defaults.contains(&0) && defaults.contains(&1) && defaults.iter().any(|d| *d > 1),
        "{defaults:?}"
    );
    // the mutation round: the property holds on every input, and the receipts it makes are handed to the engine
    let all = seeds();
    let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
    let mut made_by_mutation = 0;
    let mut mutated_ends: BTreeMap<String, usize> = BTreeMap::new();
    let mut trace = String::new();
    for n in 0..ROUNDS {
        let data = mutated(&all, &mut rng, n);
        let (ended, made) = check::run(&data);
        let reason = if ended == Ended::Left {
            check::left_reason(&data)
        } else {
            String::new()
        };
        trace += &format!(
            "{n}\t{ended:?}\t{}\t{reason}\n",
            data.iter().map(|b| format!("{b:02x}")).collect::<String>()
        );
        *mutated_ends.entry(format!("{ended:?}")).or_default() += 1;
        if let (Some(m), true) = (made, made_by_mutation < DUMPED) {
            made_by_mutation += 1;
            input += &json!({"id": MUTATION_ID + n, "keys": check::declared()}).to_string();
            input.push('\n');
            output += &json!({"id": MUTATION_ID + n, "r": ["ok", m.document, m.authored_input, m.binding, m.expected_keys, m.projection]}).to_string();
            output.push('\n');
        }
    }
    for end in [
        Ended::Left,
        Ended::Identity,
        Ended::Version,
        Ended::Release,
        Ended::Engine,
        Ended::Document,
        Ended::Capability,
        Ended::Repeated,
        Ended::Receipt,
    ] {
        assert!(
            mutated_ends.contains_key(&format!("{end:?}")),
            "the mutation round never ends as {end:?}: {mutated_ends:?}"
        );
    }
    if let Some(dir) = std::env::var_os("CIC_FUZZ_RECEIPT_DUMP_DIR") {
        let dir = PathBuf::from(dir);
        fs::write(dir.join("rust.in.jsonl"), input).expect("rust.in.jsonl");
        fs::write(dir.join("rust.jsonl"), output).expect("rust.jsonl");
        fs::write(dir.join("rust.trace"), trace).expect("rust.trace");
    }
}

#[test]
fn what_is_not_text_or_does_not_parse_is_left_to_the_parser() {
    assert_eq!(check::run(&[]).0, Ended::Left);
    assert_eq!(check::run(&[0]).0, Ended::Left);
    assert_eq!(check::run(&[0, 0, 0xff, 0xfe]).0, Ended::Left);
    assert_eq!(check::run(b"\0\0a: [\n").0, Ended::Left);
}
