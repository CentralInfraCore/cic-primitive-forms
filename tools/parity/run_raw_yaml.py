#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One local command for the raw YAML differential, Unit 3b (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, sections 3, 5, 8, 9).

    run_raw_yaml.py [--out DIR] [--seed N] [--rust-target-dir DIR]

Builds the byte-level input (tools/parity/gen_raw_yaml.py, regenerated and compared), runs the Rust and Go dumps and the Python oracle on the SAME bytes, compares (parity, construction,
allowed, mapping, difference from Python) and writes one report. The memory limit (RLIMIT_AS) and the wall-clock limit are ENFORCED on every reader process: a
chunk that dies or overruns is re-run one case at a time, and the case that kills or overruns its process is recorded as CRASH or TIMEOUT, which is red. Unit 3c (the selftest and the
evidence report) is on hold, so this report is never marked as evidence. Not a CI step; no file under .github/workflows is touched.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import compare_raw_yaml as cmp_  # noqa: E402
import gen_raw_yaml as gen  # noqa: E402

KNOWN = os.path.join(HERE, "known_differences_raw_yaml.json")
SUBTYPES = os.path.join(HERE, "pinned_subtypes_raw_yaml.json")
FLOORS = os.path.join(HERE, "floors_raw_yaml.json")
SOURCES = {
    "go": ["go/parse.go", "go/node.go", "go/errors.go", "go/digestmapping.go", "go/parity_raw_yaml_dump_test.go"],
    "rust": ["rust/src/parse.rs", "rust/src/node.rs", "rust/src/error.rs", "rust/src/digest_mapping.rs", "rust/tests/parity_raw_yaml_dump.rs"],
    "python": ["tools/parity/dump_raw_yaml_py.py", "tools/p5/digest_mapping.py"],
}
STARTUP = 5         # seconds a reader process gets to START (to create its output file); it is not part of any case's time, and a reader that does not start stops the run: that is not a verdict on a case
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


def build_binaries(env, rust_target, out_dir):
    go_bin = os.path.join(out_dir, "raw-yaml-go.test")        # a build product of this run, written outside the tree's tracked files
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


def reader_launch(lang, binary):
    """-> a function (inp, outp) -> (cmd, cwd, env_extra) for a reader that is given its files by the environment."""
    def launch(inp, outp):
        if lang == "go":
            cmd, cwd = [binary, "-test.run", "^TestDumpRawYAMLOutcomes$", "-test.count=1"], os.path.join(ROOT, "go")
        else:
            cmd, cwd = [binary, "--exact", "dump_raw_yaml_outcomes", "--test-threads=1"], os.path.join(ROOT, "rust")
        return cmd, cwd, {"PARITY_RAW_YAML_IN": inp, "PARITY_RAW_YAML_OUT": outp}
    return launch


def python_launch(inp, outp):
    return [sys.executable, os.path.join(HERE, "dump_raw_yaml_py.py"), inp, outp], ROOT, {}


