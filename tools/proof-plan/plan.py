"""The proof-aware execution planner, REPORT ONLY: decide, per test, whether an earlier result could stand. Nothing is skipped.

    python3 tools/proof-plan/plan.py --repo . --typeddeps BIN --scipdeps BIN --rust-toolchain 1.99.1 --out DIR [--prev DIR] [--sha SHA]

It makes the evidence of the current tree for every Go and Rust test (the graph of the lab's extractors, the Merkle key of the closure, the
constructs in it and the completeness of the analysis, the digests of the data and of the environment), reads the evidence an earlier run
left (`--prev`, see find_prev.py), and lets the verification kernel (evidence.py) decide each test:

    REUSE_ELIGIBLE   every condition holds; the earlier result could stand
    RERUN_REQUIRED   a known dependency or the environment differs, or the earlier result was not a pass
    UNVERIFIABLE     the proof model cannot support a claim (an unqualified construct, an incomplete analysis, an unknown extractor ...)
    NO_EVIDENCE      there is no earlier evidence for the test (not a kernel decision: the planner has nothing to ask it)

Everything that has no rule of its own (mutation, coverage, fuzzing, parity, linting ...) is EXECUTE: the planner does not touch it.
record.py then sets the decisions against what the tests did and writes the evidence of this run for the next one.

Files in --out: plan.json (the decisions), current.json (the evidence of this tree, without verdicts), timings.json.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402
import qualify as qgo  # noqa: E402
import qualify_rust as qrs  # noqa: E402
import rust_graph as rg  # noqa: E402

SCHEMA = "cic-proof-plan/evidence/v1"
# The commands the gate runs (workflow p1): the evidence of a test belongs to the command that produced it, so it is part of the
# environment digest. A result of another command is another evidence.
GO_TEST_CMD = "go test -race -count=1 -rapid.checks=1000 ./..."
RUST_TEST_CMD = "cargo test --locked"
DATA_ROOTS = ("go/", "rust/", "testdata/", "corpus/")
CODE_SUFFIXES = (".go", ".rs", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode() if isinstance(p, str) else p)
        h.update(b"\0")
    return h.hexdigest()


def tracked_blobs(root):
    """{path: blob id} of the tracked files as they are in the WORKING TREE: the index for a file git sees as unchanged, the hash of the file for one
    modified since (`git ls-files -s` alone would show the content of the index, and a file changed after the last `git add` would look the same)."""
    rows = {}
    for line in run(["git", "-C", root, "ls-files", "-s"], check=True).stdout.splitlines():
        meta, path = line.split("\t", 1)
        rows[path] = meta.split()[1]
    for path in filter(None, run(["git", "-C", root, "ls-files", "-m", "-z"], check=True).stdout.split("\0")):
        if os.path.exists(os.path.join(root, path)):
            rows[path] = run(["git", "-C", root, "hash-object", "--", path], check=True).stdout.strip()
        else:
            rows[path] = "deleted"
    return rows


def blob_lines(root, pick):
    """`path blob-id` lines of the tracked files `pick` accepts."""
    return ["%s %s" % (path, blob) for path, blob in sorted(tracked_blobs(root).items()) if pick(path)]


def is_data(path):
    return path.startswith(DATA_ROOTS) and not path.endswith(CODE_SUFFIXES)


def data_digest(root):
    """Every tracked file under go/, rust/, testdata/, corpus/ that is not Go or Rust source or a manifest. Coarse on purpose: a change of
    any of them is a change for every test (the older rule of the lab, before data edges): it only ever costs a rerun."""
    return sha(*blob_lines(root, is_data))


class TreeState:
    """The content of the tracked files of a tree, by what git holds: a blob id per file, the names in a directory. What a test read is a
    dependency on exactly this. A path git does not track (a build output, a temporary file) has the constant id "-": not a dependency."""

    def __init__(self, root):
        self.blob, self.children = tracked_blobs(root), {}
        for path in self.blob:
            parts = path.split("/")
            for i in range(len(parts)):
                self.children.setdefault("/".join(parts[:i]), set()).add(parts[i])

    def content(self, path, kind):
        if kind == "dir":
            return sha(*sorted(self.children.get(path.rstrip("/"), ())))
        return self.blob.get(path, "-")


def reads_digest(state, reads):
    """The digest of the content, in this tree, of the paths a test read ({path: kind}); equal digests mean no file it read has changed."""
    return sha(*["%s %s %s" % (p, k, state.content(p, k)) for p, k in sorted(reads.items())])


def opaque_of(reads, traced_ok):
    """The reads a decision cannot rest on: an unresolved path, or a traced run that failed (its reads may be incomplete)."""
    out = [p for p, k in reads.items() if k == "opaque"]
    if not traced_ok:
        out.append("the traced run of the test failed: its reads may be incomplete")
    return out


def env_digests(root, tc):
    def tool(*cmd):
        r = run(list(cmd))
        return (r.stdout + r.stderr).strip()

    ident = rg.toolchain_identity(tc)
    go = sha("go", GO_TEST_CMD, tool("go", "version"), *blob_lines(root, lambda p: p in ("go/go.mod", "go/go.sum")))
    rs = sha("rust", RUST_TEST_CMD, ident["rustc"], ident["rust-analyzer"],
             *blob_lines(root, lambda p: p in ("rust/Cargo.toml", "rust/Cargo.lock")))
    return {"go": go, "rust": rs}


def runner_key(node_id):
    """The Rust test id of the graph (`tests/<file>::<name>().` or `<module path>/<name>().`) as libtest names it: `<target>::<name>`."""
    rest = node_id
    target = "cic_primitive_forms"
    if rest.startswith("tests/"):
        target, rest = rest[len("tests/"):].split("::", 1)
    rest = rest.replace("<macro>/", "")
    return "%s::%s" % (target, rest[: -len("().")].replace("/", "::"))


def summaries(graph, ident, prov, name_of):
    keys, _ = merkle.keys(graph)
    out = {}
    for n in graph["nodes"]:
        if n.get("entry"):
            e = be.build_evidence(graph, keys, n["id"], ident, prov, "-", "-")
            e.pop("provenance", None)
            out[name_of(n["id"])] = e
    return out


def with_data(e, key, prev_tests, data, state):
    """The evidence `e` of a test with its data fields. If the earlier evidence of the test recorded what it read, the digest is that of those
    files in this tree (a data edge); if not, the coarse digest of all data files."""
    p = prev_tests.get(key)
    if p is not None and "data_reads" in p:
        return dict(e, data_reads=p["data_reads"], data_dependencies_digest=reads_digest(state, p["data_reads"]),
                    opaque_reads=list(p.get("opaque_reads", [])))
    return dict(e, data_dependencies_digest=data, opaque_reads=[])


def current_evidence(root, a, idents, prev_tests=None):
    """{test key: evidence without verdict} for every Go and Rust test of the tree, and the errors of the extractors."""
    prev_tests = prev_tests or {}
    state = TreeState(root)
    data = data_digest(root)
    env = env_digests(root, a.rust_toolchain)
    res, errors = {}, {}
    r = run([os.path.abspath(a.typeddeps), os.path.join(root, "go")])  # -mod=readonly: the extractor must not rewrite go.mod of the tree it reads
    if r.returncode:
        errors["go"] = r.stderr[-400:]
    else:
        for k, e in summaries(json.loads(r.stdout), *idents["go"], lambda i: "go:" + i.rsplit("/", 1)[-1].rsplit(".", 1)[-1]).items():
            res[k] = with_data(dict(e, environment_digest=env["go"]), k, prev_tests, data, state)
    try:
        # rust-analyzer indexes a crate that has been built (build script outputs, proc-macro expansions) differently from one that has
        # not: on the same source, 230 of 253 Rust keys differed between the two states. The state is made the same, the built one, before
        # indexing, so that the key of a test does not depend on whether a cache restored target/.
        b = run(["rustup", "run", a.rust_toolchain, "cargo", "test", "--locked", "--no-run"], cwd=os.path.join(root, "rust"))
        if b.returncode:
            raise subprocess.CalledProcessError(b.returncode, "cargo test --no-run", stderr=b.stderr)
        g = rg.graph(os.path.join(root, "rust"), a.scipdeps, a.rust_toolchain)
        for k, e in summaries(g, *idents["rust"], lambda i: "rust:" + runner_key(i)).items():
            res[k] = with_data(dict(e, environment_digest=env["rust"]), k, prev_tests, data, state)
    except subprocess.CalledProcessError as e:
        errors["rust"] = ((e.stderr or b"")[-400:]).decode("utf-8", "replace") if isinstance(e.stderr, bytes) else str(e.stderr)[-400:]
    return res, errors


PROBE_FILES = ("evidence.py", "build_evidence.py", "merkle.py", "qualify.py", "qualify_rust.py", "rust_graph.py", "repro_rust_dispatch.py")


def tool_version(*cmd):
    """The first line of a tool's version, "unavailable" if it is not there: a cache made with another answer is simply not used."""
    try:
        return run(list(cmd)).stdout.strip()
    except OSError:
        return "unavailable"


