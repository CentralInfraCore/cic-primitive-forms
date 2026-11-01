"""The history of main, decided by the verification kernel, for Go and Rust.

    python3 lab/measure/kernel_backtest.py --repo . --work DIR --typeddeps BIN --scipdeps BIN --rust-toolchain 1.99.1 \
        --rev 314475ab14e0 --out report.json

DIR holds cache.json and traces/ (backtest.py, backtest_data.py). For every commit the Go and Rust graphs are made again with the
lab's extractors (not the commit's) and, for every test, the evidence of its closure is built (build_evidence.py). For every commit
pair the kernel (evidence.py) then decides, per test that exists on both sides, whether the earlier result may stand:

  previous evidence   the test on the parent: its Merkle key, the constructs and analysis of its closure, the extractor identity,
                      the data digest of the files it read on the parent, the environment key, its verdict (a pass; a trace says
                      otherwise)
  current state       the same on the child; the files are those the parent's trace names, read on the child

The same pair is also judged by the older rule of RESULTS.md section 4 (equal key, equal environment, no read file changed), so the
two can be set side by side. The kernel adds: construct qualification, completeness of the analysis, extractor identity. It must
never allow what the older rule does not (checked, and the script stops otherwise).

Assumptions, stated: a test with no trace on the parent (the data digest did not change, so no trace was made) had a pass there (main
was green); a test that is new on the child has no previous evidence and is not counted. Environment keys are those of backtest.py.
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402
import qualify as qgo  # noqa: E402
import qualify_rust as qrs  # noqa: E402
import rust_graph as rg  # noqa: E402

DATA_PLACEHOLDER = "-"
# the constructs whose qualification is being decided; the table of the runs by their presence shows what each is worth
WATCHED = {"go": {"go.func.generic", "go.type.alias"}, "rust": {"rs.macro.generated"}}


def summaries(graph, ident, prov, name_of):
    """{cache-style test key: evidence without data/env/verdict} for the entry points of a graph."""
    keys, _ = merkle.keys(graph)
    out = {}
    for n in graph["nodes"]:
        if n.get("entry"):
            e = be.build_evidence(graph, keys, n["id"], ident, prov, DATA_PLACEHOLDER, DATA_PLACEHOLDER)
            e.pop("provenance", None)
            out[name_of(n["id"])] = e
    return out


def commit_graphs(repo, wt, commit, a, idents, langs=("go", "rust")):
    bd.run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", commit], check=True)
    res = {"go": {}, "rust": {}, "errors": {}}
    env = dict(os.environ, GOFLAGS="-mod=mod", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
    if "go" in langs and os.path.isfile(os.path.join(wt, "go", "go.mod")):
        r = bd.run([os.path.abspath(a.typeddeps), os.path.join(wt, "go")], env=env)
        if r.returncode:
            res["errors"]["go"] = r.stderr[-300:]
        else:
            res["go"] = summaries(json.loads(r.stdout), *idents["go"], lambda i: "go:" + i.rsplit("/", 1)[-1])
    if "rust" in langs and os.path.isfile(os.path.join(wt, "rust", "Cargo.toml")):
        try:
            g = rg.graph(os.path.join(wt, "rust"), a.scipdeps, a.rust_toolchain)
            res["rust"] = summaries(g, *idents["rust"], lambda i: "rust:" + i)
        except subprocess.CalledProcessError as e:
            res["errors"]["rust"] = ((e.stderr or b"") if isinstance(e.stderr, bytes) else (e.stderr or ""))[-300:]
            if isinstance(res["errors"]["rust"], bytes):
                res["errors"]["rust"] = res["errors"]["rust"].decode("utf-8", "replace")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--work", required=True)
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--scipdeps", required=True)
    ap.add_argument("--rust-toolchain", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--verdicts", help="shadow_replay.py output: the observed outcome of every Go test; replaces the assumed pass on the parent")
    ap.add_argument("--dump-decisions", help="write every decision as a line: child commit, test, status, reasons")
    a = ap.parse_args()
    repo, work = os.path.abspath(a.repo), os.path.abspath(a.work)
    cache = json.load(open(os.path.join(work, "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]

    tc = rg.toolchain_identity(a.rust_toolchain)
    go_id, go_prov = be.extractor_identity(a.typeddeps, (), name="typeddeps")
    rs_id, rs_prov = be.extractor_identity(a.scipdeps, (), name="scipdeps", chain=(("rust-analyzer", tc["rust-analyzer-path"]),))
    idents = {"go": (go_id, go_prov), "rust": (rs_id, rs_prov)}
    assurance = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {go_id: qgo.qualify(a.typeddeps, ()),
                                                                        rs_id: qrs.qualify(a.scipdeps, a.rust_toolchain)})
    print("qualified: go %d constructs, rust %d constructs" % (len(assurance.extractors[go_id]), len(assurance.extractors[rs_id])),
          file=sys.stderr)

    verdicts = json.load(open(a.verdicts)) if a.verdicts else None
    decisions_out = []
    gpath = os.path.join(work, "kernel-graphs.json")
    graphs = json.load(open(gpath)) if os.path.exists(gpath) else {}
    wt = os.path.join(work, "kernel-wt")
    try:
        for c in commits:
            # a language is made again only if the extractor that made it is not the one of this run (its identity is kept)
            need = tuple(l for l in ("go", "rust") if c not in graphs or graphs[c].get("ids", {}).get(l) != idents[l][0])
            if not need:
                continue
            if not os.path.isdir(wt):
                bd.run(["git", "-C", repo, "worktree", "add", "--detach", wt, c], check=True)
            res = commit_graphs(repo, wt, c, a, idents, need)
            g = graphs.setdefault(c, {"go": {}, "rust": {}, "errors": {}, "ids": {}})
            for l in need:
                g[l] = res[l]
                g["ids"][l] = idents[l][0]
                g["errors"].pop(l, None)
            g["errors"].update(res["errors"])
            json.dump(graphs, open(gpath, "w"))
            print("%s %s go %d rust %d %s" % (c[:7], "+".join(need), len(g["go"]), len(g["rust"]), g["errors"] or ""),
                  file=sys.stderr, flush=True)
    finally:
        if os.path.isdir(wt):
            bd.run(["git", "-C", repo, "worktree", "remove", "--force", wt])

    envd = lambda lang, c: hashlib.sha256((cache[c]["env"][lang] + tc["rustc"] * (lang == "rust")).encode()).hexdigest()
    tally = {lang: collections.Counter() for lang in ("go", "rust")}
    reasons = {lang: collections.Counter() for lang in ("go", "rust")}
    cross = {lang: collections.Counter() for lang in ("go", "rust")}
    lost_missing = {lang: collections.Counter() for lang in ("go", "rust")}  # the same, by the unqualified constructs that are missing
    by_presence = {lang: collections.Counter() for lang in ("go", "rust")}  # runs the older rule reuses, by the watched constructs in the closure
    lost = {lang: collections.Counter() for lang in ("go", "rust")}  # what the older rule would reuse and the kernel does not, by reason
    rows, violations = [], []
    for p, c in zip(commits, commits[1:]):
        data_changed = cache[p]["data"] != cache[c]["data"]
        trace = json.load(open(os.path.join(work, "traces", p + ".json"))) if data_changed and cache[c]["tests"] else None
        row = collections.Counter()
        for lang in ("go", "rust"):
            gp, gc = graphs[p], graphs[c]
            for t, cur_e in gc[lang].items():
                prev_e = gp[lang].get(t)
                if prev_e is None:
                    row["new"] += 1
                    continue
                tally_key = lang
                verdict, opaque_reads, d_prev, d_cur = "pass", [], "-", "-"
                if data_changed:
                    rec = trace["tests"].get(bd.runner_key(t))
                    if rec is None:
                        opaque_reads = ["no trace of the test on the parent"]
                    else:
                        verdict = "pass" if rec["rc"] == 0 else "fail"
                        sp, sc = [], []
                        for path, kind in sorted(rec["reads"].items()):
                            if kind == "opaque":
                                opaque_reads.append(path)
                            else:
                                sp.append((path, json.dumps(bd.content(repo, p, path, kind), default=str)))
                                sc.append((path, json.dumps(bd.content(repo, c, path, kind), default=str)))
                        d_prev, d_cur = [hashlib.sha256(json.dumps(x).encode()).hexdigest() for x in (sp, sc)]
                if verdicts is not None and lang == "go":
                    # the verdict of the parent is what the run said, twice: a pass only if both runs passed; anything else (a failure,
                    # a skip, two different outcomes, a test not seen) is not a pass and is not reused
                    seen = verdicts.get(p, {}).get("go", {}).get(t.split(":", 1)[1].rsplit(".", 1)[-1])
                    verdict = "pass" if seen == ["PASS", "PASS"] else "fail"
                prev = dict(prev_e, verdict=verdict, data_dependencies_digest=d_prev, environment_digest=envd(lang, p),
                            opaque_reads=opaque_reads, provenance={"evidence_id": p[:12] + " " + t})
                cur = dict(cur_e, data_dependencies_digest=d_cur, environment_digest=envd(lang, c), opaque_reads=opaque_reads)
                d = ev.evaluate_reuse(prev, cur, assurance)
                # the older rule on the same keys: equal key, equal environment, no read file changed, not a failure
                old = (prev_e["code_closure_digest"] == cur_e["code_closure_digest"] and envd(lang, p) == envd(lang, c)
                       and d_prev == d_cur and verdict == "pass" and not opaque_reads)
                if a.dump_decisions:
                    decisions_out.append("%s %s %s %s" % (c, t, d.status, ",".join(d.reasons)))
                tally[lang][d.status] += 1
                reasons[lang][(d.status,) + d.reasons] += 1
                cross[lang][(old, d.status)] += 1
                if old:
                    present = (set(prev_e["graph"]["constructs"]) | set(cur_e["graph"]["constructs"])) & WATCHED[lang]
                    opq = bool(prev_e["graph"]["opaque"] or cur_e["graph"]["opaque"])
                    by_presence[lang][(" + ".join(sorted(present)) or "none") + (" + opaque" if opq else "") + " => " + d.status] += 1
                if old and d.status != ev.REUSE_ELIGIBLE:
                    lost[lang][(d.status,) + d.reasons] += 1
                    ident = prev_e["graph"]["extractor"]
                    missing = sorted((set(prev_e["graph"]["constructs"]) | set(cur_e["graph"]["constructs"])) - assurance.extractors.get(ident, frozenset()))
                    lost_missing[lang][" + ".join(missing + [r for r in d.reasons if r != "CONSTRUCT_NOT_VERIFIED"])] += 1
                if d.status == ev.REUSE_ELIGIBLE and not old:
                    violations.append((c, t, d.reasons))
                row[lang + "_" + d.status] += 1
        rows.append(dict(commit=c[:12], subject=cache[c]["subject"], **row))
    if violations:
        sys.exit("the kernel allowed what the older rule does not: %s" % violations[:3])
    report = {"revision": a.rev, "pairs": len(rows),
              "extractors": {"go": go_id, "rust": rs_id},
              "toolchain": {k: tc[k] for k in ("rustc", "rust-analyzer")},
              "assumptions": (["a test new on the child is not counted", "Rust: a test with no trace on the parent had a pass there"] if verdicts is not None else
                              ["a test with no trace on the parent had a pass there", "a test new on the child is not counted"]),
              "verdicts": "observed (shadow_replay.py) for Go" if verdicts is not None else "assumed",
              "decisions": {l: dict(tally[l]) for l in tally},
              "by_reason": {l: {",".join(k): v for k, v in reasons[l].items()} for l in reasons},
              "reusable_by_the_older_rule_not_by_the_kernel": {l: {",".join(k): v for k, v in lost[l].items()} for l in lost},
              "withheld_by_missing_construct": {l: dict(lost_missing[l].most_common()) for l in lost_missing},
              "older_rule_runs_by_watched_constructs": {l: dict(sorted(by_presence[l].items())) for l in by_presence},
              "older_rule_vs_kernel": {l: {"%s/%s" % (str(k[0]).lower(), k[1]): v for k, v in cross[l].items()} for l in cross},
              "rows": rows}
    if a.dump_decisions:
        open(a.dump_decisions, "w").write("\n".join(decisions_out) + "\n")
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
