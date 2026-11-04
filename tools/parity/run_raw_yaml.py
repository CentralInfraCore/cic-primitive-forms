#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One local command for the raw YAML differential, Unit 3b (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, sections 3, 5, 8, 9).

    run_raw_yaml.py [--out DIR] [--seed N] [--rust-target-dir DIR]

Builds the byte-level input (tools/parity/gen_raw_yaml.py, regenerated and compared), runs the Rust and Go dumps and the Python oracle on the SAME bytes, compares (parity, construction,
allowed, mapping, difference from Python) and writes one report. The memory limit (RLIMIT_AS) and the wall-clock limit are ENFORCED on every reader process: the input is cut in chunks, a
chunk that dies or overruns is re-run one case at a time, and the case that kills or overruns its process is recorded as CRASH or TIMEOUT, which is red. Unit 3c (the selftest and the
evidence report) is on hold, so this report is never marked as evidence. Not a CI step; no file under .github/workflows is touched.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import compare_raw_yaml as cmp_  # noqa: E402
import gen_raw_yaml as gen  # noqa: E402

KNOWN = os.path.join(HERE, "known_differences_raw_yaml.json")
FLOORS = os.path.join(HERE, "floors_raw_yaml.json")
SOURCES = {
    "go": ["go/parse.go", "go/node.go", "go/errors.go", "go/digestmapping.go", "go/parity_raw_yaml_dump_test.go"],
    "rust": ["rust/src/parse.rs", "rust/src/node.rs", "rust/src/error.rs", "rust/src/digest_mapping.rs", "rust/tests/parity_raw_yaml_dump.rs"],
    "python": ["tools/parity/dump_raw_yaml_py.py", "tools/p5/digest_mapping.py"],
}
CHUNK = 100
SLACK = 60          # seconds on top of the per-case limit before the runner kills a reader process that is not answering at all
TIMEOUT = gen.LIMITS["case_timeout_seconds"]
MEMORY = gen.LIMITS["memory_bytes"]


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def tool_env():
    env = dict(os.environ)
    base = os.environ.get("CIC_TOOLCHAIN")
    if base:
        env.setdefault("GOCACHE", os.path.join(base, "gocache"))
        env.setdefault("GOPATH", os.path.join(base, "gopath"))
        env.setdefault("CARGO_HOME", os.path.join(base, "cargo"))
        env.setdefault("RUSTUP_HOME", os.path.join(base, "rustup"))
        env["PATH"] = os.pathsep.join([os.path.join(base, "go", "bin"), os.path.join(base, "cargo", "bin"), env["PATH"]])
    return env


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))   # read at call time: the tests lower it


def run_limited(cmd, cwd, env, wall):
    """-> (returncode or None on a wall-clock overrun, stderr tail). The memory limit is applied to the process itself."""
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=wall, preexec_fn=limits)
        return p.returncode, (p.stdout + p.stderr)[-400:]
    except subprocess.TimeoutExpired:
        return None, "wall-clock overrun"


