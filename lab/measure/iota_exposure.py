"""How much of the historical reuse of main rests on Go constants whose dependency the graph does not model (implicit iota)?

    python3 lab/measure/iota_exposure.py --repo . --work DIR --typeddeps BIN --out report.json [--verified verified.json]

DIR holds what backtest.py and backtest_data.py left: cache.json (per commit the Merkle key of every test, the environment and data
digests) and traces/ (which files each test read, on the parent). BIN is the typed Go extractor of the lab with construct reports
(schema cic-lab/typeddeps/v2, `group_sha256`). Nothing here changes the Merkle algorithm or the reuse rule: the tests the data-edge
rule calls reusable are recomputed exactly (the total must be the 24973 of RESULTS.md, or this stops), then looked at again.

For every reusable Go test of every commit pair, on both commit states:

  exposure                the dependency closure of the test reaches a const declared with `go.const.implicit_repeat` or
                          `go.const.iota`. The graph cannot say whether the value of such a const moved, so no evidence of this
                          test can be called valid. (Also: the closure could not be read -> exposed, fail closed.)
  semantic change         an exposed const whose whole `const ( ... )` declaration differs between the parent and the child, while
  candidate               the test's Merkle key is the same. This is the mechanism of the defect, narrowed. It is NOT a wrong
                          decision: adding a constant after the last use changes the group and moves no value.
  verified failure        a candidate that passed on the parent and fails on the child (--verified: the file written by
                          iota_verify.py). Only this is a proven false reuse.

The three are kept apart on purpose. Zero verified failures with many exposed runs is the expected shape: the model is shown to be
vulnerable (lab/testdata/iota), not shown to have been wrong in history. Rust has no iota: its runs are counted as they were.
"""
import argparse
import collections
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import build_evidence as be  # noqa: E402
import merkle  # noqa: E402

EXPOSING = ("go.const.implicit_repeat", "go.const.iota")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def graph_exposure(repo, wt, commit, typeddeps, env):
    """{test key: {"exposed": {node id: group digest}}} for the Go tests of one commit, or {"error": why}."""
    run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", commit], check=True)
    if not os.path.isfile(os.path.join(wt, "go", "go.mod")):
        return {"tests": {}}
    r = run([typeddeps, os.path.join(wt, "go")], env=env)
    if r.returncode:
        return {"error": r.stderr[-300:]}
    g = json.loads(r.stdout)
    if g.get("schema") != be.GRAPH_SCHEMA:
        return {"error": "schema " + str(g.get("schema"))}
    nodes = {n["id"]: n for n in g["nodes"]}
    tests = {}
    for n in g["nodes"]:
        if not n.get("entry"):
            continue
        exposed, unresolved = {}, []
        for i in sorted(be.closure(nodes, n["id"])):
            m = nodes[i]
            c = m.get("constructs")
            if not isinstance(c, list) or m.get("analysis", {}).get("status") != "COMPLETE":
                unresolved.append(i)
            elif any(x in EXPOSING for x in c):
                exposed[i] = {"group": m.get("group_sha256", ""), "constructs": [x for x in c if x in EXPOSING]}
        tests["go:" + n["id"].rsplit("/", 1)[-1]] = {"exposed": exposed, "unresolved": unresolved}
    return {"tests": tests}


