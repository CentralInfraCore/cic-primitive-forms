//! `read_diff` against the corpus: every canonical diff string of the corpus (the positive `diff` cases and the diff of each `patch` case) reads and writes back to
//! itself, and the events read from the bytes of a patch vector are the events the vector lists. The Go peer is go/diffread_test.go (which also runs the patch from both).

use std::fs;
use std::path::PathBuf;

use cic_primitive_forms::{read_diff, DiffResult, EventKind, Node, Value};
use saphyr::{LoadableYamlNode, ScalarOwned, YamlOwned};

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn suites() -> Vec<YamlOwned> {
    let mut dirs: Vec<PathBuf> = fs::read_dir(repo_root().join("corpus"))
        .expect("corpus directory")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.join("vectors.yaml").is_file())
        .collect();
    dirs.sort();
    dirs.iter()
        .map(|d| {
            let text = fs::read_to_string(d.join("vectors.yaml")).expect("vectors.yaml");
            YamlOwned::load_from_str(&text).expect("yaml").remove(0)
        })
        .collect()
}

fn get<'a>(y: &'a YamlOwned, key: &str) -> Option<&'a YamlOwned> {
    y.as_mapping_get(key)
}

fn text<'a>(y: &'a YamlOwned, what: &str) -> &'a str {
    y.as_str()
        .unwrap_or_else(|| panic!("{what}: expected a string"))
}

/// A corpus value as a Node (the typed values of the vectors are plain values here).
fn node_of(y: &YamlOwned) -> Node {
    match y {
        YamlOwned::Value(ScalarOwned::Null) => Node::new(Value::Null),
        YamlOwned::Value(ScalarOwned::Boolean(b)) => Node::new(Value::Bool(*b)),
        YamlOwned::Value(ScalarOwned::Integer(i)) => Node::new(Value::Int(*i)),
        YamlOwned::Value(ScalarOwned::String(s)) => Node::new(Value::Str(s.clone())),
        YamlOwned::Sequence(items) => Node::new(Value::Seq(items.iter().map(node_of).collect())),
        YamlOwned::Mapping(m) => Node::new(Value::Map(
            m.iter()
                .map(|(k, v)| cic_primitive_forms::Entry {
                    key: text(k, "key").to_string(),
                    key_pos: None,
                    value: node_of(v),
                })
                .collect(),
        )),
        other => panic!("unsupported corpus value {other:?}"),
    }
}

fn kind_of(name: &str) -> EventKind {
    match name {
        "added" => EventKind::Added,
        "removed" => EventKind::Removed,
        "value_change" => EventKind::ValueChange,
        "policy_declaration_change" => EventKind::PolicyDeclarationChange,
        _ => EventKind::SetChange,
    }
}

fn canonical_diffs() -> Vec<String> {
    let mut out = Vec::new();
    for suite in suites() {
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let canonical = match text(get(case, "kind").expect("kind"), "kind") {
                "diff" | "diff_stability" => get(case, "expect").and_then(|e| get(e, "canonical")),
                "patch" => get(case, "diff").and_then(|d| get(d, "canonical")),
                _ => None,
            };
            if let Some(c) = canonical {
                out.push(text(c, "canonical").to_string());
            }
        }
    }
    out
}

#[test]
fn every_canonical_diff_of_the_corpus_reads_and_writes_back_to_itself() {
    let all = canonical_diffs();
    assert!(all.len() >= 120, "only {} canonical strings", all.len());
    for s in &all {
        let got = read_diff(s.as_bytes()).unwrap_or_else(|e| panic!("{s}: {e}"));
        if got.version == 1 {
            let again = DiffResult {
                changes: got.changes,
                representation: got.representation,
            }
            .canonical();
            assert_eq!(&again, s);
        }
    }
}

#[test]
fn the_events_read_from_the_bytes_of_a_patch_vector_are_the_events_it_lists() {
    let mut ran = 0;
    for suite in suites() {
        if get(&suite, "suite").and_then(YamlOwned::as_str) != Some("patch") {
            continue;
        }
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let diff = get(case, "diff").expect("diff");
            let read =
                read_diff(text(get(diff, "canonical").expect("canonical"), "canonical").as_bytes())
                    .unwrap_or_else(|e| panic!("{id}: {e}"));
            let Some(changes) = get(diff, "changes").and_then(YamlOwned::as_vec) else {
                assert!(
                    read.version != 1 && read.changes.is_empty(),
                    "{id}: another version is read for its version only"
                );
                continue;
            };
            ran += 1;
            assert_eq!(read.changes.len(), changes.len(), "{id}");
            for (e, c) in read.changes.iter().zip(changes) {
                assert_eq!(
                    (e.kind, e.path.as_str()),
                    (
                        kind_of(text(get(c, "kind").expect("kind"), "kind")),
                        text(get(c, "path").expect("path"), "path")
                    ),
                    "{id}"
                );
                for (got, key) in [(&e.previous, "previous"), (&e.next, "next")] {
                    if let Some(want) = get(c, key).and_then(|v| get(v, "value")) {
                        assert!(
                            got.as_ref().is_some_and(|g| g.same(&node_of(want))),
                            "{id}: {key}"
                        );
                    }
                }
            }
        }
    }
    assert!(ran >= 50, "only {ran} patch cases with events");
}
