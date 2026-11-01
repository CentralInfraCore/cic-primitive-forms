"""Injected semantic changes against the decisions of the kernel: is a test that fails on a mutant ever called REUSE_ELIGIBLE?

    python3 lab/measure/mutant_shadow.py --repo . --typeddeps BIN --upstream DIR --out report.json \
        [--general 300] [--seed 1] [--workers 8] [--extractor-arg=-planted-... ...] [--trust-all]

The history of main is green at every commit, so it cannot show a wrong decision (RESULTS.md section 10.3). A mutant is a state in
which tests fail on purpose. For the tree at HEAD (the baseline: every test passes) and for each mutant of the Go product code:

  1. the whole Go suite is run on the mutant (go test -json); K = the tests that passed on the baseline and fail on the mutant;
  2. the kernel decides every test for the pair baseline -> mutant, from the evidence of the extractor (keys, constructs, analysis),
     with the Assurance measured for this build (qualify.py), the previous verdict observed, nothing else changed;
  3. a test in K called REUSE_ELIGIBLE is a FALSE REUSE: the earlier pass would have stood for a state in which the test fails.

A mutant that does not compile, or whose run times out, is dropped. A mutant no test fails on (a survivor) says nothing about the
decisions and is only counted. The operators are textual and applied to code outside strings and comments:
  general   == != < <= > >= && || true false + -, an integer literal +1, a string literal gets a suffix
  targeted  the blind spots found so far: an iota expression, a blank constant inserted into an iota group (it shifts the values after
            it), the strings of String() and Error() methods

--extractor-arg runs the extractor with planted flags and --trust-all gives it an Assurance that vouches for everything it reports: the
control that shows the mutant set CAN find the defects fixed earlier (implicit iota, dispatch). Without them the numbers are those of
the build as it is, qualified by its probes.
"""
import argparse
import collections
import concurrent.futures
import json
import os
import random
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402
import qualify as q  # noqa: E402

STR = re.compile(r'"(?:\\.|[^"\\])*"')


def mask(line, state):
    """The line with strings, runes and comments blanked (same length); state = inside a raw string across lines."""
    out, i, n = [], 0, len(line)
    raw = state["raw"]
    while i < n:
        c = line[i]
        if raw:
            out.append(" ")
            if c == "`":
                raw = False
            i += 1
        elif c == "`":
            raw = True
            out.append(" ")
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 2 if line[j] == "\\" else 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = j + 1
        elif c == "'":
            j = i + 1
            while j < n and line[j] != "'":
                j += 2 if line[j] == "\\" else 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = j + 1
        elif c == "/" and i + 1 < n and line[i + 1] == "/":
            out.append(" " * (n - i))
            i = n
        else:
            out.append(c)
            i += 1
    state["raw"] = raw
    return "".join(out)[:n].ljust(n)


SWAPS = ((" == ", " != "), (" != ", " == "), (" < ", " <= "), (" <= ", " < "), (" > ", " >= "), (" >= ", " > "),
         (" && ", " || "), (" || ", " && "), (" + ", " - "), (" - ", " + "))


