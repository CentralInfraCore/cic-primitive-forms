"""Mutation-verdict carry-over, as a PREDICTION (report only; nothing is skipped).

    python3 mutplan.py (--prev-graph G0.json | --prev-repo DIR) (--cur-graph G1.json | --cur-repo DIR) --outcomes DIR [--prev-env E] [--out report.json]

Question: a mutant of an earlier FULL run was CaughtMutant. May that verdict stand for the current tree without running it?

Rule (strict; every clause must hold, otherwise the mutant is run):
  1. the earlier verdict is CaughtMutant (Missed, Timeout, Unviable, anything else is run: a survivor or a timeout proves nothing);
  2. the mutant lies inside a function node of the graph, in the earlier and in the current tree (same node id, same file);
  3. the source of that node is unchanged (src_sha256) and so is its Merkle key (nothing it depends on moved);
  4. the tests that reached the node before are still there, still reach it, and each has the same Merkle key (a test that kills a
     mutant is a function of its closure; the closure is unchanged, so the kill stands). A test ADDED since only adds kills: a kill
     cannot be undone by another test. The killer is one of the earlier tests, which cargo-mutants does not name, so all of them must hold.
The test keys stand here for the planner's decisions; in CI the data and environment digests of the planner decide as well (not modelled
in this offline prototype, see the report).

Same tool and flags are checked from env.json when given. Nothing here reads a verdict from the current tree: the current tree only
supplies the graph.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_evidence as be  # noqa: E402
import merkle  # noqa: E402


def load_outcomes(dirs):
    """[(mutant, summary, seconds)] of every cargo-mutants outcomes.json under the directories (baseline excluded)."""
    out = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "**", "outcomes.json"), recursive=True)):
            for o in json.load(open(f))["outcomes"]:
                s = o["scenario"]
                if s == "Baseline":
                    continue
                m = s["Mutant"]
                out.append((m, o["summary"], sum(p.get("duration", 0) for p in o.get("phase_results", []))))
    return out


def index(graph):
    """(nodes by id, keys by id, {node id: set of tests reaching it})."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    keys, _ = merkle.keys(graph)
    reach = {}
    for n in graph["nodes"]:
        if n.get("entry"):
            for i in be.closure(nodes, n["id"]):
                reach.setdefault(i, set()).add(n["id"])
    return nodes, keys, reach


def enclosing(nodes, file, line):
    """The innermost function-like node of `file` that contains `line` (smallest span), or None."""
    best = None
    for n in nodes.values():
        if n.get("file") == file and n.get("line") is not None and n["line"] <= line <= n.get("end_line", n["line"]) and not n.get("test"):
            if best is None or (n["end_line"] - n["line"]) < (best["end_line"] - best["line"]):
                best = n
    return best


def decide(m, summary, prev, cur):
    """(carried?, reason) for one mutant."""
    if summary != "CaughtMutant":
        return False, "NOT_CAUGHT:" + summary
    (pn, pk, pr), (cn, ck, cr) = prev, cur
    line = m["span"]["start"]["line"]
    a = enclosing(pn, m["file"], line)
    if a is None:
        return False, "NO_NODE_BEFORE"
    b = cn.get(a["id"])
    if b is None:
        return False, "NODE_GONE"
    if a.get("src_sha256") != b.get("src_sha256"):
        return False, "CODE_CHANGED"
    if pk.get(a["id"]) != ck.get(a["id"]):
        return False, "DEPENDENCY_CHANGED"
    ta, tb = pr.get(a["id"], set()), cr.get(a["id"], set())
    if not ta:
        return False, "NO_TEST_REACHES"
    if not ta <= tb:
        return False, "A_REACHING_TEST_LOST"
    if any(pk.get(t) != ck.get(t) for t in ta):
        return False, "A_REACHING_TEST_CHANGED"
    return True, "CARRY"


