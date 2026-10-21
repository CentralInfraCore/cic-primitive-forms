//! Edge cases of the text of logical paths that the corpus does not spell out (`testdata/p1/pathtext.json`): every escape,
//! the ends of the text, the control characters. The Go peer is `TestSharedPathTextEdges`.

use cic_primitive_forms::{format_path, parse_path, KeyValue, PathSeg};
use serde_json::Value as J;

#[test]
fn the_shared_path_text_edges() {
    let root = std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    );
    let file: J = serde_json::from_str(
        &std::fs::read_to_string(root.join("testdata/p1/pathtext.json")).unwrap(),
    )
    .unwrap();
    for c in file["cases"].as_array().unwrap() {
        let path = c["path"].as_str().unwrap();
        match (c.get("error").and_then(J::as_str), parse_path(path)) {
            (Some(code), Err(e)) => {
                assert_eq!(e.code, code, "{path:?}");
                assert_eq!(e.offset as u64, c["offset"].as_u64().unwrap(), "{path:?}");
            }
            (None, Ok(segs)) => {
                let PathSeg::Item(item) = &segs[1] else {
                    panic!("{path:?}")
                };
                assert_eq!(
                    item[0].value,
                    KeyValue::Str(c["value"].as_str().unwrap().to_string()),
                    "{path:?}"
                );
                assert_eq!(format_path(&segs).as_deref(), Ok(path), "{path:?}");
            }
            (want, got) => panic!("{path:?}: want {want:?}, got {got:?}"),
        }
    }
}