def count_lines(path, state):
    """The number of COMPLETE lines in a growing file (state keeps the offset); an unfinished last line is not counted."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return state["n"]
    if size > state["off"]:
        with open(path, "rb") as f:
            f.seek(state["off"])
            data = f.read(size - state["off"])
        state["n"] += data.count(b"\n")
        state["off"] = size
    return state["n"]


def complete_lines(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    parts = data.split(b"\n")
    return [x.decode("utf-8") for x in parts[:-1]]


def run_dump(launch, lines, env, workdir, name="reader"):
    """-> (the dump lines, stats). The reader is a PROCESS that writes one line per case as it goes. This function SUPERVISES it: it kills the whole process group of a reader that does not
    finish a case within the limit (the case is TIMEOUT), and a reader that dies on a case (the memory limit, a crash, a panic) leaves that case CRASH. In both cases the run restarts
    after the culprit with a fresh process, so the work of an overrun case never continues in the background and cannot take CPU or memory from the next one."""
    out, stats = [], {"restarts": 0, "crash": 0, "timeout": 0}
    idx = 0
    while idx < len(lines):
        inp = os.path.join(workdir, "%s-in.jsonl" % name)
        outp = os.path.join(workdir, "%s-out.jsonl" % name)
        with open(inp, "w", encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in lines[idx:]))
        if os.path.exists(outp):
            os.unlink(outp)
        cmd, cwd, extra = launch(inp, outp)
        proc = subprocess.Popen(cmd, cwd=cwd, env=dict(env, **extra), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=limits, start_new_session=True)
        state = {"n": 0, "off": 0}
        began = time.monotonic()
        started = False                       # the reader creates its output file when it is READY, before it reads the first case: that is when the first case's clock starts
        last, seen, killed, never_started = began, 0, False, False
        while proc.poll() is None:
            now = time.monotonic()
            if not started:
                if os.path.exists(outp):
                    started, last = True, now
                elif now - began > STARTUP:
                    os.killpg(proc.pid, signal.SIGKILL)
                    never_started = True
                    break
            else:
                n = count_lines(outp, state)
                if n != seen:
                    seen, last = n, now
                if now - last > TIMEOUT:      # exactly the per-case limit: the start-up time is not part of it and not added to it
                    os.killpg(proc.pid, signal.SIGKILL)
                    killed = True
                    break
            time.sleep(0.02)
        proc.wait()
        if never_started or (not started and not os.path.exists(outp)):
            tail = (proc.stderr.read() or b"")[-300:].decode("utf-8", "replace").replace("\n", " ").strip()
            proc.stderr.close()
            raise SystemExit("a reader did not start (no output file within %s s, exit %s): %s" % (STARTUP, proc.returncode, tail[-200:]))
        proc.wait()
        err = (proc.stderr.read() or b"")[-300:].decode("utf-8", "replace").replace("\n", " ").strip()
        proc.stderr.close()
        done = complete_lines(outp)
        out += done
        idx += len(done)
        if idx >= len(lines):
            break
        culprit = json.loads(lines[idx])["id"]
        stats["restarts"] += 1
        if killed:
            stats["timeout"] += 1
            out.append(json.dumps({"id": culprit, "ok": False, "class": "TIMEOUT", "code": "TIMEOUT", "detail": "killed after %s s without an answer" % TIMEOUT}, sort_keys=True))
        else:
            stats["crash"] += 1
            out.append(json.dumps({"id": culprit, "ok": False, "class": "CRASH", "code": "CRASH", "detail": "exit %s: %s" % (proc.returncode, err[-160:])}, sort_keys=True))
        idx += 1
    return out, stats


def version(cmd, env):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=env).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def git(*args):
    p = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def evidence_of(clean, is_red, selftest):
    """A report is evidence only for a clean tree, a green compare AND a selftest that ran and passed."""
    return bool(clean) and not is_red and selftest is not None and bool(selftest.get("passed"))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "raw-yaml-diff"))
    ap.add_argument("--seed", type=int, default=gen.DEFAULT_SEED)
    ap.add_argument("--rust-target-dir")
    ap.add_argument("--selftest", action="store_true", help="also run the selftest of Unit 3c (tools/parity/selftest_raw_yaml.py)")
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
    go_bin, rust_bin = build_binaries(env, a.rust_target_dir, a.out)
    dumps, stats = {}, {}
    for lang, launch in (("go", reader_launch("go", go_bin)), ("rust", reader_launch("rust", rust_bin)), ("python", python_launch)):
        got, stats[lang] = run_dump(launch, lines, env, a.out, lang)
        dumps[lang] = os.path.join(a.out, "dump-%s.jsonl" % lang)
        with open(dumps[lang], "w", encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in got))
    known = json.load(open(KNOWN, encoding="utf-8"))
    floors = json.load(open(FLOORS, encoding="utf-8"))
    subtypes = json.load(open(SUBTYPES, encoding="utf-8"))
    rep = cmp_.compare(inp, dumps, known, floors, subtypes)
    status = git("status", "--porcelain", "-uall")
    rel_out = os.path.relpath(os.path.abspath(a.out), ROOT)
    clean = status is not None and all(l[3:].strip('"').startswith(rel_out + "/") or l[3:].strip('"') == rel_out or l[3:].strip('"').startswith("build/") for l in status.splitlines())
    report = {
        "contract": "docs/P5-RAW-YAML-UNIT3-CONTRACT.md",
        "unit": "3b (dump and compare), 3c (selftest, evidence rule)",
        "repository": {"commit": git("rev-parse", "HEAD"), "clean": clean},
        "evidence": False,
        "evidence_note": "evidence only for a clean tree, a green compare and a passed selftest; P_effective is an analysis result, not a production admission (D9)",
        "selftest": None,
        "input": {"sha256": digest, "seed": a.seed, "cases": len(cs.rows), "generator_sha256": cmp_.sha256_file(os.path.join(HERE, "gen_raw_yaml.py")),
                  "undetermined_list_sha256": cmp_.sha256_file(os.path.join(HERE, "undetermined_raw_yaml.json")),
                  "pinned_classification_sha256": cmp_.sha256_file(os.path.join(HERE, "pinned_classification_raw_yaml.json")),
                  "skipped_unrepresentable": len(cs.skipped), "undetermined_constructions": len(cs.undetermined)},
        "implementations": {i: {"files": {f: cmp_.sha256_file(os.path.join(ROOT, f)) for f in SOURCES[i] if os.path.exists(os.path.join(ROOT, f))}, "dump_sha256": cmp_.sha256_file(dumps[i]),
                                "reader_binary_sha256": ({"go": cmp_.sha256_file(go_bin), "rust": cmp_.sha256_file(rust_bin)}.get(i))} for i in cmp_.IMPLS},
        "known_differences": {"file": "tools/parity/known_differences_raw_yaml.json", "sha256": cmp_.sha256_file(KNOWN), "entries": len(known["entries"])},
        "pinned_subtypes": {"file": "tools/parity/pinned_subtypes_raw_yaml.json", "sha256": cmp_.sha256_file(SUBTYPES), "declared_non_semantic": len(subtypes)},
        "floors": {"file": "tools/parity/floors_raw_yaml.json", "sha256": cmp_.sha256_file(FLOORS), "values": floors},
        "limits": {"case_timeout_seconds": TIMEOUT, "startup_grace_seconds": STARTUP, "memory_bytes_per_reader_process": MEMORY,
                   "how": "every reader is a process under RLIMIT_AS; the runner supervises it, kills its whole process group when a case takes longer than the limit (TIMEOUT), records a case that kills the process as CRASH, and restarts after it; no timer runs inside the readers"},
        "reader_runs": stats,
        "versions": {"python": platform.python_version(), "pyyaml": __import__("yaml").__version__, "go": version(["go", "version"], env), "rustc": version(["rustc", "--version"], env), "cargo": version(["cargo", "--version"], env)},
        "result": {k: rep[k] for k in ("cases", "green", "red_cases", "pinned", "p_effective", "findings_rust_go", "position_findings", "events", "python", "classes", "red", "errors", "is_red")},
    }
    if a.selftest:
        import selftest_raw_yaml as st
        report["selftest"] = st.run_all(a.out, env, a.rust_target_dir)
        report["selftest_definitions"] = {"file": "tools/parity/selftest_raw_yaml.py", "sha256": cmp_.sha256_file(os.path.join(HERE, "selftest_raw_yaml.py")),
                                          "identifies": "the planted defects, the comparison breakages, the oracle changes and the harness checks of this run (their definitions are in that file)"}
    report["evidence"] = evidence_of(clean, rep["is_red"], report["selftest"])
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