def probe_digest():
    """The digest of everything a qualification is a function of besides the binaries: the kernel, the scripts that run the probes and
    every fixture of testdata/. A change of any of them is another qualification."""
    here = os.path.dirname(os.path.abspath(__file__))
    parts = []
    for f in PROBE_FILES:
        parts.append(f + " " + sha(open(os.path.join(here, f), "rb").read()))
    for base, dirs, files in os.walk(os.path.join(here, "testdata")):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(base, f)
            parts.append(os.path.relpath(full, here) + " " + sha(open(full, "rb").read()))
    return sha(*parts)


def qualify_cached(a, idents, tc, cache_file):
    """(assurance extractors {identity: frozenset}, seconds, hit): the qualification of these two builds, from `cache_file` if it was made for
    exactly these identities, probes and toolchains, else measured (and written there). The cache is never trusted for more than that: a file
    for other binaries or other probes is ignored, whatever key the CI cache restored it under."""
    go_id, rs_id = idents["go"][0], idents["rust"][0]
    want = {"go": go_id, "rust": rs_id, "probes": probe_digest(), "toolchain": [tc["rustc"], tc["rust-analyzer"]],
            "go_version": tool_version("go", "version")}
    t0 = time.time()
    if cache_file and os.path.exists(cache_file):
        try:
            d = json.load(open(cache_file))
            if d.get("for") == want:
                return {k: frozenset(v) for k, v in d["qualified"].items()}, time.time() - t0, True
        except (ValueError, KeyError):
            pass
    q = {go_id: qgo.qualify(a.typeddeps, ()), rs_id: qrs.qualify(a.scipdeps, a.rust_toolchain)}
    if cache_file:
        os.makedirs(os.path.dirname(os.path.abspath(cache_file)), exist_ok=True)
        json.dump({"for": want, "qualified": {k: sorted(v) for k, v in q.items()}}, open(cache_file, "w"), indent=1)
    return q, time.time() - t0, False


