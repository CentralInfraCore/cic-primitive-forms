"""The backtest of mutation verdicts: how many mutants of each commit of main could have taken the parent's verdict?

    python3 lab/measure/backtest_mutants.py --repo . --work DIR --typeddeps BIN --scipdeps BIN --gomutid BIN \
        --rust-toolchain 1.99.1 --out report.json

It builds on backtest.py (DIR/cache.json: the Merkle key of every test) and backtest_data.py (DIR/traces: what each test read on
the parents whose data changed). For every commit it enumerates the mutants without running a test (cargo mutants --list; Go:
go-mutesting with an executor that only records, then the lab's mutid for a stable id) and places each mutant on the graph
of that commit: the smallest declaration that contains its line.

A mutant may take the verdict of the same mutant on the parent when:

* the parent has a mutant with the same id (Rust: file, function, the change as cargo-mutants names it, ordinal; Go: the
  token-diff id of the C0 study: file, unit, changed tokens, ordinal);
* the tests that reach its declaration are the same set on both (a test that does not reach it cannot kill it), and every one
  of them could reuse its own result (the same closure, environment and read data: the test level rule);
* when no test reaches it, the declaration's own Merkle key is the same (it survives on both, for the same reason);
* the mutation tool and its settings are the same (the pins and flags of the p1 workflow's mutation steps).

The Rust count is also weighed by the measured cost of each mutant (tools/mutation-costs.json of the revision measured).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import merkle  # noqa: E402

LISTING = re.compile(r"^[^:]+:\d+:\d+: ")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def node_at(nodes_by_file, file, line):
    best = None
    for n in nodes_by_file.get(file, ()):
        if n["line"] <= line <= n["end_line"] and (best is None or n["end_line"] - n["line"] < best["end_line"] - best["line"]):
            best = n
    return best


def reach(graph, runner_of):
    """For every node, the runner keys of the test entries whose closure contains it."""
    deps = {n["id"]: n["deps"] for n in graph["nodes"]}
    out = {}
    for n in graph["nodes"]:
        if not n.get("entry"):
            continue
        seen, stack = {n["id"]}, [n["id"]]
        while stack:
            for d in deps.get(stack.pop(), ()):
                if d in deps and d not in seen:
                    seen.add(d)
                    stack.append(d)
        for s in seen:
            out.setdefault(s, set()).add(runner_of(n["id"]))
    return out


def tool_key(repo, commit):
    try:
        wf = bd.git(repo, "show", "%s:.github/workflows/p1.yml" % commit)
    except subprocess.CalledProcessError:
        return ""
    lines = [l.strip() for l in wf.splitlines()
             if re.search(r"cargo-mutants@|go-mutesting@|cargo \+\S+ mutants|go-mutesting --exec|--timeout|MUTATE_TIMEOUT", l)]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def rust_mutants(crate, toolchain):
    r = run(["cargo", "+" + toolchain, "mutants", "--list", "--json"], cwd=crate)
    if r.returncode:
        raise RuntimeError(r.stderr[-500:])
    out, count = [], {}
    for m in sorted(json.loads(r.stdout), key=lambda m: (m["file"], m["span"]["start"]["line"], m["span"]["start"]["column"])):
        what = LISTING.sub("", m["name"])
        unit = (m.get("function") or {}).get("function_name", "")
        k = (m["file"], unit, what)
        count[k] = count.get(k, 0) + 1
        cost_key = "%s: %s" % (m["file"], what)
        out.append({"id": "rust-name/1\0%s\0%s\0%s\0%d" % (m["file"], unit, what, count[k]), "file": m["file"],
                    "line": m["span"]["start"]["line"], "cost_key": cost_key})
    # the ordinal of the cost table counts per (file, what), as tools/shard_plan.py keys() does
    per = {}
    for m in out:
        per[m["cost_key"]] = per.get(m["cost_key"], 0) + 1
        m["cost_key"] = "%s#%d" % (m["cost_key"], per[m["cost_key"]])
    return out


def go_mutants(godir, work, gomutid, record_exec):
    rec = os.path.join(work, "gorec")
    run(["rm", "-rf", rec])
    os.makedirs(rec)
    env = dict(os.environ, C0_REC=rec, MUTATE_TIMEOUT="10")
    run(["go-mutesting", "--exec", record_exec, "./..."], cwd=godir, env=env)
    index = os.path.join(rec, "index.tsv")
    if not os.path.exists(index):
        return []
    r = run([gomutid, "ids", "-orig", os.path.join(rec, "orig"), "-mut", os.path.join(rec, "mut"), "-index", index])
    if r.returncode:
        raise RuntimeError(r.stderr[-500:])
    out = []
    for line in r.stdout.splitlines():
        f = line.split("\t")
        out.append({"id": "go-token-diff/1\0" + f[0], "file": f[1], "line": int(f[8])})
    return out


def commit_record(repo, commit, a):
    path = os.path.join(a.work, "mutants", commit + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    wt = os.path.join(a.work, "mut-wt")
    if not os.path.isdir(wt):
        run(["git", "-C", repo, "worktree", "add", "--detach", wt, commit], check=True)
    run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", commit], check=True)
    rec = {"mutants": {}, "errors": {}, "tool": tool_key(repo, commit)}
    record_exec = os.path.join(repo, "tools", "c0", "record_exec.sh")
    for lang in ("go", "rust"):
        root = os.path.join(wt, lang)
        if not os.path.isfile(os.path.join(root, "go.mod" if lang == "go" else "Cargo.toml")):
            continue
        try:
            if lang == "go":
                g = run([a.typeddeps, root])
                if g.returncode:
                    raise RuntimeError(g.stderr[-500:])
                graph = json.loads(g.stdout)
                runner_of = lambda i: "go:" + i.rsplit(".", 1)[-1]
                ms = go_mutants(root, a.work, a.gomutid, record_exec)
            else:
                scip = os.path.join(a.work, "mut-index.scip")
                r = run(["rustup", "run", a.rust_toolchain, "rust-analyzer", "scip", ".", "--output", scip], cwd=root)
                s = run([a.scipdeps, scip, root]) if r.returncode == 0 else r
                if s.returncode:
                    raise RuntimeError(s.stderr[-500:])
                graph = json.loads(s.stdout)
                runner_of = lambda i: bd.runner_key("rust:" + i)
                ms = rust_mutants(root, a.rust_toolchain)
        except RuntimeError as e:
            rec["errors"][lang] = str(e)
            continue
        keys, _ = merkle.keys(graph)
        by_file = {}
        for n in graph["nodes"]:
            by_file.setdefault(n["file"], []).append(n)
        reached = reach(graph, runner_of)
        for m in ms:
            n = node_at(by_file, m["file"], m["line"])
            rec["mutants"][m["id"]] = {
                "lang": lang, "node": n["id"] if n else None, "node_key": keys[n["id"]] if n else None,
                "tests": sorted(reached.get(n["id"], ())) if n else None, "cost_key": m.get("cost_key")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rec, open(path, "w"))
    return rec


def test_reuse(repo, p, c, cache, work):
    """The runner keys of the tests of c that may reuse their result of p (the rule of backtest_data.py)."""
    prev, cur = cache[p], cache[c]
    data_changed = prev["data"] != cur["data"]
    trace = None
    if data_changed:
        tp = os.path.join(work, "traces", p + ".json")
        trace = json.load(open(tp)) if os.path.exists(tp) else {"tests": {}}
    unreadable = set(prev["errors"]) | set(cur["errors"])
    out = set()
    for t, k in cur["tests"].items():
        lang = t.split(":", 1)[0]
        if lang in unreadable or t not in prev["tests"] or prev["tests"][t] != k or prev["env"][lang] != cur["env"][lang]:
            continue
        rk = bd.runner_key(t)
        if data_changed:
            r = trace["tests"].get(rk)
            if r is None or r["rc"] != 0 or any(bd.content(repo, p, path, kind) != bd.content(repo, c, path, kind)
                                                for path, kind in r["reads"].items()):
                continue
        out.add(rk)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--work", required=True)
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--scipdeps", required=True)
    ap.add_argument("--gomutid", required=True)
    ap.add_argument("--rust-toolchain", required=True)
    ap.add_argument("--costs", default="tools/mutation-costs.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="only the last N commits")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    cache = json.load(open(os.path.join(a.work, "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]
    commits = [c for c in commits if cache[c]["tests"]]
    if a.limit:
        commits = commits[-a.limit:]
    costs = json.load(open(os.path.join(repo, a.costs)))["costs"]

    recs = {}
    for c in commits:
        recs[c] = commit_record(repo, c, a)
        print("%s mutants %d %s" % (c[:7], len(recs[c]["mutants"]), recs[c]["errors"] or ""), file=sys.stderr, flush=True)

    totals = {lang: {"mutants": 0, "reusable": 0, "new": 0, "tests_changed": 0, "unplaced": 0, "tool": 0, "unreadable": 0,
                     "cost": 0.0, "cost_reusable": 0.0, "killed_reuse_bound": 0, "costed": 0} for lang in ("go", "rust")}
    rows = []
    for p, c in zip(commits, commits[1:]):
        prev, cur = recs[p], recs[c]
        reusable_tests = test_reuse(repo, p, c, cache, a.work)
        row = {"commit": c[:12], "subject": cache[c]["subject"]}
        for lang in ("go", "rust"):
            t = {"mutants": 0, "reusable": 0, "new": 0, "tests_changed": 0, "unplaced": 0, "tool": 0, "unreadable": 0,
                 "cost": 0.0, "cost_reusable": 0.0, "killed_reuse_bound": 0, "costed": 0}
            for mid, m in cur["mutants"].items():
                if m["lang"] != lang:
                    continue
                t["mutants"] += 1
                cost = costs.get(m["cost_key"], 0.0) if lang == "rust" else 0.0
                t["cost"] += cost
                old = prev["mutants"].get(mid)
                if lang in prev["errors"] or lang in cur["errors"]:
                    why = "unreadable"
                elif old is None:
                    why = "new"
                elif m["node"] is None or old["node"] is None:
                    why = "unplaced"
                elif prev["tool"] != cur["tool"]:
                    why = "tool"
                elif m["tests"] != old["tests"] or not set(m["tests"]) <= reusable_tests:
                    why = "tests_changed"
                elif not m["tests"] and m["node_key"] != old["node_key"]:
                    why = "tests_changed"
                else:
                    why = "reusable"
                t[why] += 1
                if lang == "rust" and m["cost_key"] in costs:
                    t["costed"] += 1
                if why == "reusable":
                    t["cost_reusable"] += cost
                # a mutant killed on the parent stays killed if a test that killed it is the same; which test killed it is not
                # known here, so this counts the mutants that some reusable test of the parent reaches: an upper bound
                if why == "reusable" or (why == "tests_changed" and set(old["tests"]) & reusable_tests):
                    t["killed_reuse_bound"] += 1
            row[lang] = t
            for k in t:
                totals[lang][k] += t[k]
        rows.append(row)
    for lang in totals:
        tt = totals[lang]
        tt["reusable_share"] = round(tt["reusable"] / tt["mutants"], 3) if tt["mutants"] else None
        tt["cost_reusable_share"] = round(tt["cost_reusable"] / tt["cost"], 3) if tt["cost"] else None
        tt["killed_reuse_bound_share"] = round(tt["killed_reuse_bound"] / tt["mutants"], 3) if tt["mutants"] else None
    report = {"pairs": len(rows), "totals": totals, "rows": rows}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps(totals, indent=1))


if __name__ == "__main__":
    main()
