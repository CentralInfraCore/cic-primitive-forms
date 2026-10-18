//! rustbranch: cic-branch/1 branch coverage of a Rust crate (ADR 0009).
//!
//!     rustbranch run --dir CRATE --out OUT.json [-- cargo test args]
//!
//! The own source files (src/**/*.rs) are enumerated and instrumented in a throwaway copy of the crate, `cargo test`
//! runs there, and the number of times each edge was taken is written to OUT.json. Anything it cannot enumerate or
//! instrument is an error; a failing test run produces no measurement.

mod instrument;

use instrument::{instrument as instr, Edge};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

const TOOL_NAME: &str = "rustbranch";
const TOOL_VERSION: &str = "0.1.0";

#[derive(Serialize)]
struct FileResult {
    path: String,
    sha256: String,
    edges: Vec<Edge>,
    hits: Vec<u64>,
}

#[derive(Serialize)]
struct PkgResult {
    import_path: String,
    files: Vec<FileResult>,
}

#[derive(Serialize)]
struct RunResult {
    tool: std::collections::BTreeMap<&'static str, &'static str>,
    toolchain: String,
    packages: Vec<PkgResult>,
    ignored_files: Vec<String>,
}

fn die(msg: String) -> ! {
    eprintln!("rustbranch: {}", msg);
    std::process::exit(1)
}

fn rs_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let mut entries: Vec<_> = fs::read_dir(dir).unwrap_or_else(|e| die(format!("{}: {}", dir.display(), e))).flatten().collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let p = e.path();
        if p.is_dir() {
            rs_files(&p, out);
        } else if p.extension().map_or(false, |x| x == "rs") {
            out.push(p);
        }
    }
}

fn copy_dir(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for e in fs::read_dir(from).unwrap().flatten() {
        let name = e.file_name();
        if name == "target" || name == ".git" {
            continue;
        }
        let (src, dst) = (e.path(), to.join(&name));
        if src.is_dir() {
            copy_dir(&src, &dst);
        } else {
            fs::copy(&src, &dst).unwrap();
        }
    }
}

