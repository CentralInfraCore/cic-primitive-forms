"""Which files does a test read? Run alone under strace (ported from lab/measure/trace_data.py of the branch lab/verification).

    python3 tools/proof-plan/record.py trace --repo . --plan DIR [--prev DIR] --out trace.json     (the driver)

A test depends on a path of the repository when it (or a process it starts) opens it, stats it, checks it for access or reads its link,
whether or not the path exists: a file absent now and present later can change the result. A directory opened as a directory is a dependency on
its listing. A path under a pinned upstream checkout (CIC_PRIMITIVES_ROOT, CIC_REGISTRY_ROOT, upstream/cic-primitive-engine) is a dependency on
the pin file that names its commit, because that is where the repository decides what is there. A path relative to a directory file descriptor is
resolved with `strace -y`; if it cannot be, the test gets the dependency "<unresolved ...>" of kind "opaque", and a test with one is never reused.

Paths outside the repository (the system, the toolchain, temporary files) are counted, not listed.
"""
import os
import re
import subprocess
import tempfile

CALL = re.compile(r'^(?:\d+\s+)?(openat|open|newfstatat|statx|faccessat2?|access|readlinkat|readlink|stat|lstat)\((.*)$')
PATH_ARG = re.compile(r'"((?:[^"\\]|\\.)*)"')
DIRFD = re.compile(r'^\d+<([^>]*)>, ')
UNRESOLVED = "<unresolved: a path relative to a directory fd>"
PINS = {"CIC_PRIMITIVES_ROOT": "corpus/pins.yaml", "CIC_REGISTRY_ROOT": "corpus/pins.yaml"}
ENGINE = ("upstream/cic-primitive-engine", "testdata/p5/engine-pin.txt")
SKIP = (".git" + os.sep, "rust" + os.sep + "target" + os.sep, "tools" + os.sep + "proof-plan" + os.sep)


def upstream_roots(repo):
    roots = {os.path.normpath(os.environ[v]): pin for v, pin in PINS.items() if os.environ.get(v)}
    roots[os.path.normpath(os.path.join(repo, ENGINE[0]))] = ENGINE[1]
    return roots


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
            if rel == ".git" or rel.startswith(SKIP):
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
    """(return code, tail of the output, {path: kind}, number of paths outside)."""
    with tempfile.NamedTemporaryFile(suffix=".strace", delete=False) as f:
        out = f.name
    try:
        res = subprocess.run(["strace", "-f", "-qq", "-y", "-o", out, "-e",
                              "trace=openat,open,newfstatat,statx,faccessat,faccessat2,access,readlinkat,readlink,stat,lstat"] + argv,
                             cwd=cwd, capture_output=True, text=True, env=env)
        deps, outside = parse(out, cwd, repo, upstream)
        return res.returncode, (res.stdout + res.stderr)[-600:], deps, outside
    finally:
        os.unlink(out)


def go_jobs(root, binary, env):
    """[(test key, argv, cwd, env)] for the Go tests of a compiled test binary (`go test -c`)."""
    cwd = os.path.join(root, "go")
    names = subprocess.run([binary, "-test.list", ".*"], cwd=cwd, capture_output=True, text=True, check=True).stdout.split()
    return [("go:" + n, [binary, "-test.run", "^" + re.escape(n) + "$", "-test.count=1"], cwd, env) for n in names if n.startswith(("Test", "Fuzz"))]


def rust_jobs(root, exes, env):
    """[(test key, argv, cwd, env)] for the Rust tests of the test executables [(target, path)]."""
    cwd = os.path.join(root, "rust")
    jobs = []
    for target, exe in exes:
        listing = subprocess.run([exe, "--list", "--format", "terse"], cwd=cwd, capture_output=True, text=True, env=env, check=True).stdout
        for t in (l[: -len(": test")] for l in listing.splitlines() if l.endswith(": test")):
            jobs.append(("rust:%s::%s" % (target, t), [exe, "--exact", t, "--test-threads=1", "-q"], cwd, env))
    return jobs