def decide(prev_tests, cur_tests, assurance):
    """[{key, status, reasons}] for every test of the current tree. Pure: no file, no git, no process."""
    out = []
    for key in sorted(cur_tests):
        prev = prev_tests.get(key)
        if prev is None:
            out.append({"key": key, "status": "NO_EVIDENCE", "reasons": ["NO_PREVIOUS_EVIDENCE"]})
            continue
        d = ev.evaluate_reuse(prev, cur_tests[key], assurance)
        out.append({"key": key, "status": d.status, "reasons": list(d.reasons)})
    return out


def load_source(path):
    """Where the earlier evidence came from ({branch, event, trust, run_id}); trust "none" if it is unknown, which is the least."""
    try:
        return json.load(open(os.path.join(path, "source.json")))
    except (OSError, ValueError, TypeError):
        return {"trust": "none"}


def authority(source, tooling_trust):
    """(True, "") if a decision made here could authorize skipping work, else (False, why). Both the evidence and the planner that read it
    must come from main: evidence a pull request's own earlier run left is unauthenticated, and a planner in the pull request can make any
    evidence it likes. Reporting is all either is good for."""
    why = []
    if source.get("trust") != "main":
        why.append("the earlier evidence is %s (branch %s)" % ("from no run" if source.get("trust") == "none" else "not from a push to main",
                                                              source.get("branch") or "-"))
    if tooling_trust != "main":
        why.append("the planner is the one of the branch being tested, not the one of main")
    return (not why), "; ".join(why)


