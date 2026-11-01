"""Do the runs whose Merkle key moved only under the corrected model really change outcome?

    python3 lab/measure/verify_moved.py --repo . --work DIR --moved moved.json --out report.json

moved.json is a list of [child commit, test key] the older rule would have reused and the corrected model would rerun
(kernel_backtest.py / the comparison of two extractor builds). For each, the test is run on the parent and on the child, alone
(go test -run, -v). Only a test that passes on the parent and fails on the child is a VERIFIED false reuse of the older rule; a
test that passes on both is a key that moved without a change of outcome (an over-approximation of the model, the cost of
being conservative); a test that fails or cannot run on the parent is INCONCLUSIVE.

Go only. The tests that need the upstream checkouts (CIC_PRIMITIVES_ROOT, CIC_REGISTRY_ROOT) are run with whatever the environment
holds; a test that skips is reported as skipped, never as a pass.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402


def results(wt, names):
    """{test name: PASS|FAIL|SKIP|MISSING} for the tests of the Go module in wt/go, run alone."""
    pattern = "^(" + "|".join(re.escape(n) for n in sorted(names)) + ")$"
    r = bd.run(["go", "test", "-count=1", "-v", "-run", pattern, "."], cwd=os.path.join(wt, "go"),
               env=dict(os.environ, GOFLAGS="-mod=mod", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off"))
    out = {n: "MISSING" for n in names}
    for m in re.finditer(r"^--- (PASS|FAIL|SKIP): (\S+)", r.stdout, re.M):
        if m.group(2) in out:
            out[m.group(2)] = m.group(1)
    if r.returncode and all(v == "MISSING" for v in out.values()):
        out["__build__"] = (r.stdout + r.stderr)[-300:]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--moved", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo, work = os.path.abspath(a.repo), os.path.abspath(a.work)
    cache = json.load(open(os.path.join(work, "cache.json")))
    order = list(cache)
    parent = {order[i + 1]: order[i] for i in range(len(order) - 1)}
    moved = json.load(open(a.moved))
    by_commit = collections.defaultdict(set)
    for c, t in moved:
        by_commit[c].add(t.split(":", 1)[1].rsplit(".", 1)[-1])
    wt = os.path.join(work, "verify-wt")
    rows, verdict = [], collections.Counter()
    try:
        for c, names in sorted(by_commit.items()):
            res = {}
            for label, commit in (("parent", parent[c]), ("child", c)):
                if not os.path.isdir(wt):
                    bd.run(["git", "-C", repo, "worktree", "add", "--detach", wt, commit], check=True)
                bd.run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", commit], check=True)
                res[label] = results(wt, names)
            for n in sorted(names):
                p, ch = res["parent"].get(n), res["child"].get(n)
                v = ("VERIFIED_FAILURE" if (p == "PASS" and ch == "FAIL") else
                     "NO_CHANGE_OF_OUTCOME" if (p == "PASS" and ch == "PASS") else "INCONCLUSIVE")
                verdict[v] += 1
                rows.append({"commit": c[:12], "test": n, "parent": p, "child": ch, "verdict": v})
            if "__build__" in res["parent"] or "__build__" in res["child"]:
                rows.append({"commit": c[:12], "build_error": {k: v.get("__build__") for k, v in res.items()}})
    finally:
        if os.path.isdir(wt):
            bd.run(["git", "-C", repo, "worktree", "remove", "--force", wt])
    report = {"distinct_tests_by_commit": {c[:12]: len(n) for c, n in by_commit.items()}, "runs_moved": len(moved), "verdicts_by_test": dict(verdict), "rows": rows}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