fn crate_name(manifest: &str) -> String {
    manifest
        .lines()
        .find_map(|l| l.trim().strip_prefix("name").and_then(|r| r.split('"').nth(1)).map(|s| s.to_string()))
        .unwrap_or_else(|| die("no package name in Cargo.toml".into()))
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 || args[1] != "run" {
        die("usage: rustbranch run --dir CRATE --out OUT.json [-- cargo test args]".into());
    }
    let (mut dir, mut out, mut extra) = (String::new(), String::new(), vec![]);
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--dir" => { dir = args[i + 1].clone(); i += 2 }
            "--out" => { out = args[i + 1].clone(); i += 2 }
            "--" => { extra = args[i + 1..].to_vec(); break }
            a => die(format!("unknown argument {}", a)),
        }
    }
    if dir.is_empty() || out.is_empty() {
        die("--dir and --out are required".into());
    }
    let root = fs::canonicalize(&dir).unwrap_or_else(|e| die(format!("{}: {}", dir, e)));
    let manifest = fs::read_to_string(root.join("Cargo.toml")).unwrap_or_else(|e| die(format!("Cargo.toml: {}", e)));
    if manifest.contains("[workspace]") || root.join("build.rs").exists() {
        die("workspaces and build scripts are not supported".into());
    }
    let src_dir = root.join("src");
    let mut files = vec![];
    rs_files(&src_dir, &mut files);
    if files.is_empty() {
        die("no source files under src/".into());
    }
    let tmp = std::env::temp_dir().join(format!("rustbranch-{}", std::process::id()));
    let _ = fs::remove_dir_all(&tmp);
    let work = tmp.join("crate");
    copy_dir(&root, &work);
    let hits_dir = tmp.join("hits");
    fs::create_dir_all(&hits_dir).unwrap();

    let mut next = 0usize;
    let mut edges: Vec<Edge> = vec![];
    let mut results: Vec<(String, String, Vec<Edge>)> = vec![];
    let mut errors = vec![];
    for f in &files {
        let rel = f.strip_prefix(&root).unwrap().to_string_lossy().replace('\\', "/");
        let src = fs::read_to_string(f).unwrap_or_else(|e| die(format!("{}: {}", rel, e)));
        let before = edges.len();
        match instr(&rel, &src, &mut next, &mut edges) {
            Ok(text) => {
                fs::write(work.join(&rel), text).unwrap();
                let sum = hex(&Sha256::digest(src.as_bytes()));
                results.push((rel, sum, edges[before..].to_vec()));
            }
            Err(e) => errors.push(e),
        }
    }
    if !errors.is_empty() {
        die(errors.join("\n"));
    }
    // the runtime module and its declaration in the crate roots
    fs::write(work.join("src/rustbranch_rt.rs"), runtime(next)).unwrap();
    let mut roots = 0;
    for r in ["src/lib.rs", "src/main.rs"] {
        let p = work.join(r);
        if p.exists() {
            let mut text = fs::read_to_string(&p).unwrap();
            text = text.replace("#![forbid(unsafe_code)]", "#![deny(unsafe_code)]");
            text.push_str("\n#[allow(dead_code)]\nmod rustbranch_rt;\n");
            fs::write(&p, text).unwrap();
            roots += 1;
        }
    }
    if roots == 0 {
        die("no src/lib.rs or src/main.rs".into());
    }
    let status = Command::new("cargo")
        .arg("test")
        .args(&extra)
        .current_dir(&work)
        .env("RUSTBRANCH_OUT", &hits_dir)
        .env("CARGO_TARGET_DIR", tmp.join("target"))
        .status()
        .unwrap_or_else(|e| die(format!("cargo: {}", e)));
    if !status.success() {
        die("cargo test failed (no measurement is produced from a failing run)".into());
    }
    let mut totals = vec![0u64; next.max(1)];
    let mut n = 0;
    for e in fs::read_dir(&hits_dir).unwrap().flatten() {
        let v: Vec<u64> = serde_json::from_str(&fs::read_to_string(e.path()).unwrap()).unwrap_or_else(|e| die(format!("hits: {}", e)));
        if v.len() != totals.len() {
            die("a test process wrote counters of another size".into());
        }
        for (t, x) in totals.iter_mut().zip(v) {
            *t += x;
        }
        n += 1;
    }
    if n == 0 && next > 0 {
        die("the tests ran but wrote no counters".into());
    }
    let toolchain = Command::new("rustc").arg("--version").output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default();
    let files_out = results
        .into_iter()
        .map(|(path, sha256, edges)| {
            let hits = edges.iter().map(|e| totals[e.id]).collect();
            FileResult { path, sha256, edges, hits }
        })
        .collect();
    let res = RunResult {
        tool: [("name", TOOL_NAME), ("version", TOOL_VERSION)].into_iter().collect(),
        toolchain,
        packages: vec![PkgResult { import_path: crate_name(&manifest), files: files_out }],
        ignored_files: vec![],
    };
    fs::write(&out, serde_json::to_string_pretty(&res).unwrap() + "\n").unwrap();
    let _ = fs::remove_dir_all(&tmp);
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

fn runtime(n: usize) -> String {
    format!(
        r#"// Generated by rustbranch: the counters of the measured copy. Not part of the crate under test.
#![allow(unsafe_code, dead_code)]
use std::sync::atomic::{{AtomicU64, Ordering}};
use std::sync::Once;

static HITS: [AtomicU64; {n}] = [const {{ AtomicU64::new(0) }}; {n}];
static REGISTER: Once = Once::new();

extern "C" {{
    fn atexit(cb: extern "C" fn()) -> i32;
}}

extern "C" fn flush() {{
    if let Ok(dir) = std::env::var("RUSTBRANCH_OUT") {{
        let v: Vec<u64> = HITS.iter().map(|h| h.load(Ordering::Relaxed)).collect();
        let name = format!("{{}}/{{}}-{{}}.json", dir, std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        let body = format!("[{{}}]", v.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(","));
        let _ = std::fs::write(name, body);
    }}
}}

pub fn hit(id: usize) {{
    REGISTER.call_once(|| unsafe {{
        atexit(flush);
    }});
    HITS[id].fetch_add(1, Ordering::Relaxed);
}}

pub fn cond(id: usize, v: bool) -> bool {{
    if v {{
        hit(id)
    }} else {{
        hit(id + 1)
    }}
    v
}}

pub trait Failed {{
    fn failed(&self) -> bool;
}}
impl<T, E> Failed for Result<T, E> {{
    fn failed(&self) -> bool {{
        self.is_err()
    }}
}}
impl<T> Failed for Option<T> {{
    fn failed(&self) -> bool {{
        self.is_none()
    }}
}}

pub fn q<R: Failed>(id: usize, r: R) -> R {{
    if r.failed() {{
        hit(id + 1)
    }} else {{
        hit(id)
    }}
    r
}}
"#,
        n = n.max(1)
    )
}
