"""Which files does each test read? Every Go and Rust test run alone under strace.

    python3 lab/measure/trace_data.py --repo . --go-bin forms.test --go-graph typed.json \
        --rust-exes rexes.txt --rust-graph rtyped.json --out data.json [--jobs N]

A path relative to a directory file descriptor (openat(fd, "name")) is resolved with the fd's path, which strace -y prints; if it
cannot be resolved the test gets the dependency "<unresolved ...>" of kind "opaque", and a test with one is never reused.

A test depends on a path of the repository when it (or a process it starts) opens it, stats it, checks it for access or reads
its link, whether or not the path exists: a file that is absent now and present later can change the result. A directory
opened as a directory is a dependency on its listing. A path under one of the pinned upstream checkouts (CIC_*_ROOT) is a
dependency on the pin file that names the commit (corpus/pins.yaml, testdata/p5/engine-pin.txt), because that is where the
repository decides what is there.

Paths outside the repository and the upstream checkouts (the system, the toolchain, temporary files) are counted, not listed.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile

CALL = re.compile(r'^(?:\d+\s+)?(openat|open|newfstatat|statx|faccessat2?|access|readlinkat|readlink|stat|lstat)\((.*)$')
PATH_ARG = re.compile(r'"((?:[^"\\]|\\.)*)"')
DIRFD = re.compile(r'^\d+<([^>]*)>, ')
UNRESOLVED = "<unresolved: a path relative to a directory fd>"
PINS = {"CIC_PRIMITIVES_ROOT": "corpus/pins.yaml", "CIC_REGISTRY_ROOT": "corpus/pins.yaml"}


def parse(trace_file, cwd, repo, upstream):
    deps, outside = {}, 0
    for line in open(trace_file, errors="replace"):
        m = CALL.match(line)
        if not m:
            continue
        call, rest = m.group(1), m.group(2)
        p = PATH_ARG.search(rest)
        if not p:
            continue
        path = p.group(1).encode().decode("unicode_escape")
        base = cwd
        if call in ("openat", "newfstatat", "statx", "faccessat", "faccessat2", "readlinkat") and not (
                rest.startswith("AT_FDCWD") or path.startswith("/")):
            # relative to a directory fd: strace -y prints the fd's path, N</dir>; without it the path is unknown
            d = DIRFD.match(rest)
            if d is None:
                deps[UNRESOLVED] = "opaque"
                continue
            base = d.group(1)
        full = os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))
        kind = "dir" if call in ("openat", "open") and "O_DIRECTORY" in rest else "path"
        hit = None
        for root, pin in upstream.items():
            if full == root or full.startswith(root + os.sep):
                hit = (pin, "file")
        if hit is None and (full == repo or full.startswith(repo + os.sep)):
            rel = os.path.relpath(full, repo)
            if rel.startswith((".git" + os.sep, "lab" + os.sep)) or rel == ".git":
                continue
            hit = (rel, kind)
        if hit is None:
            outside += 1
            continue
        rel, kind = hit
        if deps.get(rel) != "dir":
            deps[rel] = kind
    return deps, outside


def traced(argv, cwd, repo, upstream, env):
    with tempfile.NamedTemporaryFile(suffix=".strace", delete=False) as f:
        out = f.name
    try:
        res = subprocess.run(["strace", "-f", "-qq", "-y", "-o", out, "-e",
                              "trace=openat,open,newfstatat,statx,faccessat,faccessat2,access,readlinkat,readlink,stat,lstat"]
                             + argv, cwd=cwd, capture_output=True, text=True, env=env)
        deps, outside = parse(out, cwd, repo, upstream)
        return res.returncode, (res.stdout + res.stderr)[-1500:], deps, outside
    finally:
        os.unlink(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--go-bin")
    ap.add_argument("--go-graph")
    ap.add_argument("--rust-exes")
    ap.add_argument("--rust-graph")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    upstream = {os.path.normpath(os.environ[v]): pin for v, pin in PINS.items() if os.environ.get(v)}
    work = tempfile.mkdtemp(prefix="trace-")
    jobs = []  # (test key, node id, argv, cwd, env)

    if a.go_bin:
        graph = json.load(open(a.go_graph))
        entries = {n["id"].rsplit(".", 1)[-1]: n["id"] for n in graph["nodes"] if n.get("entry")}
        gobin = os.path.abspath(a.go_bin)
        cwd = os.path.join(repo, "go")
        env = dict(os.environ, GOCOVERDIR=work)
        names = subprocess.run([gobin, "-test.list", ".*"], cwd=cwd, capture_output=True, text=True, check=True).stdout.split()
        for n in names:
            if n.startswith(("Test", "Fuzz")):
                jobs.append(("go:" + n, entries.get(n), [gobin, "-test.run", "^" + re.escape(n) + "$", "-test.count=1"], cwd, env))
    if a.rust_exes:
        graph = json.load(open(a.rust_graph))
        ids = {n["id"] for n in graph["nodes"] if n.get("entry")}
        cwd = os.path.join(repo, "rust")
        env = dict(os.environ, LLVM_PROFILE_FILE=os.path.join(work, "%p.profraw"))
        for line in open(a.rust_exes).read().splitlines():
            kind, target, exe = line.split(None, 2)
            listing = subprocess.run([exe, "--list", "--format", "terse"], cwd=cwd, capture_output=True, text=True, env=env,
                                     check=True).stdout
            for t in (l[: -len(": test")] for l in listing.splitlines() if l.endswith(": test")):
                desc = t.replace("::", "/") + "()."
                node = desc if kind == "lib" else "tests/%s::%s" % (target, desc)
                jobs.append(("rust:%s::%s" % (target, t), node if node in ids else None,
                             [exe, "--exact", t, "--test-threads=1", "-q"], cwd, env))

    def one(j):
        key, node, argv, cwd, env = j
        rc, tail, deps, outside = traced(argv, cwd, repo, upstream, env)
        return key, {"node": node, "rc": rc, "tail": tail if rc else "", "reads": deps, "outside": outside}

    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        tests = dict(ex.map(one, jobs))
    json.dump({"repo_relative": True, "tests": tests}, open(a.out, "w"), indent=1, sort_keys=True)
    failed = [k for k, v in tests.items() if v["rc"]]
    print("tests %d, failed %d, without a node %d, distinct paths %d" % (
        len(tests), len(failed), sum(1 for v in tests.values() if v["node"] is None),
        len({p for v in tests.values() for p in v["reads"]})))
    if failed:
        sys.exit("failed: %s" % failed[:10])


if __name__ == "__main__":
    main()
