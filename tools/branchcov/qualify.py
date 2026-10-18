#!/usr/bin/env python3
"""Qualification run of the cic-branch/1 measurement pipeline on the four hand-counted samples (ADR 0009).

    qualify.py [--out DIR] [--llvm]

For each sample (go, go-complex, rust, rust-complex) and for the full and the reduced test set it:
  1. measures with gobranch / rustbranch,
  2. compares the result with the hand-counted golden table, function by function and category by category,
  3. turns it into a cic-coverage-report/1 with the adapter,
and then runs the REAL `tools/coverage_gate.py check` on both languages: the full sets must pass, the reduced sets
must fail with BELOW_THRESHOLD. A second measurement must give byte-identical output. With --llvm (needs the nightly
toolchain and cargo-llvm-cov) the Rust counters are also compared with LLVM's branch regions where LLVM has them.

Needs go, cargo and rustc on PATH. Exit status 0 only if everything above holds.
"""
import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
QS = "spike/coverage-qualification"
SAMPLES = [  # name, scope file, go dir, rust dir
    ("simple", "scope.yaml", "go", "rust"),
    ("complex", "scope-complex.yaml", "go-complex", "rust-complex"),
]
GOLDEN = {
    "go": ("golden.json", "go", "go/sample.go"),
    "go-complex": ("golden-go-complex.json", "go-complex", "go-complex/complex.go"),
    "rust": ("golden.json", "rust", "rust/src/lib.rs"),
    "rust-complex": ("golden-rust-complex.json", "rust-complex", "rust-complex/src/lib.rs"),
}


def sh(cmd, **kw):
    return subprocess.run(cmd, cwd=kw.pop("cwd", ROOT), capture_output=True, text=True, **kw)


def must(r, what):
    if r.returncode != 0:
        print("FAIL: %s\n%s%s" % (what, r.stdout[-1500:], r.stderr[-1500:]))
        sys.exit(1)
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None)
    ap.add_argument("--llvm", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out or tempfile.mkdtemp(prefix="branchcov-"))
    os.makedirs(out, exist_ok=True)
    build = os.path.join(out, "build")
    os.makedirs(build, exist_ok=True)
    env = dict(os.environ)
    must(sh(["go", "build", "-o", os.path.join(build, "gobranch"), "./cmd/gobranch"], cwd=os.path.join(HERE, "go")), "build gobranch")
    must(sh(["cargo", "build", "--release", "--locked"], cwd=os.path.join(HERE, "rust"),
            env=dict(env, CARGO_TARGET_DIR=os.path.join(build, "rust-target"))), "build rustbranch")
    gobranch = os.path.join(build, "gobranch")
    rustbranch = os.path.join(build, "rust-target", "release", "rustbranch")
    commit = must(sh(["git", "rev-parse", "HEAD"]), "git rev-parse").stdout.strip()
    tools = os.path.join(ROOT, QS, "tools-pipeline.yaml")
    ok = True

    def measure(lang, d, mode, dest):
        reduced = mode == "reduced"
        if lang == "go":
            e = dict(env, **({"COV_REDUCED": "1"} if reduced else {}))
            must(sh([gobranch, "run", "-C", os.path.join(ROOT, QS, d), "-o", dest], env=e), "gobranch %s %s" % (d, mode))
        else:
            cmd = [rustbranch, "run", "--dir", os.path.join(ROOT, QS, d), "--out", dest] + (["--", "--features", "reduced"] if reduced else [])
            must(sh(cmd, env=dict(env, CARGO_TARGET_DIR=os.path.join(build, "rust-sample-target"))), "rustbranch %s %s" % (d, mode))

    for name, scope, gdir, rdir in SAMPLES:
        for mode in ("full", "reduced"):
            reports = {}
            for lang, d in (("go", gdir), ("rust", rdir)):
                run = os.path.join(out, "%s-%s-%s.json" % (d, mode, lang))
                measure(lang, d, mode, run)
                gfile, gkey, gsrc = GOLDEN[d]
                g = sh([sys.executable, os.path.join(ROOT, QS, "check_golden.py"), lang, run, os.path.join(ROOT, QS, gsrc),
                        os.path.join(ROOT, QS, gfile), gkey, mode])
                print(g.stdout.strip())
                if g.returncode != 0:
                    print(g.stdout, g.stderr)
                    ok = False
                rep = os.path.join(out, "%s-%s-%s.report.json" % (d, mode, lang))
                must(sh([sys.executable, os.path.join(HERE, "adapter.py"), lang, "--run", run, "--root", ROOT, "--module-dir",
                         "%s/%s" % (QS, d), "--scope", os.path.join(ROOT, QS, scope), "--commit", commit, "--out", rep]),
                     "adapter %s %s" % (d, mode))
                reports[lang] = rep
                if mode == "full" and a.llvm and lang == "rust":
                    raw = os.path.join(out, "%s-llvm.json" % d)
                    cwd = os.path.join(ROOT, QS, d)
                    must(sh(["cargo", "llvm-cov", "clean", "--workspace"], cwd=cwd), "llvm-cov clean")
                    must(sh(["cargo", "llvm-cov", "--json", "--output-path", raw], cwd=cwd,
                            env=dict(env, RUSTFLAGS="-Zcoverage-options=condition", CARGO_TARGET_DIR=os.path.join(build, "llvm-target"))), "llvm-cov")
                    c = sh([sys.executable, os.path.join(HERE, "crosscheck_llvm.py"), run, raw])
                    print("%s vs LLVM: %s" % (d, c.stdout.strip().splitlines()[-1]))
                    ok = ok and c.returncode == 0
            gate = sh([sys.executable, os.path.join(ROOT, "tools", "coverage_gate.py"), "check", "--root", ROOT, "--scope",
                       os.path.join(ROOT, QS, scope), "--tools", tools, "--commit", commit,
                       "--report", "rust=" + reports["rust"], "--report", "go=" + reports["go"]])
            print("gate (%s, %s): rc=%d  %s" % (name, mode, gate.returncode, gate.stdout.strip().splitlines()[-1]))
            want = 0 if mode == "full" else 1
            if gate.returncode != want or (mode == "reduced" and gate.stdout.count("BELOW_THRESHOLD") != 2):
                print(gate.stdout)
                ok = False
    # determinism: a second measurement gives byte-identical output
    for lang, d, mode in (("go", "go-complex", "reduced"), ("rust", "rust-complex", "reduced")):
        first = os.path.join(out, "%s-%s-%s.json" % (d, mode, lang))
        again = os.path.join(out, "again-%s.json" % d)
        measure(lang, d, mode, again)
        same = filecmp.cmp(first, again, shallow=False)
        print("determinism %s: %s" % (d, "identical" if same else "DIFFERENT"))
        ok = ok and same
    print("QUALIFICATION RUN: %s" % ("PASS" if ok else "FAIL"))
    if not a.out:
        shutil.rmtree(out, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