def same_tool(prev_env, cur_env):
    """True only when both env.json files exist and agree on what decides a mutant's verdict: the mutation tool, cargo and rustc (the runner
    image and Python do not). A missing or different one carries nothing: unknown is not equal."""
    if prev_env is None or cur_env is None:
        return False
    pick = lambda e: (e.get("tools"), e.get("cargo"), e.get("rustc"))  # noqa: E731
    return pick(prev_env) == pick(cur_env) and pick(prev_env)[0] is not None


def predict(prev_graph, cur_graph, outcomes, prev_env=None, cur_env=None):
    prev, cur = index(prev_graph), index(cur_graph)
    tool_ok = same_tool(prev_env, cur_env)
    rows = []
    for m, s, secs in outcomes:
        ok, why = decide(m, s, prev, cur)
        if ok and not tool_ok:
            ok, why = False, "TOOL_OR_FLAGS_NOT_THE_SAME"
        rows.append({"mutant": m["name"], "summary": s, "seconds": secs, "carry": ok, "reason": why})
    return rows


def report(rows):
    from collections import Counter
    n = len(rows)
    c = [r for r in rows if r["carry"]]
    secs, csecs = sum(r["seconds"] for r in rows), sum(r["seconds"] for r in c)
    why = Counter(r["reason"] for r in rows)
    lines = ["Mutation carry-over prediction (report only)", "mutants: %d, carried: %d (%.1f%%), seconds of mutant runs: %.0f of %.0f (%.1f%%)"
             % (n, len(c), 100.0 * len(c) / max(n, 1), csecs, secs, 100.0 * csecs / max(secs, 1))]
    lines += ["  %-28s %d" % (k, v) for k, v in sorted(why.items(), key=lambda kv: -kv[1])]
    lines.append("It shows what the rule would carry, not that a carried verdict is right: that is measured against the next real run.")
    return "\n".join(lines)


PIN = re.compile(r"cargo-mutants@(\d+\.\d+\.\d+)")


def env_of_tree(repo, toolchain):
    """The env.json the nightly would write for this tree: the cargo-mutants version p1.yml pins, and the toolchain's cargo and rustc."""
    def ver(tool):
        out = subprocess.run(["rustup", "run", toolchain, tool, "--version"], capture_output=True, text=True).stdout.split()
        return out[1] if len(out) > 1 else None
    pins = set(PIN.findall(open(os.path.join(repo, ".github", "workflows", "p1.yml")).read()))
    return {"cargo": ver("cargo"), "rustc": ver("rustc"), "tools": {"cargo-mutants": pins.pop()} if len(pins) == 1 else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev-graph")
    ap.add_argument("--cur-graph")
    ap.add_argument("--prev-repo", help="checkout of the commit of the earlier run (instead of --prev-graph)")
    ap.add_argument("--cur-repo", help="checkout of this tree (instead of --cur-graph; also gives --cur-env)")
    ap.add_argument("--scipdeps")
    ap.add_argument("--rust-toolchain")
    ap.add_argument("--outcomes", action="append", required=True)
    ap.add_argument("--prev-env", help="env.json of the earlier run (without both env files nothing is carried)")
    ap.add_argument("--cur-env", help="env.json of this run")
    ap.add_argument("--out")
    a = ap.parse_args()
    env = lambda p: json.load(open(p)) if p else None  # noqa: E731
    import rust_graph as rg

    def made(repo):  # the crate is built first: rust-analyzer indexes a built crate differently (plan.py)
        crate = os.path.join(repo, "rust")
        subprocess.run(["rustup", "run", a.rust_toolchain, "cargo", "test", "--locked", "--no-run"], cwd=crate, check=True, capture_output=True)
        return rg.graph(crate, a.scipdeps, a.rust_toolchain)
    pg = made(a.prev_repo) if a.prev_repo else json.load(open(a.prev_graph))
    cg = made(a.cur_repo) if a.cur_repo else json.load(open(a.cur_graph))
    cur_env = env_of_tree(a.cur_repo, a.rust_toolchain) if a.cur_repo and not a.cur_env else env(a.cur_env)
    rows = predict(pg, cg, load_outcomes(a.outcomes), env(a.prev_env), cur_env)
    print(report(rows))
    if a.out:
        json.dump(rows, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