def build_binaries(env, rust_target):
    go_bin = os.path.join(ROOT, "build", "raw-yaml-go.test")
    os.makedirs(os.path.dirname(go_bin), exist_ok=True)
    p = subprocess.run(["go", "test", "-c", "-o", go_bin, "."], cwd=os.path.join(ROOT, "go"), env=env, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit("the Go test binary did not build:\n" + (p.stdout + p.stderr)[-1500:])
    e = dict(env)
    if rust_target:
        e["CARGO_TARGET_DIR"] = rust_target
    p = subprocess.run(["cargo", "test", "--test", "parity_raw_yaml_dump", "--no-run", "--message-format=json"], cwd=os.path.join(ROOT, "rust"), env=e, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit("the Rust test binary did not build:\n" + p.stderr[-1500:])
    rust_bin = None
    for line in p.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and m.get("target", {}).get("name") == "parity_raw_yaml_dump" and m.get("executable"):
            rust_bin = m["executable"]
    if not rust_bin:
        raise SystemExit("the Rust test binary was not found in the build output")
    return go_bin, rust_bin


def reader_cmd(lang, binary):
    if lang == "go":
        return [binary, "-test.run", "^TestDumpRawYAMLOutcomes$", "-test.count=1"], os.path.join(ROOT, "go")
    return [binary, "--exact", "dump_raw_yaml_outcomes", "--test-threads=1"], os.path.join(ROOT, "rust")


def run_dump(lang, binary, lines, env, workdir, cmd_override=None):
    """-> the dump lines for `lines` (cases as JSON text). Chunked; a chunk that fails is re-run one case at a time; a case that kills or overruns its process is CRASH or TIMEOUT."""
    cmd, cwd = cmd_override or reader_cmd(lang, binary)
    out = []
    stats = {"chunks": 0, "rerun_chunks": 0, "crash": 0, "timeout": 0}

    def attempt(batch, wall):
        inp = os.path.join(workdir, "%s-chunk-in.jsonl" % lang)
        outp = os.path.join(workdir, "%s-chunk-out.jsonl" % lang)
        with open(inp, "w", encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in batch))
        if os.path.exists(outp):
            os.unlink(outp)
        e = dict(env, PARITY_RAW_YAML_IN=inp, PARITY_RAW_YAML_OUT=outp, PARITY_RAW_YAML_TIMEOUT=str(TIMEOUT))
        rc, tail = run_limited(cmd, cwd, e, wall)
        if rc == 0 and os.path.exists(outp):
            with open(outp, encoding="utf-8") as f:
                got = f.read().splitlines()
            if len(got) == len(batch):
                return got, None
        return None, ("TIMEOUT" if rc is None else "CRASH", "exit %s: %s" % (rc, tail.strip().replace("\n", " ")[-160:]))

    for i in range(0, len(lines), CHUNK):
        batch = lines[i:i + CHUNK]
        stats["chunks"] += 1
        got, why = attempt(batch, TIMEOUT * len(batch) + 2 * SLACK)
        if got is not None:
            out += got
            continue
        stats["rerun_chunks"] += 1
        for line in batch:
            got, why = attempt([line], TIMEOUT + SLACK)
            if got is not None:
                out += got
            else:
                cid = json.loads(line)["id"]
                kind, detail = why
                stats["crash" if kind == "CRASH" else "timeout"] += 1
                out.append(json.dumps({"id": cid, "ok": False, "class": kind, "code": kind, "detail": detail}, sort_keys=True))
    return out, stats


def version(cmd, env):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=env).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def git(*args):
    p = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "raw-yaml-diff"))
    ap.add_argument("--seed", type=int, default=gen.DEFAULT_SEED)
    ap.add_argument("--rust-target-dir")
    a = ap.parse_args(argv)
    env = tool_env()
    os.makedirs(a.out, exist_ok=True)
    cs = gen.generate(a.seed)
    text = gen.render(cs.rows)
    inp = os.path.join(a.out, "input.jsonl")
    with open(inp, "w", encoding="utf-8") as f:
        f.write(text)
    with open(inp, encoding="utf-8") as f:
        if f.read() != gen.render(gen.generate(a.seed).rows):
            raise SystemExit("the input file is not the deterministic output of the generator: refusing to run")
    digest = sha256_text(text)
    lines = text.splitlines()
    go_bin, rust_bin = build_binaries(env, a.rust_target_dir)
    dumps, stats = {}, {}
    for lang, binary in (("go", go_bin), ("rust", rust_bin)):
        got, stats[lang] = run_dump(lang, binary, lines, env, a.out)
        dumps[lang] = os.path.join(a.out, "dump-%s.jsonl" % lang)
        with open(dumps[lang], "w", encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in got))
    dumps["python"] = os.path.join(a.out, "dump-python.jsonl")
    rc, tail = run_limited([sys.executable, os.path.join(HERE, "dump_raw_yaml_py.py"), inp, dumps["python"]], ROOT, dict(os.environ), TIMEOUT * len(lines))
    if rc != 0:
        raise SystemExit("the Python dump failed: " + tail)
    known = json.load(open(KNOWN, encoding="utf-8"))
    floors = json.load(open(FLOORS, encoding="utf-8"))
    rep = cmp_.compare(inp, dumps, known, floors)
    status = git("status", "--porcelain", "-uall")
    rel_out = os.path.relpath(os.path.abspath(a.out), ROOT)
    clean = status is not None and all(l[3:].strip('"').startswith(rel_out + "/") or l[3:].strip('"') == rel_out or l[3:].strip('"').startswith("build/") for l in status.splitlines())
    report = {
        "contract": "docs/P5-RAW-YAML-UNIT3-CONTRACT.md",
        "unit": "3b (dump and compare); 3c (selftest, evidence report) is on hold",
        "repository": {"commit": git("rev-parse", "HEAD"), "clean": clean},
        "evidence": False,
        "evidence_note": "never evidence in Unit 3b: no selftest has been run (Unit 3c), and P_effective is an analysis result, not a production admission (D9)",
        "selftest": None,
        "input": {"sha256": digest, "seed": a.seed, "cases": len(cs.rows), "generator_sha256": cmp_.sha256_file(os.path.join(HERE, "gen_raw_yaml.py")),
                  "undetermined_list_sha256": cmp_.sha256_file(os.path.join(HERE, "undetermined_raw_yaml.json")),
                  "pinned_classification_sha256": cmp_.sha256_file(os.path.join(HERE, "pinned_classification_raw_yaml.json")),
                  "skipped_unrepresentable": len(cs.skipped), "undetermined_constructions": len(cs.undetermined)},
        "implementations": {i: {"files": {f: cmp_.sha256_file(os.path.join(ROOT, f)) for f in SOURCES[i] if os.path.exists(os.path.join(ROOT, f))}, "dump_sha256": cmp_.sha256_file(dumps[i])} for i in cmp_.IMPLS},
        "known_differences": {"file": "tools/parity/known_differences_raw_yaml.json", "sha256": cmp_.sha256_file(KNOWN), "entries": len(known["entries"])},
        "floors": {"file": "tools/parity/floors_raw_yaml.json", "sha256": cmp_.sha256_file(FLOORS), "values": floors},
        "limits": {"case_timeout_seconds": TIMEOUT, "memory_bytes_per_reader_process": MEMORY, "how": "RLIMIT_AS on every reader process; chunks of %d cases, a failed chunk re-run one case at a time; in-process per-case timeout in the Rust and Go dumps" % CHUNK},
        "reader_runs": stats,
        "versions": {"python": platform.python_version(), "pyyaml": __import__("yaml").__version__, "go": version(["go", "version"], env), "rustc": version(["rustc", "--version"], env), "cargo": version(["cargo", "--version"], env)},
        "result": {k: rep[k] for k in ("cases", "green", "red_cases", "pinned", "p_effective", "findings_rust_go", "events", "python", "classes", "red", "errors", "is_red")},
    }
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")
    print("compare: cases %d, green %d, red %d, P_effective %d, rust/go findings %d, errors %d" % (rep["cases"], rep["green"], rep["red_cases"], rep["p_effective"]["count"], len(rep["findings_rust_go"]), len(rep["errors"])))
    print("pinned: %s" % rep["pinned"])
    print("reader runs:", stats)
    for e in rep["errors"][:15]:
        print("ERROR", e)
    for r in rep["red"][:15]:
        print("RED", r["id"], r["why"][:150])
    print("report:", os.path.join(a.out, "report.json"))
    return 1 if rep["is_red"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
