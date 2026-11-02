"""Observe what the tests do, set the plan against it, and write the evidence of this run for the next one. REPORT ONLY.

    python3 tools/proof-plan/record.py observe-go   --repo . --out go.json
    python3 tools/proof-plan/record.py observe-rust --repo . --rust-toolchain 1.99.1 --out rust.json
    python3 tools/proof-plan/record.py finish --plan DIR --observed go.json --observed rust.json [--prev DIR] --out DIR > report.md

observe-*: run the tests with the commands of the gate (workflow p1) and keep the outcome of each (PASS, FAIL, SKIP) and the time.
finish: for every test the plan decided, compare the decision with what the test did, write `evidence.json` (the evidence of this tree with the
observed verdict: what the next run will find) and the Proof-Aware Execution Report (markdown on stdout).

The comparison is the point of the report-only phase:
  REUSE_ELIGIBLE and the test now fails, the earlier verdict a pass    FALSE_REUSE (the reuse would have been wrong: a failure of the planner)
  REUSE_ELIGIBLE and the test passes again                              CONSISTENT
  a test whose key did not move and whose outcome flipped               marked `unstable` in the evidence: the kernel will not reuse it
  RERUN_REQUIRED / UNVERIFIABLE / NO_EVIDENCE                           SAME_OUTCOME / OUTCOME_CHANGED, to see what the rerun bought
Nothing is skipped here and no gate depends on this.
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan as pl  # noqa: E402
import trace as tr  # noqa: E402


def observe_go(root):
    t0 = time.time()
    r = pl.run(["go", "test", "-race", "-count=1", "-rapid.checks=1000", "-json", "./..."], cwd=os.path.join(root, "go"))
    out = {}
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("Test")
        if t and "/" not in t and e.get("Action") in ("pass", "fail", "skip"):
            out["go:" + t] = {"outcome": e["Action"].upper(), "elapsed": e.get("Elapsed", 0.0)}
    return {"tests": out, "wall_s": round(time.time() - t0, 1), "exit": r.returncode}


def observe_rust(root, tc):
    t0 = time.time()
    env = dict(os.environ)
    b = pl.run(["rustup", "run", tc, "cargo", "test", "--locked", "--no-run", "--message-format=json"], cwd=os.path.join(root, "rust"), env=env)
    exes = []
    for line in b.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
            exes.append((m["target"]["name"].replace("-", "_"), m["executable"]))
    out = {}
    for target, exe in exes:
        t1 = time.time()
        r = pl.run([exe], cwd=os.path.join(root, "rust"), env=env)
        el = time.time() - t1
        for m in re.finditer(r"^test (\S+) \.\.\. (ok|FAILED|ignored)", r.stdout, re.M):
            out["rust:%s::%s" % (target, m.group(1))] = {"outcome": {"ok": "PASS", "FAILED": "FAIL", "ignored": "SKIP"}[m.group(2)], "binary_elapsed": round(el, 2)}
    return {"tests": out, "wall_s": round(time.time() - t0, 1), "exit": b.returncode}


def trace_reads(root, decisions, prev_tests, tc, jobs):
    """{test key: {"reads", "rc", "outside"}} for the tests whose reads are not already known: every test the plan does not call
    REUSE_ELIGIBLE, and every one whose earlier evidence did not record what it read. The reads of a REUSE_ELIGIBLE test are carried on from the
    earlier evidence (the same closure, the same data, the same environment: it reads the same files; the lab made the same assumption)."""
    t0 = time.time()
    want = {d["key"] for d in decisions if d["status"] != "REUSE_ELIGIBLE" or "data_reads" not in prev_tests.get(d["key"], {})}
    env = dict(os.environ)
    work = tempfile.mkdtemp(prefix="trace-")
    gobin = os.path.join(work, "forms.test")
    b = pl.run(["go", "test", "-c", "-o", gobin, "."], cwd=os.path.join(root, "go"))
    todo = []
    if b.returncode == 0:
        todo += tr.go_jobs(root, gobin, dict(env, GOCOVERDIR=work))
    r = pl.run(["rustup", "run", tc, "cargo", "test", "--locked", "--no-run", "--message-format=json"], cwd=os.path.join(root, "rust"), env=env)
    exes = []
    for line in r.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
            exes.append((m["target"]["name"].replace("-", "_"), m["executable"]))
    if r.returncode == 0:
        todo += tr.rust_jobs(root, exes, env)
    todo = [j for j in todo if j[0] in want]
    upstream = tr.upstream_roots(root)

    def one(j):
        key, argv, cwd, e = j
        rc, tail, deps, outside = tr.traced(argv, cwd, root, upstream, e)
        return key, {"reads": deps, "rc": rc, "outside": outside}

    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        out = dict(ex.map(one, todo))
    return {"tests": out, "wall_s": round(time.time() - t0, 1), "wanted": len(want), "traced": len(out),
            "carried": len(decisions) - len(want)}


def classify(decisions, prev_tests, observed, skipped=frozenset()):
    """(rows, summary): for every decided test the kind of agreement between the decision and the observed outcome. Pure.
    A test the job did not run because the plan skipped it (`skipped`) is CARRIED: nothing was observed and the earlier verdict stands."""
    rows, summary = [], {}
    for d in decisions:
        key, status = d["key"], d["status"]
        obs = (observed.get(key) or {}).get("outcome")
        pv = (prev_tests.get(key) or {}).get("verdict")
        if obs is None and key in skipped and status == "REUSE_ELIGIBLE" and pv == "pass":
            kind = "CARRIED"
        elif obs is None:
            kind = "NOT_OBSERVED"
        elif status == "REUSE_ELIGIBLE":
            kind = "CONSISTENT" if (pv == "pass" and obs == "PASS") else ("FALSE_REUSE" if (pv == "pass" and obs == "FAIL") else "OTHER")
        elif pv is None:
            kind = "NO_PREVIOUS"
        else:
            kind = "SAME_OUTCOME" if (pv == "pass") == (obs == "PASS") else "OUTCOME_CHANGED"
        rows.append(dict(d, observed=obs, previous_verdict=pv, kind=kind))
        summary.setdefault(status, {}).setdefault(kind, 0)
        summary[status][kind] += 1
    return rows, summary


def new_evidence(current, prev_tests, observed, sha, trace=None, state=None, skipped=frozenset()):
    """The evidence of this tree with the observed verdicts. A test whose key and environment did not move and whose outcome flipped is
    `unstable` (and stays so): the outcome is then not a function of its closure, and the kernel will not reuse it."""
    out = {}
    for key, e in current.items():
        o = (observed.get(key) or {}).get("outcome")
        p0 = prev_tests.get(key)
        if o is None and key in skipped and p0 is not None and p0.get("verdict") == "pass":
            # skipped by the plan: the earlier verdict and where it was obtained stand (the data it read is the earlier one too)
            ne = dict(e, verdict="pass", provenance=p0.get("provenance", {}), carried=True)
            if "data_reads" in p0 and state is not None:
                ne["data_reads"] = p0["data_reads"]
                ne["opaque_reads"] = list(p0.get("opaque_reads", []))
                ne["data_dependencies_digest"] = pl.reads_digest(state, p0["data_reads"])
            if p0.get("stability") == "unstable":
                ne["stability"] = "unstable"
            out[key] = ne
            continue
        if o is None:
            continue  # not observed: no verdict, no evidence
        ne = dict(e, verdict="pass" if o == "PASS" else "fail", provenance={"evidence_id": "%s %s" % (sha[:12], key), "commit": sha})
        p = prev_tests.get(key)
        # what the test read: measured now, else carried on from the earlier evidence; with it the digest is that of those files in this tree
        t = (trace or {}).get(key)
        if t is not None and state is not None:
            ne["data_reads"] = t["reads"]
            ne["opaque_reads"] = pl.opaque_of(t["reads"], t["rc"] == 0)
            ne["data_dependencies_digest"] = pl.reads_digest(state, t["reads"])
        elif p is not None and "data_reads" in p and state is not None:
            ne["data_reads"] = p["data_reads"]
            ne["opaque_reads"] = list(p.get("opaque_reads", []))
            ne["data_dependencies_digest"] = pl.reads_digest(state, p["data_reads"])
        if p is not None:
            same = p["code_closure_digest"] == e["code_closure_digest"] and p["environment_digest"] == e["environment_digest"] \
                and p["data_dependencies_digest"] == e["data_dependencies_digest"]
            if p.get("stability") == "unstable" or (same and (p.get("verdict") == "pass") != (o == "PASS")):
                ne["stability"] = "unstable"
        out[key] = ne
    return out


def report(plan, rows, summary, observed_meta, timings, false_reuse, trace_meta=None):
    counts = {}
    for d in plan["decisions"]:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    elig = [r for r in rows if r["status"] == "REUSE_ELIGIBLE"]
    would_skip = sum((observed_meta.get("go") or {}).get(r["key"], {}).get("elapsed", 0.0) for r in elig if r["key"].startswith("go:"))
    trace_s = (trace_meta or {}).get("wall_s", 0.0)
    cost = timings.get("total_s", 0.0) + trace_s
    ok, why = pl.authority(plan.get("previous_source") or {"trust": "none"}, plan.get("tooling_trust", "branch"))
    elig_n = counts.get("REUSE_ELIGIBLE", 0)
    L = ["## Proof-Aware Execution Report (report only: nothing was skipped)", "",
         "Evidence compared with: `%s`." % (plan.get("previous_commit") or "none found"), "",
         "**Could authorize skipping: %s.** %s" % ("%d tests" % elig_n if ok else "0 tests",
                                                   "Trusted evidence and trusted planner." if ok else "Reporting only: " + why + "."), "",
         "| | Tests |", "|---|---|"]
    for label, k in (("Could be reused (`REUSE_ELIGIBLE`)", "REUSE_ELIGIBLE"), ("Would be rerun, a known dependency moved (`RERUN_REQUIRED`)", "RERUN_REQUIRED"),
                     ("Would be rerun, the proof cannot support a claim (`UNVERIFIABLE`)", "UNVERIFIABLE"), ("No earlier evidence", "NO_EVIDENCE")):
        L.append("| %s | %d |" % (label, counts.get(k, 0)))
    L += ["", "Everything without a rule of its own (mutation, coverage, fuzzing, parity, lint ...) is EXECUTE: not decided here.", "",
          "### The decisions against what the tests did", "", "| Decision | Observed |", "|---|---|"]
    for st in sorted(summary):
        L.append("| `%s` | %s |" % (st, ", ".join("%s %d" % (k, v) for k, v in sorted(summary[st].items()))))
    reasons = {}
    for d in plan["decisions"]:
        for r_ in d["reasons"]:
            reasons[(d["status"], r_)] = reasons.get((d["status"], r_), 0) + 1
    L += ["", "Reasons: " + ", ".join("`%s` %s %d" % (r_, st, n) for (st, r_), n in sorted(reasons.items()) if st != "REUSE_ELIGIBLE") + "."]
    L += ["", "**False reuse (a reuse that would have been wrong): %d**" % len(false_reuse)]
    for r in false_reuse[:20]:
        L.append("- `%s`" % r["key"])
    L += ["", "### Cost", "",
          "| | seconds |", "|---|---|",
          "| Making the evidence and deciding, and tracing the reads of %d tests (this job's planner) | %.1f |" % ((trace_meta or {}).get("traced", 0), cost),
          "| Test time of the Go tests the planner could have spared (measured per test) | %.1f |" % would_skip,
          "| Net for Go tests (spared minus planner; Rust: no per-test time, not counted) | %.1f |" % (would_skip - cost), "",
          "Planner timings: %s." % json.dumps(timings)]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("observe-go")
    g.add_argument("--repo", default=".")
    g.add_argument("--out", required=True)
    r = sub.add_parser("observe-rust")
    r.add_argument("--repo", default=".")
    r.add_argument("--rust-toolchain", required=True)
    r.add_argument("--out", required=True)
    t = sub.add_parser("trace")
    t.add_argument("--repo", default=".")
    t.add_argument("--plan", required=True)
    t.add_argument("--prev")
    t.add_argument("--rust-toolchain", required=True)
    t.add_argument("--jobs", type=int, default=os.cpu_count())
    t.add_argument("--out", required=True)
    f = sub.add_parser("finish")
    f.add_argument("--repo", default=".")
    f.add_argument("--trace")
    f.add_argument("--plan", required=True)
    f.add_argument("--observed", action="append", required=True, help="may be repeated; a file that is not there is a job that did not run")
    f.add_argument("--prev")
    f.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "observe-go":
        res = observe_go(os.path.abspath(a.repo))
        json.dump(res, open(a.out, "w"))
        print("go: %d tests, %s s, exit %d" % (len(res["tests"]), res["wall_s"], res["exit"]), file=sys.stderr)
        return 0
    if a.cmd == "observe-rust":
        res = observe_rust(os.path.abspath(a.repo), a.rust_toolchain)
        json.dump(res, open(a.out, "w"))
        print("rust: %d tests, %s s, exit %d" % (len(res["tests"]), res["wall_s"], res["exit"]), file=sys.stderr)
        return 0
    if a.cmd == "trace":
        root = os.path.abspath(a.repo)
        plan = json.load(open(os.path.join(a.plan, "plan.json")))
        prev_tests, _ = pl.load_prev(a.prev)
        res = trace_reads(root, plan["decisions"], prev_tests, a.rust_toolchain, a.jobs)
        json.dump(res, open(a.out, "w"))
        print("traced %d of %d wanted tests (%d carried on), %s s" % (res["traced"], res["wanted"], res["carried"], res["wall_s"]), file=sys.stderr)
        return 0
    plan = json.load(open(os.path.join(a.plan, "plan.json")))
    cur = json.load(open(os.path.join(a.plan, "current.json")))
    timings = json.load(open(os.path.join(a.plan, "timings.json")))
    observed, skipped = {}, set()
    for p in a.observed:
        if not os.path.exists(p):
            print("no observation in %s: that job did not run" % p, file=sys.stderr)
            continue
        d = json.load(open(p))
        observed.update(d["tests"])
        skipped |= set(d.get("skipped", []))
    prev_tests, _ = pl.load_prev(a.prev)
    rows, summary = classify(plan["decisions"], prev_tests, observed, skipped)
    false_reuse = [r_ for r_ in rows if r_["kind"] == "FALSE_REUSE"]
    os.makedirs(a.out, exist_ok=True)
    trace = json.load(open(a.trace)) if a.trace else None
    state = pl.TreeState(os.path.abspath(a.repo)) if trace is not None else None
    json.dump({"schema": pl.SCHEMA, "commit": plan["commit"],
               "tests": new_evidence(cur["tests"], prev_tests, observed, plan["commit"], (trace or {}).get("tests"), state, skipped)},
              open(os.path.join(a.out, "evidence.json"), "w"))
    json.dump({"rows": rows, "summary": summary, "false_reuse": false_reuse}, open(os.path.join(a.out, "comparison.json"), "w"))
    sys.stdout.write(report(plan, rows, summary, {"go": observed}, timings, false_reuse,
                            {k: trace[k] for k in ("wall_s", "traced")} if trace else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
