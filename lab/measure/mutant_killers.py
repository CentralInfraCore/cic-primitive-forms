"""Which tests kill a mutant, and does a verdict carried over from the parent hold on the child? Measured on a sample.

    python3 lab/measure/mutant_killers.py --repo . --work DIR --parent P --child C --sample N [--seed S] \
        --gomutid BIN --rust-toolchain 1.99.1 --out report.json

DIR is the work directory of backtest_mutants.py (mutants/<commit>.json, cache.json, traces/). Two groups are sampled from the
mutants of the child, per language:

* STRICT: the mutants the strict rule reuses (the same tests reach them, all reusable). Their verdict on the child must equal the
  parent's.
* KILL: the mutants the strict rule reruns although a reusable test of the parent reaches them. The kill rule reuses such a mutant
  when it was killed on the parent and one of the tests that failed then is reusable: it must be killed on the child as well.
* GROWN (--groups grown): the mutants that new tests of the child reach. Nothing is carried over; the group shows whether the
  rerun is needed: a mutant that survived on the parent and dies on the child is a verdict that carrying everything over (a rule
  without the test sets: the planted defect) would have got wrong.

A mutant is run by applying it to a worktree of the commit (the Go file go-mutesting made, or the diff cargo-mutants lists) and
running every test, with the upstream checkouts at the commit's pins; the tests that fail are its killers. A build failure is
"unviable", a run over the timeout is "timeout" (killed, as the gates count it).
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import backtest_mutants as bm  # noqa: E402

RUNNING = re.compile(r"^\s+Running (?:unittests )?(\S+) \((.+)\)")
RFAIL = re.compile(r"^test (\S+) \.\.\. FAILED")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def run_group(cmd, timeout, **kw):
    """Run in a process group of its own and kill the whole group on timeout: a mutant that loops forever lives in the test
    binary cargo or go test started, which a plain timeout would leave running."""
    p = subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.PIPE, stderr=kw.pop("stderr", subprocess.PIPE),
                         text=True, errors="replace", **kw)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, 9)
        p.communicate()
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


class Commit:
    def __init__(self, repo, commit, work, gomutid, toolchain, upstream):
        self.repo, self.commit, self.work, self.toolchain, self.upstream = repo, commit, work, toolchain, upstream
        self.wt = os.path.join(work, "kill-wt-" + commit[:7])
        if not os.path.isdir(self.wt):
            run(["git", "-C", repo, "worktree", "add", "--detach", self.wt, commit], check=True)
        self.go, self.rust = {}, {}
        rec = os.path.join(work, "kill-gorec-" + commit[:7])
        if not os.path.exists(os.path.join(rec, "index.tsv")):
            run(["rm", "-rf", rec])
            os.makedirs(rec)
            run(["go-mutesting", "--exec", os.path.join(repo, "tools/c0/record_exec.sh"), "./..."],
                cwd=os.path.join(self.wt, "go"), env=dict(os.environ, C0_REC=rec, MUTATE_TIMEOUT="10"))
        ids = run([gomutid, "ids", "-orig", rec + "/orig", "-mut", rec + "/mut", "-index", rec + "/index.tsv"], check=True)
        for line in ids.stdout.splitlines():
            f = line.split("\t")
            self.go["go-token-diff/1\0" + f[0]] = (f[1], os.path.join(rec, "mut", f[7] + ".go"))
        listing = run(["cargo", "+" + toolchain, "mutants", "--list", "--json"], cwd=os.path.join(self.wt, "rust"), check=True)
        count = {}
        for m in sorted(json.loads(listing.stdout), key=lambda m: (m["file"], m["span"]["start"]["line"], m["span"]["start"]["column"])):
            what = bm.LISTING.sub("", m["name"])
            unit = (m.get("function") or {}).get("function_name", "")
            k = (m["file"], unit, what)
            count[k] = count.get(k, 0) + 1
            self.rust["rust-name/1\0%s\0%s\0%s\0%d" % (m["file"], unit, what, count[k])] = (m["file"], m["diff"])

    def env(self):
        env = dict(os.environ, CIC_FORMS_ROOT=self.wt, CIC_PRIMITIVES_ROOT=os.path.join(self.upstream, "cic-primitives"),
                   CIC_REGISTRY_ROOT=os.path.join(self.upstream, "cic-schema-registry"))
        for name, pin in bd.pins(self.repo, self.commit).items():
            run(["git", "-C", os.path.join(self.upstream, name), "checkout", "-q", "--detach", pin], check=True)
        return env

    def restore(self):
        run(["git", "-C", self.wt, "checkout", "-q", "--", "."], check=True)

    def run_go(self, mid):
        file, mutated = self.go[mid]
        target = os.path.join(self.wt, "go", file)
        try:
            with open(mutated, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
            try:
                r = run_group(["go", "test", "-json", "-count=1", "-timeout", "300s", "."], cwd=os.path.join(self.wt, "go"),
                              env=self.env(), timeout=400)
            except subprocess.TimeoutExpired:
                return "timeout", []
            failed, build_failed = set(), False
            for line in r.stdout.splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("Action") == "fail" and e.get("Test") and "/" not in e["Test"]:
                    failed.add("go:" + e["Test"])
                if e.get("Action") == "build-fail" or (e.get("Action") == "output" and "[build failed]" in e.get("Output", "")):
                    build_failed = True
            if build_failed or (r.returncode and not failed and "FAIL" in r.stdout and "panic" not in r.stdout):
                return "unviable", []
            if r.returncode and not failed:
                return "killed", ["go:<package>"]
            return ("killed" if failed else "survived"), sorted(failed)
        finally:
            self.restore()

    def run_rust(self, mid):
        file, diff = self.rust[mid]
        target = os.path.join(self.wt, "rust", file)
        try:
            p = run(["patch", "-s", target], input=diff)
            if p.returncode:
                raise RuntimeError("patch failed for %s: %s" % (mid, p.stdout + p.stderr))
            env = dict(self.env(), CARGO_TARGET_DIR=os.path.join(self.work, "kill-target"))
            b = run(["cargo", "+" + self.toolchain, "test", "--no-run"], cwd=os.path.join(self.wt, "rust"), env=env, timeout=900)
            if b.returncode:
                return "unviable", []
            try:
                # one stream: the "Running <target>" lines go to stderr, the test results to stdout, and their order matters
                r = run_group(["cargo", "+" + self.toolchain, "test", "--no-fail-fast", "--", "--test-threads=8"],
                              cwd=os.path.join(self.wt, "rust"), env=env, timeout=600, stderr=subprocess.STDOUT)
            except subprocess.TimeoutExpired:
                return "timeout", []
            failed, current = set(), None
            for line in r.stdout.splitlines():
                m = RUNNING.match(line)
                if m:
                    src, path = m.group(1), m.group(2)
                    current = "cic_primitive_forms" if src.startswith("src/") else os.path.basename(src)[: -len(".rs")]
                    continue
                m = RFAIL.match(line)
                if m:
                    failed.add("rust:%s::%s" % (current, m.group(1)))
            if r.returncode and not failed:
                return "killed", ["rust:<run>"]
            return ("killed" if failed else "survived"), sorted(failed)
        finally:
            self.restore()

    def run(self, lang, mid):
        return self.run_go(mid) if lang == "go" else self.run_rust(mid)

    def close(self):
        run(["git", "-C", self.repo, "worktree", "remove", "--force", self.wt])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--parent", required=True)
    ap.add_argument("--child", required=True)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--gomutid", required=True)
    ap.add_argument("--rust-toolchain", required=True)
    ap.add_argument("--langs", default="go,rust")
    ap.add_argument("--groups", default="strict,kill")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    full = {c: bd.git(repo, "rev-parse", c).strip() for c in (a.parent, a.child)}
    p, c = full[a.parent], full[a.child]
    cache = json.load(open(os.path.join(a.work, "cache.json")))
    prev = json.load(open(os.path.join(a.work, "mutants", p + ".json")))
    cur = json.load(open(os.path.join(a.work, "mutants", c + ".json")))
    reusable_tests = bm.test_reuse(repo, p, c, cache, a.work)
    rnd = random.Random(a.seed)

    groups = {}
    for mid, m in sorted(cur["mutants"].items()):
        old = prev["mutants"].get(mid)
        if old is None or m["node"] is None or old["node"] is None or prev["tool"] != cur["tool"]:
            continue
        strict = m["tests"] == old["tests"] and set(m["tests"]) <= reusable_tests and (m["tests"] or m["node_key"] == old["node_key"])
        if strict:
            groups.setdefault((m["lang"], "strict"), []).append(mid)
        elif set(old["tests"]) & reusable_tests:
            groups.setdefault((m["lang"], "kill"), []).append(mid)
        if set(m["tests"]) > set(old["tests"]):
            groups.setdefault((m["lang"], "grown"), []).append(mid)

    P = Commit(repo, p, a.work, a.gomutid, a.rust_toolchain, a.upstream)
    C = Commit(repo, c, a.work, a.gomutid, a.rust_toolchain, a.upstream)
    results = []
    try:
        for lang in a.langs.split(","):
            for group in a.groups.split(","):
                pool = groups.get((lang, group), [])
                for mid in rnd.sample(pool, min(a.sample, len(pool))):
                    vp, kp = P.run(lang, mid)
                    vc, kc = C.run(lang, mid)
                    killed_p = vp in ("killed", "timeout")
                    if group == "grown":
                        predicted, reused = None, False  # nothing carried: the rerun is what is measured
                    elif group == "strict":
                        predicted = vp
                        reused = True
                    else:
                        reused = killed_p and bool(set(kp) & reusable_tests)
                        predicted = vp if reused else None
                    holds = None if predicted is None else (predicted == vc or (predicted in ("killed", "timeout") and vc in ("killed", "timeout")))
                    results.append({"lang": lang, "group": group, "id": mid.replace("\0", " | "), "parent": vp, "child": vc,
                                    "killers_parent": kp, "killers_child": kc, "reused": reused, "holds": holds})
                    print("%-4s %-6s %-9s -> %-9s reused=%-5s holds=%s %s" % (lang, group, vp, vc, reused, holds,
                                                                           mid.split("\0")[-3][:50] if lang == "rust" else ""),
                          file=sys.stderr, flush=True)
    finally:
        P.close()
        C.close()
        for name, pin in bd.pins(repo, bd.git(repo, "rev-parse", "HEAD").strip()).items():
            run(["git", "-C", os.path.join(a.upstream, name), "checkout", "-q", "--detach", pin])
    summary = {}
    for r in results:
        s = summary.setdefault("%s/%s" % (r["lang"], r["group"]), {"sampled": 0, "reused": 0, "held": 0, "broke": 0,
                                                                     "pool": len(groups.get((r["lang"], r["group"]), []))})
        s["sampled"] += 1
        s["reused"] += r["reused"]
        s["held"] += r["holds"] is True
        s["broke"] += r["holds"] is False
        s.setdefault("flipped", 0)
        s["flipped"] += r["parent"] != r["child"]
    json.dump({"parent": p, "child": c, "summary": summary, "results": results}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))
    if any(s["broke"] for s in summary.values()):
        sys.exit("a carried verdict did not hold")


if __name__ == "__main__":
    main()