def load_prev(path):
    if not path:
        return {}, None
    f = os.path.join(path, "evidence.json")
    if not os.path.exists(f):
        return {}, None
    d = json.load(open(f))
    if d.get("schema") != SCHEMA:
        return {}, None  # another format is no evidence
    return d["tests"], d.get("commit")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--scipdeps", required=True)
    ap.add_argument("--rust-toolchain", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev")
    ap.add_argument("--tooling-trust", default="branch", choices=["main", "branch"],
                    help="where this planner came from: main (the base of the pull request, or a push to main) or the branch under test")
    ap.add_argument("--qual-cache", help="a file: the qualification of exactly these builds and probes is read from it, or measured and written to it")
    ap.add_argument("--sha", default="")
    a = ap.parse_args()
    root = os.path.abspath(a.repo)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    tc = rg.toolchain_identity(a.rust_toolchain)
    go_id, go_prov = be.extractor_identity(a.typeddeps, (), name="typeddeps")
    rs_id, rs_prov = be.extractor_identity(a.scipdeps, (), name="scipdeps", chain=(("rust-analyzer", tc["rust-analyzer-path"]),))
    idents = {"go": (go_id, go_prov), "rust": (rs_id, rs_prov)}
    t1 = time.time()
    prev, prev_commit = load_prev(a.prev)
    cur, errors = current_evidence(root, a, idents, prev)
    t2 = time.time()
    # the Assurance of these builds is MEASURED here (the probes of qualify.py / qualify_rust.py), never declared
    qualified, _q_s, q_hit = qualify_cached(a, idents, tc, a.qual_cache)
    assurance = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], qualified)
    t3 = time.time()
    decisions = decide(prev, cur, assurance)
    json.dump({"schema": SCHEMA, "commit": a.sha, "tests": cur}, open(os.path.join(a.out, "current.json"), "w"))
    source = load_source(a.prev) if a.prev else {"trust": "none"}
    json.dump({"schema": "cic-proof-plan/plan/v1", "commit": a.sha, "previous_commit": prev_commit, "previous_source": source,
               "tooling_trust": a.tooling_trust, "extractor_errors": errors,
               "qualified": {k: sorted(v) for k, v in assurance.extractors.items()}, "decisions": decisions},
              open(os.path.join(a.out, "plan.json"), "w"), indent=1)
    json.dump({"identity_s": round(t1 - t0, 1), "evidence_s": round(t2 - t1, 1), "qualification_s": round(t3 - t2, 1), "qualification_cached": q_hit,
               "decision_s": round(time.time() - t3, 1), "total_s": round(time.time() - t0, 1)}, open(os.path.join(a.out, "timings.json"), "w"))
    counts = {}
    for d in decisions:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    print("tests %d: %s; previous evidence from %s; extractor errors: %s; qualification %s" % (
        len(decisions), counts, prev_commit or "none", sorted(errors) or "none", "from the cache" if q_hit else "measured"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