def classify(ex_p, ex_c):
    """('unreadable'|'clean'|'exposed'|'candidate', nodes) for a test whose Merkle key is the same on both commits.

    ex_p / ex_c are the entries of graph_exposure for the test on the parent / the child, None where the graph or the test is missing.
    The group digest is the digest of the whole `const ( ... )` declaration: a change of it, with the key equal, is how a value can
    move without the key moving."""
    if ex_p is None or ex_c is None or ex_p["unresolved"] or ex_c["unresolved"]:
        return "unreadable", []
    if not (ex_p["exposed"] or ex_c["exposed"]):
        return "clean", []
    moved = sorted(i for i, v in ex_c["exposed"].items() if i in ex_p["exposed"] and ex_p["exposed"][i]["group"] != v["group"])
    moved += sorted(set(ex_c["exposed"]) ^ set(ex_p["exposed"]))  # the set of exposed consts itself changed
    return ("candidate", moved) if moved else ("exposed", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--work", required=True)
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--verified")
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect-reusable", type=int, help="stop unless the recomputed reusable runs are exactly this many (24973 for RESULTS.md section 4)")
    ap.add_argument("--dump-reusable", help="write the reusable runs as sorted \"commit test\" lines, to compare two states run by run")
    a = ap.parse_args()
    repo, work = os.path.abspath(a.repo), os.path.abspath(a.work)
    cache = json.load(open(os.path.join(work, "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]
    exp_path = os.path.join(work, "exposure.json")
    exposure = json.load(open(exp_path)) if os.path.exists(exp_path) else {}
    verified = json.load(open(a.verified)) if a.verified else {}
    env = dict(os.environ, GOFLAGS="-mod=mod", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
    wt = os.path.join(work, "exposure-wt")
    try:
        for c in commits:
            if c not in exposure:
                if not os.path.isdir(wt):
                    run(["git", "-C", repo, "worktree", "add", "--detach", wt, c], check=True)
                exposure[c] = graph_exposure(repo, wt, c, os.path.abspath(a.typeddeps), env)
                json.dump(exposure, open(exp_path, "w"))
                print("%s %s" % (c[:7], exposure[c].get("error", "%d go tests" % len(exposure[c]["tests"]))), file=sys.stderr, flush=True)
    finally:
        if os.path.isdir(wt):
            run(["git", "-C", repo, "worktree", "remove", "--force", wt])

    tot = {"runs": 0, "reusable": 0, "reusable_go": 0, "reusable_rust": 0, "go_exposed": 0, "go_exposed_unreadable": 0,
           "candidates": 0, "verified_failures": 0}
    tests_seen = {"go_tests": 0, "go_tests_exposed": 0}
    rows, candidates, reusable_runs = [], [], []
    by_const = collections.Counter()
    for p, c in zip(commits, commits[1:]):
        prev, cur = cache[p], cache[c]
        data_changed = prev["data"] != cur["data"]
        trace = None
        if data_changed and cur["tests"]:
            tp = os.path.join(work, "traces", p + ".json")
            if not os.path.exists(tp):
                sys.exit("no trace of %s: run backtest_data.py first" % p)
            trace = json.load(open(tp))
        ep, ec = exposure[p], exposure[c]
        row = {"commit": c[:12], "subject": cur["subject"], "runs": len(cur["tests"]), "reusable": 0, "exposed": 0, "candidates": 0}
        unreadable = set(prev["errors"]) | set(cur["errors"])
        for t, k in cur["tests"].items():
            lang = t.split(":", 1)[0]
            tot["runs"] += 1
            if lang == "go" and "tests" in ec:
                tests_seen["go_tests"] += 1
                e = ec["tests"].get(t)
                tests_seen["go_tests_exposed"] += 1 if (e is None or e["exposed"] or e["unresolved"]) else 0
            same_code = (lang not in unreadable and t in prev["tests"] and prev["tests"][t] == k
                         and prev["env"][lang] == cur["env"][lang])
            if not same_code:
                continue
            if data_changed:
                rec = trace["tests"].get(bd.runner_key(t))
                if (rec is None or rec["rc"] != 0
                        or any(bd.content(repo, p, path, kind) != bd.content(repo, c, path, kind) for path, kind in rec["reads"].items())):
                    continue
            tot["reusable"] += 1
            row["reusable"] += 1
            reusable_runs.append(c + " " + t)
            if lang != "go":
                tot["reusable_rust"] += 1
                continue
            tot["reusable_go"] += 1
            ex_p, ex_c = ep.get("tests", {}).get(t), ec.get("tests", {}).get(t)
            if "tests" not in ep or "tests" not in ec:
                ex_p = None
            kind, moved = classify(ex_p, ex_c)
            if kind == "clean":
                continue
            tot["go_exposed"] += 1
            row["exposed"] += 1
            if kind == "unreadable":
                tot["go_exposed_unreadable"] += 1
                continue
            for i in ex_c["exposed"]:
                by_const[i.rsplit("/", 1)[-1]] += 1
            if kind == "candidate":
                tot["candidates"] += 1
                row["candidates"] += 1
                cand = {"parent": p, "commit": c, "test": t, "nodes": moved}
                v = verified.get(c + " " + t)
                if v is not None:
                    cand["verified"] = v
                    if v.get("parent_pass") and v.get("child_pass") is False:
                        tot["verified_failures"] += 1
                candidates.append(cand)
        rows.append(row)
    if a.dump_reusable:
        open(a.dump_reusable, "w").write("\n".join(sorted(reusable_runs)) + "\n")
    if a.expect_reusable is not None and tot["reusable"] != a.expect_reusable:
        sys.exit("the reusable runs recomputed are %d, not %d: the measurement does not reproduce the backtest" % (tot["reusable"], a.expect_reusable))
    conservative = tot["reusable"] - tot["go_exposed"]
    report = {
        "pairs": len(rows), "totals": tot, "tests_seen": tests_seen,
        "reusable_share_original": round(tot["reusable"] / tot["runs"], 4),
        "reusable_share_conservative": round(conservative / tot["runs"], 4),
        "reusable_conservative": conservative, "lost_to_exposure": tot["go_exposed"],
        "go_reusable_share_exposed": round(tot["go_exposed"] / tot["reusable_go"], 4) if tot["reusable_go"] else None,
        "exposed_runs_by_const_top20": by_const.most_common(20),
        "candidates": candidates, "rows": rows,
    }
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k not in ("rows", "candidates")}, indent=1))
    print("candidates:", len(candidates))


if __name__ == "__main__":
    main()