def mutants_of(path, text):
    """[(operator, line number, mutated text of the whole file)] for one product file."""
    lines = text.split("\n")
    state = {"raw": False}
    masked = [mask(l, state) for l in lines]
    out = []

    def put(op, i, new_line, insert_after=None):
        new = list(lines)
        new[i] = new_line
        if insert_after is not None:
            new.insert(i + 1, insert_after)
        out.append((op, i + 1, "\n".join(new)))

    in_method, depth = False, 0
    for i, (l, m) in enumerate(zip(lines, masked)):
        s = l.strip()
        if s.startswith(("//", "package ", "import ", "func ")) and not re.match(r"func \([^)]*\) (String|Error)\(\)", s):
            pass
        for a, b in SWAPS:
            k = m.find(a)
            if k >= 0 and not s.startswith(("case ", "//")):
                put("general:" + a.strip() + "->" + b.strip(), i, l[:k] + b + l[k + len(a):])
        for lit, rep in (("true", "false"), ("false", "true")):
            mm = re.search(r"\b%s\b" % lit, m)
            if mm:
                put("general:%s->%s" % (lit, rep), i, l[:mm.start()] + rep + l[mm.end():])
        mm = re.search(r"(?<![\w.\[\]])[0-9]+(?![\w.])", m)
        if mm and not s.startswith(("case ", "const ", "package ")):
            put("general:int+1", i, l[:mm.start()] + str(int(mm.group()) + 1) + l[mm.end():])
        if not s.startswith(("//", "import ", "package ")) and "`" not in l and "//go:" not in l:
            ms = STR.search(l)
            if ms and len(ms.group()) > 2:
                put("general:string+suffix", i, l[:ms.end() - 1] + "~" + l[ms.end() - 1:])
        # targeted: iota
        if "iota" in m:
            put("targeted:iota+1", i, re.sub(r"\biota\b", "iota + 1", l, count=1))
            if re.match(r"\t[A-Za-z_]\w*(\s+\w+)?\s*=\s*iota\b", l):
                put("targeted:blank-const-after-iota", i, l, insert_after="\t_")
        # targeted: the strings of String() and Error()
        if re.match(r"func \([^)]*\) (String|Error)\(\)", s):
            in_method, depth = True, 0
        if in_method:
            depth += m.count("{") - m.count("}")
            for ms in STR.finditer(l):
                if len(ms.group()) > 2:
                    put("targeted:String/Error literal", i, l[:ms.end() - 1] + "~" + l[ms.end() - 1:])
                    break
            if depth <= 0 and "{" in m + "".join(masked[max(i - 1, 0):i]):
                in_method = False
    return out


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def outcomes(wt, env, timeout=120, only=None):
    cmd = ["go", "test", "-count=1", "-json"] + (["-run", "^(" + "|".join(re.escape(n) for n in sorted(only)) + ")$"] if only else []) + ["."]
    try:
        r = run(cmd, cwd=os.path.join(wt, "go"), env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    out = {}
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("Test")
        if t and "/" not in t and e.get("Action") in ("pass", "fail", "skip"):
            out[t] = e["Action"].upper()
    return out if out else None


def observe(wt, env, wanted):
    """(outcomes, number of rounds, tests still without an outcome). A panic or a fatal error ends the test binary, and the tests after it
    never run; those are run again, alone, until each has an outcome (the test that panics gets its FAIL in the round it runs in)."""
    out = outcomes(wt, env)
    if out is None:
        return None, 0, set()
    rounds = 1
    missing = {t for t in wanted if t not in out}
    while missing and rounds < 40:
        more = outcomes(wt, env, only=missing)
        rounds += 1
        if not more:
            break
        got = {t: v for t, v in more.items() if t in missing}
        if not got:
            break
        out.update(got)
        missing = {t for t in wanted if t not in out}
    return out, rounds, missing


def summaries(graph, ident, prov):
    keys, _ = merkle.keys(graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    out = {}
    for n in graph["nodes"]:
        if n.get("entry"):
            e = be.build_evidence(graph, keys, n["id"], ident, prov, "-", "-")
            e.pop("provenance", None)
            e["_closure_nodes"] = len(be.closure(nodes, n["id"]))
            out[n["id"].rsplit("/", 1)[-1].rsplit(".", 1)[-1]] = e
    return out


def extract(wt, a, env):
    r = run([os.path.abspath(a.typeddeps), *a.extractor_arg, os.path.join(wt, "go")], env=env)
    if r.returncode:
        return None
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--general", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--extractor-arg", action="append", default=[])
    ap.add_argument("--trust-all", action="store_true")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    work = os.path.join(os.path.dirname(os.path.abspath(a.out)), "mutant-shadow-wt")
    os.makedirs(work, exist_ok=True)
    head = bd.git(repo, "rev-parse", "HEAD").strip()
    up = os.path.abspath(a.upstream)
    for name, pin in bd.pins(repo, head).items():
        if run(["git", "-C", os.path.join(up, name), "checkout", "-q", "--detach", "--force", pin]).returncode:
            run(["git", "-C", os.path.join(up, name), "fetch", "-q", "origin"])
            run(["git", "-C", os.path.join(up, name), "checkout", "-q", "--detach", "--force", pin])
    base_env = dict(os.environ, CIC_PRIMITIVES_ROOT=os.path.join(up, "cic-primitives"),
                    CIC_REGISTRY_ROOT=os.path.join(up, "cic-schema-registry"), GOFLAGS="-mod=mod", GOTOOLCHAIN="local", GOPROXY="off",
                    GOSUMDB="off")
    ident, prov = be.extractor_identity(a.typeddeps, tuple(a.extractor_arg), name="typeddeps")
    # the worktrees, one per worker
    wts = []
    for k in range(a.workers):
        wt = os.path.join(work, "wt%d" % k)
        if not os.path.isdir(wt):
            run(["git", "-C", repo, "worktree", "add", "-q", "--detach", wt, head], check=True)
        wts.append(wt)
    try:
        env0 = dict(base_env, CIC_FORMS_ROOT=wts[0])
        base_out = outcomes(wts[0], env0)
        base_graph = extract(wts[0], a, env0)
        passing = {t for t, v in (base_out or {}).items() if v == "PASS"}
        if not base_out or not base_graph:
            sys.exit("the baseline does not run")
        base_ev = summaries(base_graph, ident, prov)
        print("baseline: %d tests, %d pass, %d skip, %d fail" % (len(base_out), len(passing), sum(v == "SKIP" for v in base_out.values()),
                                                                  sum(v == "FAIL" for v in base_out.values())), file=sys.stderr)
        if a.trust_all:
            constructs = set()
            for e in base_ev.values():
                constructs |= set(e["graph"]["constructs"])
            verified = frozenset(constructs | q.BASE | q.CONST_GROUP | {"go.type.alias", "go.func.generic", "go.type.generic"})
        else:
            verified = q.qualify(a.typeddeps, tuple(a.extractor_arg))
        assurance = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {ident: verified})

        # the candidate mutants
        files = sorted(f for f in os.listdir(os.path.join(repo, "go")) if f.endswith(".go") and not f.endswith("_test.go"))
        pool = []
        for f in files:
            text = open(os.path.join(repo, "go", f)).read()
            for op, line, new in mutants_of(f, text):
                pool.append({"file": f, "line": line, "op": op, "text": new})
        targeted = [m for m in pool if m["op"].startswith("targeted")]
        general = [m for m in pool if m["op"].startswith("general")]
        random.Random(a.seed).shuffle(general)
        chosen = targeted + general[: a.general]
        print("candidates: %d targeted, %d general (of %d); running %d" % (len(targeted), len(general), len(general), len(chosen)),
              file=sys.stderr, flush=True)

        def one(job):
            idx, m = job
            wt = wts[idx % a.workers]  # one job per worktree at a time: the pool below is keyed by worker
            return idx, m

        lock_free = collections.deque(wts)
        import threading
        lock = threading.Lock()

        def work_one(m):
            with lock:
                wt = lock_free.popleft()
            try:
                path = os.path.join(wt, "go", m["file"])
                orig = open(path).read()
                open(path, "w").write(m["text"])
                env = dict(base_env, CIC_FORMS_ROOT=wt)
                try:
                    res, rounds, missing1 = observe(wt, env, passing)
                    if res is None:
                        return dict(m, text=None, status="dropped")
                    first = sorted(t for t in passing if res.get(t) == "FAIL")
                    res2 = res
                    if first:  # a failure is observed twice
                        res2, _, _ = observe(wt, env, passing)
                        if res2 is None:
                            return dict(m, text=None, status="dropped")
                    graph = extract(wt, a, env)
                    if graph is None:
                        return dict(m, text=None, status="dropped")
                finally:
                    open(path, "w").write(orig)
                cur_ev = summaries(graph, ident, prov)
                killed = sorted(t for t in passing if res.get(t) == "FAIL" and res2.get(t) == "FAIL")
                unstable = sorted(t for t in passing if (res.get(t) == "FAIL") != (res2.get(t) == "FAIL"))
                passed = sum(1 for t in passing if res.get(t) == "PASS" and res2.get(t) == "PASS")
                other = len(passing) - len(killed) - len(unstable) - passed
                dec = {}
                for t in passing:
                    if t not in cur_ev or t not in base_ev:
                        dec[t] = ("UNVERIFIABLE", ("NO_EVIDENCE",))
                        continue
                    prev = dict(base_ev[t], verdict="pass", data_dependencies_digest="-", environment_digest="-", opaque_reads=[])
                    cur = dict(cur_ev[t], data_dependencies_digest="-", environment_digest="-", opaque_reads=[])
                    d = ev.evaluate_reuse(prev, cur, assurance)
                    dec[t] = (d.status, d.reasons)
                false = [t for t in killed if dec[t][0] == ev.REUSE_ELIGIBLE]
                counts = collections.Counter(v[0] for v in dec.values())
                old_line = orig.split("\n")[m["line"] - 1]
                new_line = m["text"].split("\n")[m["line"] - 1]
                # per killed test: the keys of the parent and of the mutant, the closure, the decision and its reason
                detail = [{"test": t, "key_parent": base_ev[t]["code_closure_digest"] if t in base_ev else None,
                           "key_mutant": cur_ev[t]["code_closure_digest"] if t in cur_ev else None,
                           "key_unchanged": bool(t in base_ev and t in cur_ev and base_ev[t]["code_closure_digest"] == cur_ev[t]["code_closure_digest"]),
                           "closure_nodes": cur_ev[t]["_closure_nodes"] if t in cur_ev else None, "observed": ["FAIL", "FAIL"],
                           "decision": dec[t][0], "reasons": list(dec[t][1])} for t in killed]
                return dict(m, text=None, status="killed" if killed else "survived", killed=len(killed), unstable=len(unstable),
                            pass_pass=passed, other_outcome=other, rounds=rounds, never_observed=len(missing1), false_reuse=false, killed_tests=detail,
                            killed_with_unchanged_key=sum(1 for d_ in detail if d_["key_unchanged"]),
                            before=old_line.strip(), after=new_line.strip(),
                            killed_decisions=dict(collections.Counter(dec[t][0] for t in killed)),
                            eligible=counts[ev.REUSE_ELIGIBLE], rerun=counts[ev.RERUN_REQUIRED], unverifiable=counts[ev.UNVERIFIABLE])
            finally:
                with lock:
                    lock_free.append(wt)

        results = []
        with concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
            for i, r in enumerate(ex.map(work_one, chosen), 1):
                results.append(r)
                if i % 25 == 0:
                    print("%d/%d  killed %d  false reuse %d" % (i, len(chosen), sum(x["status"] == "killed" for x in results),
                                                                  sum(len(x.get("false_reuse", [])) for x in results)), file=sys.stderr, flush=True)
    finally:
        for wt in wts:
            if os.path.isdir(wt):
                run(["git", "-C", repo, "worktree", "remove", "--force", wt])

    st = collections.Counter(r["status"] for r in results)
    by_op = collections.defaultdict(collections.Counter)
    for r in results:
        by_op[r["op"].split(":")[0] + ":" + r["op"].split(":")[1].split("->")[0][:28]][r["status"]] += 1
    fr = [r for r in results if r.get("false_reuse")]
    report = {
        "head": head, "extractor": ident, "extractor_args": a.extractor_arg, "assurance": "everything reported is vouched (CONTROL)" if a.trust_all else "measured by qualify.py",
        "mutants": dict(st), "killed_mutants_with_a_false_reuse": len(fr),
        "pairs_mutant_x_test": {"stable_fail": sum(r.get("killed", 0) for r in results), "unstable": sum(r.get("unstable", 0) for r in results),
                                "pass_pass": sum(r.get("pass_pass", 0) for r in results), "other_outcome": sum(r.get("other_outcome", 0) for r in results)},
        "mutants_needing_a_second_round_to_observe_every_test": sum(1 for r in results if r.get("rounds", 1) > 1),
        "tests_never_observed": sum(r.get("never_observed", 0) for r in results),
        "killed_tests_with_an_unchanged_key": sum(r.get("killed_with_unchanged_key", 0) for r in results),
        "mutants_with_a_killed_test_whose_key_did_not_move": sum(1 for r in results if r.get("killed_with_unchanged_key")),
        "false_reuse_tests": sum(len(r["false_reuse"]) for r in fr),
        "killed_tests_by_decision": dict(sum((collections.Counter(r["killed_decisions"]) for r in results if r["status"] == "killed"), collections.Counter())),
        "by_operator": {k: dict(v) for k, v in sorted(by_op.items())},
        "false_reuse": [{k: r[k] for k in ("file", "line", "op", "false_reuse")} for r in fr],
        "mutants_detail": [{k: r[k] for k in r if k != "text"} for r in results],
    }
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k not in ("mutants_detail", "false_reuse")}, indent=1))
    print("FALSE REUSE (killed tests called REUSE_ELIGIBLE): %d tests in %d mutants" % (report["false_reuse_tests"], len(fr)))
    return 1 if fr and not a.trust_all else 0


if __name__ == "__main__":
    sys.exit(main())
