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


def blob_lines(root, pick):
    """`path blob-id` lines of the tracked files `pick` accepts (git ls-files -s)."""
    out = run(["git", "-C", root, "ls-files", "-s"], check=True).stdout.splitlines()
    rows = []
    for line in out:
        meta, path = line.split("\t", 1)
        if pick(path):
            rows.append("%s %s" % (path, meta.split()[1]))
    return rows


def is_data(path):
    return path.startswith(DATA_ROOTS) and not path.endswith(CODE_SUFFIXES)


def data_digest(root):
    """Every tracked file under go/, rust/, testdata/, corpus/ that is not Go or Rust source or a manifest. Coarse on purpose: a change of
    any of them is a change for every test (the older rule of the lab, before data edges): it only ever costs a rerun."""
    return sha(*blob_lines(root, is_data))


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


def current_evidence(root, a, idents):
    """{test key: evidence without verdict} for every Go and Rust test of the tree, and the errors of the extractors."""
    data = data_digest(root)
    env = env_digests(root, a.rust_toolchain)
    res, errors = {}, {}
    r = run([os.path.abspath(a.typeddeps), os.path.join(root, "go")])  # -mod=readonly: the extractor must not rewrite go.mod of the tree it reads
    if r.returncode:
        errors["go"] = r.stderr[-400:]
    else:
        for k, e in summaries(json.loads(r.stdout), *idents["go"], lambda i: "go:" + i.rsplit("/", 1)[-1].rsplit(".", 1)[-1]).items():
            res[k] = dict(e, data_dependencies_digest=data, environment_digest=env["go"], opaque_reads=[])
    try:
        # rust-analyzer indexes a crate that has been built (build script outputs, proc-macro expansions) differently from one that has
        # not: on the same source, 230 of 253 Rust keys differed between the two states. The state is made the same, the built one, before
        # indexing, so that the key of a test does not depend on whether a cache restored target/.
        b = run(["rustup", "run", a.rust_toolchain, "cargo", "test", "--locked", "--no-run"], cwd=os.path.join(root, "rust"))
        if b.returncode:
            raise subprocess.CalledProcessError(b.returncode, "cargo test --no-run", stderr=b.stderr)
        g = rg.graph(os.path.join(root, "rust"), a.scipdeps, a.rust_toolchain)
        for k, e in summaries(g, *idents["rust"], lambda i: "rust:" + runner_key(i)).items():
            res[k] = dict(e, data_dependencies_digest=data, environment_digest=env["rust"], opaque_reads=[])
    except subprocess.CalledProcessError as e:
        errors["rust"] = ((e.stderr or b"")[-400:]).decode("utf-8", "replace") if isinstance(e.stderr, bytes) else str(e.stderr)[-400:]
    return res, errors


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
    cur, errors = current_evidence(root, a, idents)
    t2 = time.time()
    # the Assurance of these builds is MEASURED here (the probes of qualify.py / qualify_rust.py), never declared
    assurance = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {go_id: qgo.qualify(a.typeddeps, ()), rs_id: qrs.qualify(a.scipdeps, a.rust_toolchain)})
    t3 = time.time()
    prev, prev_commit = load_prev(a.prev)
    decisions = decide(prev, cur, assurance)
    json.dump({"schema": SCHEMA, "commit": a.sha, "tests": cur}, open(os.path.join(a.out, "current.json"), "w"))
    json.dump({"schema": "cic-proof-plan/plan/v1", "commit": a.sha, "previous_commit": prev_commit, "extractor_errors": errors,
               "qualified": {k: sorted(v) for k, v in assurance.extractors.items()}, "decisions": decisions},
              open(os.path.join(a.out, "plan.json"), "w"), indent=1)
    json.dump({"identity_s": round(t1 - t0, 1), "evidence_s": round(t2 - t1, 1), "qualification_s": round(t3 - t2, 1),
               "decision_s": round(time.time() - t3, 1), "total_s": round(time.time() - t0, 1)}, open(os.path.join(a.out, "timings.json"), "w"))
    counts = {}
    for d in decisions:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    print("tests %d: %s; previous evidence from %s; extractor errors: %s" % (len(decisions), counts, prev_commit or "none", sorted(errors) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
