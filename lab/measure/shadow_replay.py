"""The outcome of every Go test at every commit of main, observed, twice.

    python3 lab/measure/shadow_replay.py --repo . --work DIR --upstream DIR --rev 314475ab14e0 --out verdicts.json

For every commit of the cache (backtest.py) the Go tests are run with the upstream checkouts at the commits that commit pins
(corpus/pins.yaml), as CI does, twice, with `go test -count=1 -json`. Per test (a top-level test or fuzz target, its seed corpus):
the two outcomes PASS, FAIL or SKIP. A test whose two runs differ is nondeterministic (the property tests draw cases). Nothing is
inferred from "main was green": a verdict is what the run said.

Go only: the Rust tests need a build per commit, which this does not do.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402


def go_run(wt, env):
    """{test name: PASS|FAIL|SKIP} of one run, or {"__build__": text}."""
    r = bd.run(["go", "test", "-count=1", "-json", "."], cwd=os.path.join(wt, "go"), env=env)
    out, build_failed = {}, False
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("Test")
        if t and "/" not in t and e.get("Action") in ("pass", "fail", "skip"):
            out[t] = e["Action"].upper()
        if e.get("Action") == "fail" and not t and not e.get("Test") and "build failed" in (e.get("Output") or ""):
            build_failed = True
    if not out and r.returncode:
        return {"__build__": (r.stdout + r.stderr)[-300:]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--work", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo, work, up = os.path.abspath(a.repo), os.path.abspath(a.work), os.path.abspath(a.upstream)
    cache = json.load(open(os.path.join(work, "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]
    res = json.load(open(a.out)) if os.path.exists(a.out) else {}
    wt = os.path.join(work, "replay-wt")
    try:
        for c in commits:
            if c in res:
                continue
            if not os.path.isdir(wt):
                bd.run(["git", "-C", repo, "worktree", "add", "--detach", wt, c], check=True)
            bd.run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", c], check=True)
            for name, pin in bd.pins(repo, c).items():
                u = os.path.join(up, name)
                if bd.run(["git", "-C", u, "checkout", "-q", "--detach", "--force", pin]).returncode:
                    bd.run(["git", "-C", u, "fetch", "-q", "origin"], check=True)
                    bd.run(["git", "-C", u, "checkout", "-q", "--detach", "--force", pin], check=True)
            env = dict(os.environ, CIC_FORMS_ROOT=wt, CIC_PRIMITIVES_ROOT=os.path.join(up, "cic-primitives"),
                       CIC_REGISTRY_ROOT=os.path.join(up, "cic-schema-registry"), GOFLAGS="-mod=mod", GOTOOLCHAIN="local",
                       GOPROXY="off", GOSUMDB="off")
            entry = {"go": {}}
            if os.path.isfile(os.path.join(wt, "go", "go.mod")):
                r1, r2 = go_run(wt, env), go_run(wt, env)
                if "__build__" in r1 or "__build__" in r2:
                    entry["build_error"] = (r1.get("__build__") or r2.get("__build__"))
                else:
                    entry["go"] = {t: [r1.get(t, "MISSING"), r2.get(t, "MISSING")] for t in sorted(set(r1) | set(r2))}
            res[c] = entry
            json.dump(res, open(a.out, "w"))
            n = entry["go"]
            print("%s tests %d pass %d fail %d skip %d unstable %d %s" % (
                c[:7], len(n), sum(1 for v in n.values() if v == ["PASS", "PASS"]), sum(1 for v in n.values() if "FAIL" in v),
                sum(1 for v in n.values() if "SKIP" in v), sum(1 for v in n.values() if v[0] != v[1]), entry.get("build_error", "")[:60]),
                  file=sys.stderr, flush=True)
    finally:
        if os.path.isdir(wt):
            bd.run(["git", "-C", repo, "worktree", "remove", "--force", wt])


if __name__ == "__main__":
    main()
