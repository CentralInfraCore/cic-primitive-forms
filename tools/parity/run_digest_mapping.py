#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One local command for the digest mapping differential (docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md, sections 2, 8 and 9).

    run_digest_mapping.py [--out DIR] [--seed N] [--values N] [--pairs N] [--valid N] [--no-selftest] [--allow-dirty]

Builds the input set (the shared cases plus the generated ones, one file, one SHA-256), proves the file is the deterministic output of the generator at the stated parameters,
runs the Python reference, the Go dump and the Rust dump on it, compares (agreement AND construction), runs the selftest, and writes one report. Exit 0 only when every rule holds.
Nothing here is a CI step and no file under .github/workflows is touched. A run on a dirty tree writes the report but marks it `evidence: false`.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import compare_digest_mapping as cmp_  # noqa: E402
import gen_digest_mapping as gen  # noqa: E402

SHARED = os.path.join(ROOT, "testdata", "p5", "digest-mapping.json")
KNOWN = os.path.join(HERE, "known_differences_digest_mapping.json")
FLOORS = os.path.join(HERE, "floors_digest_mapping.json")
SOURCES = {"python": "tools/p5/digest_mapping.py", "go": "go/digestmapping.go", "rust": "rust/src/digest_mapping.rs"}


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shared_cases():
    with open(SHARED, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    return [dict(c, **{"class": "shared"}) for c in cases]


def build_input(seed, values, pairs, valid):
    """-> (text, params). The generated part is the generator's own output; the shared part comes first."""
    shared, generated = shared_cases(), gen.generate(seed, values, pairs, valid)
    ids = [c["id"] for c in shared + generated]
    if len(ids) != len(set(ids)):
        raise SystemExit("an id is used by two cases of the input set")
    return gen.render(shared + generated), {"seed": seed, "values": values, "pairs": pairs, "valid": valid}


def verify_input(path, params):
    """The file on disk must be byte for byte what the parameters give; -> the SHA-256 of it. Raises SystemExit on a difference."""
    want, _ = build_input(params["seed"], params["values"], params["pairs"], params["valid"])
    with open(path, encoding="utf-8") as f:
        have = f.read()
    if have != want:
        raise SystemExit("the input file is not the output of the generator at %s: refusing to run" % params)
    return sha256_text(have)


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


def run(cmd, cwd, env, what):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit("%s failed (exit %d):\n%s" % (what, p.returncode, (p.stdout + p.stderr)[-2000:]))
    return p


def dump_go(tree, inp, out, env):
    e = dict(env, PARITY_DIGEST_MAPPING_IN=inp, PARITY_DIGEST_MAPPING_OUT=out, CIC_FORMS_ROOT=tree)
    run(["go", "test", "-count=1", "-run", "TestDumpDigestMappingOutcomes", "."], os.path.join(tree, "go"), e, "the Go dump")


def dump_rust(tree, inp, out, env, target_dir=None):
    e = dict(env, PARITY_DIGEST_MAPPING_IN=inp, PARITY_DIGEST_MAPPING_OUT=out, CIC_FORMS_ROOT=tree)
    if target_dir:
        e["CARGO_TARGET_DIR"] = target_dir
    run(["cargo", "test", "--test", "parity_digest_mapping_dump", "--", "--exact", "dump_digest_mapping_outcomes"], os.path.join(tree, "rust"), e, "the Rust dump")


def dump_python(inp, out, reference=None):
    cmd = [sys.executable, os.path.join(HERE, "dump_digest_mapping_py.py"), inp, out]
    if reference:
        cmd += ["--reference", reference]
    run(cmd, ROOT, dict(os.environ), "the Python dump")


def version(cmd, env):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=env).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def git(*args):
    p = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def evidence_of(clean, is_red, selftest):
    """A report is evidence only for a clean tree, a green compare AND a selftest that ran and passed. Without a selftest it is a diagnostic success at most."""
    return bool(clean) and not is_red and selftest is not None and bool(selftest.get("passed"))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "digest-mapping-diff"))
    ap.add_argument("--seed", type=int, default=gen.DEFAULT_SEED)
    ap.add_argument("--values", type=int, default=40)
    ap.add_argument("--pairs", type=int, default=400)
    ap.add_argument("--valid", type=int, default=40)
    ap.add_argument("--no-selftest", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--rust-target-dir")
    a = ap.parse_args(argv)
    env = tool_env()
    os.makedirs(a.out, exist_ok=True)
    inp = os.path.join(a.out, "input.jsonl")
    text, params = build_input(a.seed, a.values, a.pairs, a.valid)
    with open(inp, "w", encoding="utf-8") as f:
        f.write(text)
    digest = verify_input(inp, params)
    dumps = {i: os.path.join(a.out, "dump-%s.jsonl" % i) for i in cmp_.IMPLS}
    dump_python(inp, dumps["python"])
    dump_go(ROOT, inp, dumps["go"], env)
    dump_rust(ROOT, inp, dumps["rust"], env, a.rust_target_dir)
    known = json.load(open(KNOWN, encoding="utf-8"))
    floors = json.load(open(FLOORS, encoding="utf-8"))
    rep = cmp_.compare(inp, dumps, known, floors)
    rel_out = os.path.relpath(os.path.abspath(a.out), ROOT)
    status = git("status", "--porcelain", "-uall")
    # the output directory is where this run writes; everything else that differs from HEAD makes the tree dirty
    clean = status is not None and all(l[3:].strip('"').startswith(rel_out + "/") or l[3:].strip('"') == rel_out for l in status.splitlines())
    report = {
        "contract": "docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md",
        "repository": {"commit": git("rev-parse", "HEAD"), "clean": clean},
        "evidence": False,
        "input": {"sha256": digest, "parameters": params, "shared_cases": len(shared_cases()),
                  "generator_sha256": cmp_.sha256_file(os.path.join(HERE, "gen_digest_mapping.py"))},
        "implementations": {i: {"source": SOURCES[i], "source_sha256": cmp_.sha256_file(os.path.join(ROOT, SOURCES[i])), "dump_sha256": cmp_.sha256_file(dumps[i])} for i in cmp_.IMPLS},
        "known_differences": {"file": "tools/parity/known_differences_digest_mapping.json", "sha256": cmp_.sha256_file(KNOWN), "entries": len(known["entries"])},
        "floors": {"file": "tools/parity/floors_digest_mapping.json", "sha256": cmp_.sha256_file(FLOORS), "values": floors},
        "versions": {"python": platform.python_version(), "go": version(["go", "version"], env), "rustc": version(["rustc", "--version"], env), "cargo": version(["cargo", "--version"], env)},
        "commands": {"go": "go test -count=1 -run TestDumpDigestMappingOutcomes . (cwd go/)", "rust": "cargo test --test parity_digest_mapping_dump -- --exact dump_digest_mapping_outcomes (cwd rust/)",
                     "python": "tools/parity/dump_digest_mapping_py.py", "compare": "tools/parity/compare_digest_mapping.py"},
        "result": {k: rep[k] for k in ("cases", "green", "red_cases", "known_differences", "classes", "red", "errors", "is_red")},
        "selftest": None,
    }
    report["selftest_definitions"] = {"file": "tools/parity/selftest_digest_mapping.py", "sha256": cmp_.sha256_file(os.path.join(HERE, "selftest_digest_mapping.py")),
                                      "identifies": "the planted defects, the comparison breakages and the equivalent changes of this run (their definitions are in that file)"}
    sys.stdout.write("compare: cases %d, green %d, red %d, errors %d\n" % (rep["cases"], rep["green"], rep["red_cases"], len(rep["errors"])))
    if not a.no_selftest:
        import selftest_digest_mapping as st
        report["selftest"] = st.run_all(a.out, env, dumps, inp, a.rust_target_dir)
    else:
        report["evidence_note"] = "run with --no-selftest: diagnostic only, never evidence"
    report["evidence"] = evidence_of(clean, rep["is_red"], report["selftest"])
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")
    ok = not rep["is_red"] and (report["selftest"] is None or report["selftest"]["passed"])
    print("report: %s  evidence: %s  tree clean: %s" % (os.path.join(a.out, "report.json"), report["evidence"], clean))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
