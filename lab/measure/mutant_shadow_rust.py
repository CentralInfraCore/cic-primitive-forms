"""Injected semantic changes against the decisions of the kernel, for the Rust crate: is a test that fails on a mutant ever REUSE_ELIGIBLE?

    python3 lab/measure/mutant_shadow_rust.py --repo . --scipdeps BIN --toolchain 1.99.1 --upstream DIR --out report.json \
        [--general 100] [--seed 1] [--workers 4] [--env NAME=VALUE ...] [--trust-all]

The method of mutant_shadow.py (Go): for the tree at HEAD (the baseline, every test passes) and each mutant of rust/src, the test binaries are
built and run (a failure observed twice), the graph is made again (rust-analyzer's SCIP index, scipdeps), and the kernel decides every test
for the pair baseline -> mutant. A killed test called REUSE_ELIGIBLE is a false reuse; a killed test whose Merkle key did not move is the
same finding at the level of the key, independent of the Assurance.

Operators, textual, applied outside strings, comments and the `#[cfg(test)]` part of a file:
  general   == != < <= > >= && || true false + -, an integer literal +1, a string literal gets a suffix
  targeted  the places of the implicit calls: inside any `impl <Trait> for <Type>`: a string literal, a bool, a comparison, an integer
            (what the older rule could not follow: an impl for a type with a lifetime parameter, or any impl when its edges are planted away)

--env gives the extractor planted defects (SCIPDEPS_PLANTED_NO_IMPL=1, SCIPDEPS_PLANTED_PLAIN_SELF_ONLY=1) and --trust-all an Assurance that
vouches for everything it reports: the control that shows the mutant set CAN find them (a counterfactual, not a path of the system).
Without them the numbers are those of the build as it is, qualified by its probes.
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
import threading

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402
import qualify_rust as qr  # noqa: E402
import rust_graph as rg  # noqa: E402

STR = re.compile(r'"(?:\\.|[^"\\])*"')
IMPL = re.compile(r"^\s*impl(?:<[^>]*>)?\s+[\w:]+(?:<[^{]*?>)?\s+for\s")  # any `impl Trait for Type`: the implicit calls reach the crate through these
SWAPS = ((" == ", " != "), (" != ", " == "), (" < ", " <= "), (" <= ", " < "), (" > ", " >= "), (" >= ", " > "),
         (" && ", " || "), (" || ", " && "), (" + ", " - "), (" - ", " + "))


def mask(line, state):
    """The line with strings, chars and comments blanked (same length). A lifetime ('a) is not a char literal."""
    out, i, n = [], 0, len(line)
    raw = state["raw"]
    while i < n:
        c = line[i]
        if raw:
            out.append(" ")
            if c == '"':
                raw = False
            i += 1
        elif c == "r" and line[i:i + 2] in ('r"', 'r#') and (i == 0 or not (line[i - 1].isalnum() or line[i - 1] == "_")):
            j = i + 1
            while j < n and line[j] == "#":
                j += 1
            if j < n and line[j] == '"':
                raw = True
                out.append(" " * (j - i + 1))
                i = j + 1
            else:
                out.append(c)
                i += 1
        elif c == '"':
            j = i + 1
            while j < n and line[j] != '"':
                j += 2 if line[j] == "\\" else 1
            out.append(" " * (min(j, n - 1) - i + 1))
            i = j + 1
        elif c == "'" and i + 2 < n and (line[i + 2] == "'" or line[i + 1] == "\\"):
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


def mutants_of(text):
    """[(operator, line number, mutated file)] for one file of rust/src (only the part before `#[cfg(test)]`)."""
    lines = text.split("\n")
    end = next((i for i, l in enumerate(lines) if l.strip() == "#[cfg(test)]"), len(lines))
    state = {"raw": False}
    masked = [mask(l, state) for l in lines]
    out = []

    def put(op, i, new_line):
        new = list(lines)
        new[i] = new_line
        out.append((op, i + 1, "\n".join(new)))

    in_impl, depth = False, 0
    for i in range(end):
        l, m = lines[i], masked[i]
        s = l.strip()
        if IMPL.match(l) and "{" in m:
            in_impl, depth = True, 0
        tag = "targeted:impl " if in_impl else "general:"
        if in_impl:
            depth += m.count("{") - m.count("}")
        if s.startswith(("//", "use ", "mod ", "#[", "pub use ", "extern ")):
            if in_impl and depth <= 0:
                in_impl = False
            continue
        for a, b in SWAPS:
            k = m.find(a)
            if k >= 0:
                put(tag + a.strip() + "->" + b.strip(), i, l[:k] + b + l[k + len(a):])
        for lit, rep in (("true", "false"), ("false", "true")):
            mm = re.search(r"\b%s\b" % lit, m)
            if mm:
                put(tag + "%s->%s" % (lit, rep), i, l[:mm.start()] + rep + l[mm.end():])
        mm = re.search(r"(?<![\w.\[\]<])[0-9]+(?![\w.\]>])", m)
        if mm and "const " not in s and "static " not in s:
            put(tag + "int+1", i, l[:mm.start()] + str(int(mm.group()) + 1) + l[mm.end():])
        if "r#" not in l and "env!" not in l and "include" not in l:
            ms = STR.search(l)
            if ms and len(ms.group()) > 2:
                put(tag + "string+suffix", i, l[:ms.end() - 1] + "~" + l[ms.end() - 1:])
        if in_impl and depth <= 0:
            in_impl = False
    return out


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def build_and_run(wt, target, tc, env, twice_if_failed=False, timeout=900):
    """(None if it does not build or times out, {runner key: outcome}) for the test binaries of wt/rust."""
    env = dict(env, CARGO_TARGET_DIR=target)
    try:
        b = run(["rustup", "run", tc, "cargo", "test", "--no-run", "--message-format=json"], cwd=os.path.join(wt, "rust"), env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, None
    if b.returncode:
        return None, None
    exes = []
    for line in b.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
            exes.append((m["target"]["name"].replace("-", "_"), m["executable"]))

    def once():
        out = {}
        for tgt, exe in exes:
            try:
                r = run([exe], cwd=os.path.join(wt, "rust"), env=env, timeout=180)
            except subprocess.TimeoutExpired:
                continue
            for mm in re.finditer(r"^test (\S+) \.\.\. (ok|FAILED|ignored)", r.stdout, re.M):
                out["%s::%s" % (tgt, mm.group(1))] = {"ok": "PASS", "FAILED": "FAIL", "ignored": "SKIP"}[mm.group(2)]
        return out

    first = once()
    return exes, (first, once() if twice_if_failed and "FAIL" in first.values() else first)


def summaries(graph, ident, prov):
    keys, _ = merkle.keys(graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    out = {}
    for n in graph["nodes"]:
        if n.get("entry"):
            e = be.build_evidence(graph, keys, n["id"], ident, prov, "-", "-")
            e.pop("provenance", None)
            e["_closure_nodes"] = len(be.closure(nodes, n["id"]))
            out[bd.runner_key("rust:" + n["id"]).split(":", 1)[1]] = e
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--scipdeps", required=True)
    ap.add_argument("--toolchain", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--general", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--trust-all", action="store_true")
    ap.add_argument("--only-targeted", action="store_true")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    work = os.path.join(os.path.dirname(os.path.abspath(a.out)), "mutant-shadow-rust-wt")
    os.makedirs(work, exist_ok=True)
    head = bd.git(repo, "rev-parse", "HEAD").strip()
    up = os.path.abspath(a.upstream)
    for name, pin in bd.pins(repo, head).items():
        if run(["git", "-C", os.path.join(up, name), "checkout", "-q", "--detach", "--force", pin]).returncode:
            run(["git", "-C", os.path.join(up, name), "fetch", "-q", "origin"])
            run(["git", "-C", os.path.join(up, name), "checkout", "-q", "--detach", "--force", pin])
    planted = dict(e.split("=", 1) for e in a.env)
    base_env = dict(os.environ, CIC_PRIMITIVES_ROOT=os.path.join(up, "cic-primitives"), CIC_REGISTRY_ROOT=os.path.join(up, "cic-schema-registry"),
                    CARGO_NET_OFFLINE="true", CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0")
    tc = rg.toolchain_identity(a.toolchain)
    ident, prov = be.extractor_identity(a.scipdeps, tuple("%s=%s" % kv for kv in sorted(planted.items())), name="scipdeps",
                                        chain=(("rust-analyzer", tc["rust-analyzer-path"]),))
    wts = []
    for k in range(a.workers):
        wt = os.path.join(work, "wt%d" % k)
        if not os.path.isdir(wt):
            run(["git", "-C", repo, "worktree", "add", "-q", "--detach", wt, head], check=True)
        wts.append(wt)
    results = []
    try:
        env0 = dict(base_env, CIC_FORMS_ROOT=wts[0])
        exes, base = build_and_run(wts[0], os.path.join(work, "target0"), a.toolchain, env0, twice_if_failed=False)
        if exes is None:
            sys.exit("the baseline does not build")
        b1, b2 = base
        passing = {t for t, v in b1.items() if v == "PASS" and b2.get(t) == "PASS"}
        base_graph = rg.graph(os.path.join(wts[0], "rust"), a.scipdeps, a.toolchain, planted)
        base_ev = summaries(base_graph, ident, prov)
        print("baseline: %d tests, %d pass twice, %d fail, %d skip" % (len(b1), len(passing), sum(v == "FAIL" for v in b1.values()),
                                                                       sum(v == "SKIP" for v in b1.values())), file=sys.stderr, flush=True)
        if a.trust_all:
            constructs = set()
            for e in base_ev.values():
                constructs |= set(e["graph"]["constructs"])
            verified = frozenset(constructs | qr.CLAIMED | {"rs.trait_impl", "rs.generic.type", "rs.generic.lifetime", "rs.macro.external"})
        else:
            verified = qr.qualify(a.scipdeps, a.toolchain, planted)
        assurance = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {ident: verified})

        pool = []
        for f in sorted(os.listdir(os.path.join(repo, "rust", "src"))):
            if f.endswith(".rs"):
                for op, line, new in mutants_of(open(os.path.join(repo, "rust", "src", f)).read()):
                    pool.append({"file": f, "line": line, "op": op, "text": new})
        targeted = [m for m in pool if m["op"].startswith("targeted")]
        general = [m for m in pool if m["op"].startswith("general")]
        random.Random(a.seed).shuffle(general)
        chosen = targeted + ([] if a.only_targeted else general[: a.general])
        print("candidates: %d targeted, %d general; running %d" % (len(targeted), len(general), len(chosen)), file=sys.stderr, flush=True)

        free = collections.deque(range(a.workers))
        lock = threading.Lock()

        def work_one(m):
            with lock:
                k = free.popleft()
            wt = wts[k]
            try:
                path = os.path.join(wt, "rust", "src", m["file"])
                orig = open(path).read()
                open(path, "w").write(m["text"])
                env = dict(base_env, CIC_FORMS_ROOT=wt)
                try:
                    exes_, runs = build_and_run(wt, os.path.join(work, "target%d" % k), a.toolchain, env, twice_if_failed=True)
                    if exes_ is None:
                        return dict(m, text=None, status="dropped")
                    r1, r2 = runs
                    # the index and the graph (about 25 s under load) only where a test failed: for a survivor no decision can be a false reuse
                    graph = (rg.graph(os.path.join(wt, "rust"), a.scipdeps, a.toolchain, planted)
                             if any(r1.get(t) == "FAIL" for t in passing) else None)
                except subprocess.CalledProcessError:
                    return dict(m, text=None, status="dropped")
                finally:
                    open(path, "w").write(orig)
                if graph is None:
                    passed = sum(1 for t in passing if r1.get(t) == "PASS" and r2.get(t) == "PASS")
                    return dict(m, text=None, status="survived", killed=0, unstable=0, pass_pass=passed, other_outcome=len(passing) - passed,
                                false_reuse=[], killed_tests=[], killed_with_unchanged_key=0, killed_decisions={}, eligible=0, rerun=0,
                                unverifiable=0, before=orig.split("\n")[m["line"] - 1].strip(), after=m["text"].split("\n")[m["line"] - 1].strip())
                cur_ev = summaries(graph, ident, prov)
                killed = sorted(t for t in passing if r1.get(t) == "FAIL" and r2.get(t) == "FAIL")
                unstable = sorted(t for t in passing if (r1.get(t) == "FAIL") != (r2.get(t) == "FAIL"))
                passed = sum(1 for t in passing if r1.get(t) == "PASS" and r2.get(t) == "PASS")
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
                detail = [{"test": t, "key_parent": base_ev[t]["code_closure_digest"] if t in base_ev else None,
                           "key_mutant": cur_ev[t]["code_closure_digest"] if t in cur_ev else None,
                           "key_unchanged": bool(t in base_ev and t in cur_ev and base_ev[t]["code_closure_digest"] == cur_ev[t]["code_closure_digest"]),
                           "closure_nodes": cur_ev[t]["_closure_nodes"] if t in cur_ev else None, "observed": ["FAIL", "FAIL"],
                           "decision": dec[t][0], "reasons": list(dec[t][1])} for t in killed]
                return dict(m, text=None, status="killed" if killed else "survived", killed=len(killed), unstable=len(unstable),
                            pass_pass=passed, other_outcome=other, false_reuse=false, killed_tests=detail,
                            killed_with_unchanged_key=sum(1 for d_ in detail if d_["key_unchanged"]),
                            before=orig.split("\n")[m["line"] - 1].strip(), after=m["text"].split("\n")[m["line"] - 1].strip(),
                            killed_decisions=dict(collections.Counter(dec[t][0] for t in killed)),
                            eligible=counts[ev.REUSE_ELIGIBLE], rerun=counts[ev.RERUN_REQUIRED], unverifiable=counts[ev.UNVERIFIABLE])
            finally:
                with lock:
                    free.append(k)

        with concurrent.futures.ThreadPoolExecutor(a.workers) as ex:
            for i, r in enumerate(ex.map(work_one, chosen), 1):
                results.append(r)
                if i % 10 == 0:
                    print("%d/%d  killed %d  false reuse %d" % (i, len(chosen), sum(x["status"] == "killed" for x in results),
                                                                  sum(len(x.get("false_reuse", [])) for x in results)), file=sys.stderr, flush=True)
    finally:
        for wt in wts:
            if os.path.isdir(wt):
                run(["git", "-C", repo, "worktree", "remove", "--force", wt])

    st = collections.Counter(r["status"] for r in results)
    by_op = collections.defaultdict(collections.Counter)
    for r in results:
        by_op[r["op"].split("->")[0][:34]][r["status"]] += 1
    fr = [r for r in results if r.get("false_reuse")]
    report = {
        "head": head, "extractor": ident, "extractor_env": planted, "assurance": "everything reported is vouched (CONTROL)" if a.trust_all else "measured by qualify_rust.py",
        "mutants": dict(st), "killed_mutants_with_a_false_reuse": len(fr), "false_reuse_tests": sum(len(r["false_reuse"]) for r in fr),
        "pairs_mutant_x_test": {"stable_fail": sum(r.get("killed", 0) for r in results), "unstable": sum(r.get("unstable", 0) for r in results),
                                "pass_pass": sum(r.get("pass_pass", 0) for r in results), "other_outcome": sum(r.get("other_outcome", 0) for r in results)},
        "killed_tests_with_an_unchanged_key": sum(r.get("killed_with_unchanged_key", 0) for r in results),
        "mutants_with_a_killed_test_whose_key_did_not_move": sum(1 for r in results if r.get("killed_with_unchanged_key")),
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
