"""Are the recorded reads complete? Corrupt a data file and see which tests notice.

    python3 lab/measure/data_soundness.py --wt WORKTREE --data data.json --go-bin forms.test --rust-exes rexes.txt \
        [--sample N --seed S] [--planted-forget PATH] --out soundness.json

The worktree is a checkout of the repository the test binaries were built from (CIC_FORMS_ROOT pointing at it). For every
sampled data file (tracked, not Go or Rust source, under go/, rust/, testdata/, corpus/) the file is overwritten with bytes no
reader accepts and every test runs; then it is restored. A test that passed before and fails now read the file: it must have the
file among its recorded reads, or the record is incomplete (a MISS). For every sampled directory that a test lists, a new file is
added to it, and a failing test must have the directory among its reads.

Half of the sample is drawn from the files some test reads, half from the files no test reads (for those, nothing may fail).
--planted-forget removes a path from every record before checking: the check must then report misses (a planted defect).
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys

DATA_PREFIXES = ("go/", "rust/", "testdata/", "corpus/")
SOURCE_SUFFIXES = (".go", ".rs", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock")
GO_FAIL = re.compile(r"^--- FAIL: (\S+)")
RUST_FAIL = re.compile(r"^test (\S+) \.\.\. FAILED")


def run_all(go_bin, rust_exes, wt, env):
    failed = set()
    if go_bin:
        r = subprocess.run([go_bin, "-test.v", "-test.count=1", "-test.run", "^(Test|Fuzz)"], cwd=os.path.join(wt, "go"),
                           capture_output=True, text=True, errors="replace", env=env)
        for line in r.stdout.splitlines():
            m = GO_FAIL.match(line)
            if m and "/" not in m.group(1):
                failed.add("go:" + m.group(1))
        if r.returncode and not any(k.startswith("go:") for k in failed):
            failed.add("go:<binary failed without a failing test>")
    for line in open(rust_exes).read().splitlines() if rust_exes else ():
        kind, target, exe = line.split(None, 2)
        r = subprocess.run([exe, "--test-threads=8"], cwd=os.path.join(wt, "rust"), capture_output=True, text=True, errors="replace", env=env)
        names = [m.group(1) for m in map(RUST_FAIL.match, r.stdout.splitlines()) if m]
        failed |= {"rust:%s::%s" % (target, n) for n in names}
        if r.returncode and not names:
            failed.add("rust:%s::<binary failed without a failing test>" % target)
    return failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wt", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--go-bin")
    ap.add_argument("--rust-exes")
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--planted-forget")
    ap.add_argument("--include", action="append", default=[], help="a data file to corrupt besides the sample")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    wt = os.path.abspath(a.wt)
    env = dict(os.environ, CIC_FORMS_ROOT=wt, GOCOVERDIR=os.environ.get("TMPDIR", "/tmp"),
               LLVM_PROFILE_FILE=os.path.join(os.environ.get("TMPDIR", "/tmp"), "soundness-%p.profraw"))
    reads = {k: dict(v["reads"]) for k, v in json.load(open(a.data))["tests"].items()}
    if a.planted_forget:
        for r in reads.values():
            r.pop(a.planted_forget, None)

    tracked = subprocess.run(["git", "-C", wt, "ls-files"], capture_output=True, text=True, check=True).stdout.split("\n")
    data = sorted(p for p in tracked if p.startswith(DATA_PREFIXES) and not p.endswith(SOURCE_SUFFIXES))
    read_files = sorted({p for r in reads.values() for p, k in r.items() if k == "path"} & set(data))
    unread = sorted(set(data) - {p for r in reads.values() for p in r})
    dirs = sorted({p for r in reads.values() for p, k in r.items() if k == "dir" and os.path.isdir(os.path.join(wt, p))})
    rnd = random.Random(a.seed)
    sample = rnd.sample(read_files, min(a.sample // 2, len(read_files))) + rnd.sample(unread, min(a.sample // 2, len(unread)))
    sample += [p for p in a.include if p not in sample]
    if a.planted_forget and a.planted_forget not in sample:
        sample.insert(0, a.planted_forget)
    dir_sample = rnd.sample(dirs, min(max(2, a.sample // 5), len(dirs)))

    base = run_all(a.go_bin, a.rust_exes, wt, env)
    if base:
        sys.exit("tests fail before anything is changed: %s" % sorted(base)[:10])
    results, misses = [], 0
    for path in sample:
        full = os.path.join(wt, path)
        with open(full, "rb") as f:
            original = f.read()
        try:
            with open(full, "wb") as f:
                f.write(b"\x00\xff corrupted by data_soundness \x00{[\n")
            failed = run_all(a.go_bin, a.rust_exes, wt, env)
        finally:
            with open(full, "wb") as f:
                f.write(original)
        missed = sorted(t for t in failed if path not in reads.get(t, {}))
        misses += len(missed)
        results.append({"path": path, "read_by": sum(1 for r in reads.values() if path in r), "failed": len(failed),
                        "missed": missed})
        print("%-70s read by %3d, failed %3d, missed %d" % (path[-70:], results[-1]["read_by"], len(failed), len(missed)),
              file=sys.stderr, flush=True)
    for d in dir_sample:
        extra = os.path.join(wt, d, "zz-added-by-data-soundness.yaml")
        try:
            with open(extra, "wb") as f:
                f.write(b"\x00\xff added by data_soundness {[\n")
            failed = run_all(a.go_bin, a.rust_exes, wt, env)
        finally:
            os.unlink(extra)
        missed = sorted(t for t in failed if reads.get(t, {}).get(d) != "dir")
        misses += len(missed)
        results.append({"dir": d, "listed_by": sum(1 for r in reads.values() if r.get(d) == "dir"), "failed": len(failed),
                        "missed": missed})
        print("%-70s listed by %3d, failed %3d, missed %d" % (d[-70:] + "/", results[-1]["listed_by"], len(failed),
                                                              len(missed)), file=sys.stderr, flush=True)
    report = {"data_files": len(data), "read_files": len(read_files), "unread_files": len(unread), "misses": misses,
              "experiments": results, "noticed": sum(1 for r in results if r["failed"])}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "experiments"}))
    if a.planted_forget and not misses:
        sys.exit("the planted defect was not seen")
    if not a.planted_forget and misses:
        sys.exit("the recorded reads missed %d test(s)" % misses)


if __name__ == "__main__":
    main()
