"""How exact is a static dependency graph? Measured against what each Go test actually executes.

    python3 lab/measure/graph_vs_coverage.py --typed typed.json --test-bin forms.test --pkg-dir go \
        [--meta-dir DIR] [--jobs N] [--drop-edges FRACTION --seed N] [--out report.json]

For every top-level test (Test*, Fuzz* with its seed corpus) the coverage binary is run alone, with a coverage profile. The
product functions it executed are compared with the static closure of the test in the graph:

* a function that ran but is not in the closure is a MISS: the graph would let a change of that function reuse the test's old
  result. A sound graph has none.
* the share of the closure that ran is the precision: how much the graph over-approximates.

Package initialisation runs before every test, so what an empty run (-test.run '^$') executes is taken out of every test's set
and checked once against the closure of the package-level variables and init functions.

--meta-dir reads companion YAMLs written by go.meta.gen.py and builds the graph the knowledge base builds from them (make_source.py:
a call is matched by its last name segment to every object of that name). It is measured the same way.

--drop-edges removes that fraction of the typed graph's edges at random (a planted defect): the measurement must then report
misses, or it cannot tell a sound graph from a broken one.
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
import tempfile

PROFILE_LINE = re.compile(r"^(.+?):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$")


def run_profile(test_bin, pkg_dir, pattern):
    with tempfile.NamedTemporaryFile(suffix=".cov", delete=False) as f:
        path = f.name
    try:
        res = subprocess.run([test_bin, "-test.run", pattern, "-test.count=1", "-test.coverprofile=" + path],
                             cwd=pkg_dir, capture_output=True, text=True)
        blocks = []
        with open(path) as fh:
            for line in fh:
                m = PROFILE_LINE.match(line.strip())
                if m and int(m.group(4)) > 0:
                    blocks.append((m.group(1), int(m.group(2)), int(m.group(3))))
        return res.returncode, res.stdout[-2000:] + res.stderr[-2000:], blocks
    finally:
        os.unlink(path)


class Typed:
    def __init__(self, path):
        g = json.load(open(path))
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.module_files = {}
        for n in g["nodes"]:
            if n["kind"] in ("func", "method") and not n["test"]:
                self.module_files.setdefault(n["file"], []).append(n)

    def func_at(self, file, line):
        for n in self.module_files.get(file, ()):
            if n["line"] <= line <= n["end_line"]:
                return n["id"]
        return None


def closure(deps, roots):
    seen, stack = set(roots), list(roots)
    while stack:
        for d in deps.get(stack.pop(), ()):
            if d not in seen:
                seen.add(d)
                stack.append(d)
    return seen


def meta_graph(meta_dir, typed):
    """The knowledge base's call graph from the companion YAMLs, with nodes named by the typed graph's ids where they match."""
    import yaml
    objs = []  # (file, name, receiver, calls)
    for root, _, files in os.walk(meta_dir):
        for fn in files:
            if not fn.endswith(".yaml"):
                continue
            data = yaml.safe_load(open(os.path.join(root, fn))) or {}
            gofile = os.path.relpath(os.path.join(root, fn[:-5] + ".go"), meta_dir)
            for o in data.get("objects", []):
                objs.append((gofile, o["name"], o.get("receiver", ""), o.get("calls", []) or []))
    by_name = collections.defaultdict(list)
    key = {}
    for i, (f, name, recv, _) in enumerate(objs):
        by_name[name].append(i)
    # map typed ids onto meta objects: same file and name (and receiver for a method)
    for tid, n in typed.nodes.items():
        last = tid.rsplit("/", 1)[-1].split(".")
        name = last[-1]
        recv = last[-2] if n["kind"] == "method" and len(last) >= 3 else ""
        for i in by_name.get(name, ()):
            f, _, r, _ = objs[i]
            if f == n["file"] and (r == recv or not recv):
                key[tid] = i
    deps = {}
    for i, (_, _, _, calls) in enumerate(objs):
        out = set()
        for c in calls:
            for j in by_name.get(c.split(".")[-1], ()):
                if j != i:
                    out.add(j)
        deps[i] = out
    return key, deps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--typed", required=True)
    ap.add_argument("--test-bin", help="Go: the coverage test binary (go test -c -cover)")
    ap.add_argument("--pkg-dir", help="Go: the package directory the tests run in")
    ap.add_argument("--runs", help="instead of a Go binary: per-test runs recorded by collect_rust.py")
    ap.add_argument("--meta-dir")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--drop-edges", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out")
    ap.add_argument("--expect", choices=("sound", "misses"),
                    help="exit 1 unless the typed graph has no miss (sound) or has one (misses: a planted defect must be seen)")
    a = ap.parse_args()

    typed = Typed(a.typed)
    deps = {i: set(n["deps"]) for i, n in typed.nodes.items()}
    if a.drop_edges:
        rnd = random.Random(a.seed)
        for i in sorted(deps):
            deps[i] = {d for d in sorted(deps[i]) if rnd.random() >= a.drop_edges}

    product = {i for i, n in typed.nodes.items() if n["kind"] in ("func", "method") and not n["test"]}
    if a.runs:
        recorded = json.load(open(a.runs))
        results = {t: (r["rc"], r["tail"], set(r["ran"])) for t, r in recorded["tests"].items()}
        names = sorted(results)
        test_ids = {t: r["node"] for t, r in recorded["tests"].items() if r["node"]}
        init_funcs, init_closure = set(), set()
    else:
        test_bin = os.path.abspath(a.test_bin)
        names = subprocess.run([test_bin, "-test.list", ".*"], cwd=a.pkg_dir, capture_output=True, text=True,
                               check=True).stdout.split()
        names = [n for n in names if n.startswith(("Test", "Fuzz"))]

        # the profile names files by import path; the graph by path relative to the module directory
        def rel(profile_file):
            return profile_file.rsplit("/", 1)[-1]

        def funcs(blocks):
            out = set()
            for f, l1, _ in blocks:
                fid = typed.func_at(rel(f), l1)
                if fid:
                    out.add(fid)
            return out

        rc, _, init_blocks = run_profile(test_bin, a.pkg_dir, "^$")
        if rc != 0:
            sys.exit("the empty run failed")
        init_funcs = funcs(init_blocks)
        init_roots = [i for i, n in typed.nodes.items() if n["kind"] == "var" or ".init@" in i]
        init_closure = closure(deps, init_roots)

        def one(name):
            rc, tail, blocks = run_profile(test_bin, a.pkg_dir, "^" + re.escape(name) + "$")
            return name, rc, tail, funcs(blocks) - init_funcs

        results = {}
        with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
            for name, rc, tail, ran in ex.map(one, names):
                results[name] = (rc, tail, ran)

        test_ids = {}
        for i, n in typed.nodes.items():
            if n["test"] and n["kind"] == "func":
                test_ids.setdefault(i.rsplit(".", 1)[-1], i)

    graphs = {"typed": (lambda t: test_ids.get(t), deps, lambda i: i)}
    if a.meta_dir:
        key, mdeps = meta_graph(a.meta_dir, typed)
        graphs["meta"] = (lambda t: key.get(test_ids.get(t)), mdeps, None)

    report = {"tests": len(names), "failed": sorted(t for t, r in results.items() if r[0] != 0),
              "init": {"ran": len(init_funcs), "missed": sorted(init_funcs - init_closure)}, "graphs": {}}
    for gname, (root_of, gdeps, _) in graphs.items():
        missed_pairs, missed_funcs, ratios, no_root = 0, collections.Counter(), [], []
        for t, (rc, _, ran) in sorted(results.items()):
            root = root_of(t)
            if root is None:
                no_root.append(t)
                continue
            reach = closure(gdeps, [root])
            if gname == "typed":
                reach_ids = reach
            else:
                reach_ids = {tid for tid, j in key.items() if j in reach}
            reach_product = reach_ids & product
            miss = ran - reach_ids
            missed_pairs += len(miss)
            missed_funcs.update(miss)
            if reach_product:
                ratios.append(len(ran & reach_product) / len(reach_product))
        report["graphs"][gname] = {
            "tests_measured": len(results) - len(no_root),
            "tests_without_a_node": no_root,
            "tests_with_a_miss": None,
            "missed_test_function_pairs": missed_pairs,
            "distinct_missed_functions": len(missed_funcs),
            "most_missed": [[f.rsplit("/", 1)[-1], c] for f, c in missed_funcs.most_common(15)],
            "precision_mean": round(sum(ratios) / len(ratios), 3) if ratios else None,
            "precision_median": round(sorted(ratios)[len(ratios) // 2], 3) if ratios else None,
        }
        report["graphs"][gname]["tests_with_a_miss"] = sum(
            1 for t, (_, _, ran) in results.items() if root_of(t) is not None and ran - (
                closure(gdeps, [root_of(t)]) if gname == "typed" else
                {tid for tid, j in key.items() if j in closure(gdeps, [root_of(t)])}))
    # the reuse question: if one product function changes, how many tests must run again (static) and how many ran it (dynamic)?
    rerun_static, rerun_dynamic = collections.Counter(), collections.Counter()
    for t, (_, _, ran) in results.items():
        root = test_ids.get(t)
        if root is None:
            continue
        for f in closure(deps, [root]) & product:
            rerun_static[f] += 1
        for f in ran:
            rerun_dynamic[f] += 1

    def dist(counter):
        vals = sorted(counter.get(f, 0) / len(results) for f in product)
        return {"median": round(vals[len(vals) // 2], 3), "mean": round(sum(vals) / len(vals), 3), "max": round(vals[-1], 3)}

    report["share_of_tests_rerun_per_changed_function"] = {"typed_graph": dist(rerun_static), "coverage": dist(rerun_dynamic)}
    report["ran_product_functions_mean"] = round(sum(len(r[2]) for r in results.values()) / max(1, len(results)), 1)
    report["product_functions"] = len(product)
    out = json.dumps(report, indent=1)
    if a.out:
        open(a.out, "w").write(out + "\n")
    print(out)
    misses = report["graphs"]["typed"]["missed_test_function_pairs"]
    if report["failed"]:
        sys.exit("tests failed: %s" % report["failed"])
    if a.expect == "sound" and (misses or report["init"]["missed"]):
        sys.exit("the typed graph missed %d test/function pairs" % misses)
    # a test the graph has no node for is a test whose closure is unknown: what it ran cannot be checked, so it is not sound
    if a.expect == "sound" and report["graphs"]["typed"]["tests_without_a_node"]:
        sys.exit("tests without a node in the graph: %s" % report["graphs"]["typed"]["tests_without_a_node"][:10])
    if a.expect == "misses" and not misses:
        sys.exit("the planted defect was not seen: no miss")


if __name__ == "__main__":
    main()
