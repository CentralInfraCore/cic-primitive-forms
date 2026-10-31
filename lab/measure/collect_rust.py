"""Run every Rust test alone with LLVM source coverage and record which items of the graph it executed.

    python3 lab/measure/collect_rust.py --graph rtyped.json --crate rust --exes exes.txt --llvm-bin DIR --out runs.json [--jobs N]

exes.txt has one line per test executable: "<kind> <target name> <path>" (kind lib or test), built with
RUSTFLAGS="-C instrument-coverage". A function executed in a run is one whose entry count is positive in that run's profile
(closures and generic instances count, and are placed by their source location). The location of a function comes from
llvm-cov export over the executable; it is mapped to the smallest function or method node of the graph that contains it.

The output is the input of graph_vs_coverage.py --runs: per test, the graph node of the test function and the product
functions (nodes outside the tests) it ran.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--crate", required=True)
    ap.add_argument("--exes", required=True)
    ap.add_argument("--llvm-bin", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    crate = os.path.abspath(a.crate)
    profdata, llvm_cov = os.path.join(a.llvm_bin, "llvm-profdata"), os.path.join(a.llvm_bin, "llvm-cov")

    g = json.load(open(a.graph))
    nodes = g["nodes"]
    funcs_by_file = {}
    for n in nodes:
        if n["kind"] in ("func", "method"):
            funcs_by_file.setdefault(n["file"], []).append(n)
    ids = {n["id"] for n in nodes}

    def node_at(file, line):
        best = None
        for n in funcs_by_file.get(file, ()):
            if n["line"] <= line <= n["end_line"] and (best is None or n["end_line"] - n["line"] < best["end_line"] - best["line"]):
                best = n
        return best

    exes = [l.split(None, 2) for l in open(a.exes).read().splitlines() if l.strip()]
    work = tempfile.mkdtemp(prefix="rustcov-")
    runs, unplaced = {}, set()

    for kind, target, exe in exes:
        listing = sh([exe, "--list", "--format", "terse"], cwd=crate,
                     env=dict(os.environ, LLVM_PROFILE_FILE=os.path.join(work, "list-%p.profraw")))
        if listing.returncode != 0:
            sys.exit("%s --list failed: %s" % (exe, listing.stderr))
        tests = [l[: -len(": test")] for l in listing.stdout.splitlines() if l.endswith(": test")]
        if not tests:
            continue

        def one(i_name):
            i, name = i_name
            raw = os.path.join(work, "%s-%d.profraw" % (target, i))
            res = sh([exe, "--exact", name, "--test-threads=1", "-q"], cwd=crate,
                     env=dict(os.environ, LLVM_PROFILE_FILE=raw))
            prof = raw[:-4] + "data"
            m = sh([profdata, "merge", "-sparse", raw, "-o", prof])
            if m.returncode != 0:
                sys.exit("llvm-profdata merge: " + m.stderr)
            shown = sh([profdata, "show", "--all-functions", prof]).stdout
            ran, cur = set(), None
            for line in shown.splitlines():
                s = line.strip()
                if line.startswith("  ") and not line.startswith("    ") and s.endswith(":"):
                    cur = s[:-1]
                elif s.startswith("Function count:") and cur and int(s.split(":")[1]) > 0:
                    ran.add(cur)
            os.unlink(raw)
            return name, res.returncode, (res.stdout + res.stderr)[-1500:], ran, prof

        with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
            results = list(ex.map(one, enumerate(tests)))

        # where each function of the executable is: one export over the merged profile of its runs
        merged = os.path.join(work, target + ".profdata")
        m = sh([profdata, "merge", "-sparse", "-o", merged] + [r[4] for r in results])
        if m.returncode != 0:
            sys.exit("llvm-profdata merge: " + m.stderr)
        exp = sh([llvm_cov, "export", "--format=text", "--skip-expansions", "--instr-profile=" + merged, exe])
        if exp.returncode != 0:
            sys.exit("llvm-cov export: " + exp.stderr)
        where = {}
        for f in json.loads(exp.stdout)["data"][0]["functions"]:
            fn = f["filenames"][0]
            if not fn.startswith(crate + os.sep) or not f["regions"]:
                continue
            where[f["name"]] = (os.path.relpath(fn, crate), f["regions"][0][0])
        for r in results:
            os.unlink(r[4])
        os.unlink(merged)

        for name, rc, tail, ran, _ in results:
            placed = set()
            for fname in ran:
                loc = where.get(fname)
                if loc is None:
                    continue  # a function of another crate
                n = node_at(*loc)
                if n is None:
                    unplaced.add("%s:%d" % loc)
                    continue
                if not n["test"]:
                    placed.add(n["id"])
            desc = name.replace("::", "/") + "()."
            node = desc if kind == "lib" else "tests/%s::%s" % (target, desc)
            if node not in ids and kind != "lib":
                macro = "tests/%s::<macro>/%s" % (target, desc)
                node = macro if macro in ids else None
            elif node not in ids:
                node = None
            runs["%s::%s" % (target, name)] = {"node": node, "rc": rc, "tail": tail if rc else "", "ran": sorted(placed)}

    out = {"tests": runs, "init": [], "unplaced_locations": sorted(unplaced),
           "product": sorted(n["id"] for n in nodes if n["kind"] in ("func", "method") and not n["test"])}
    json.dump(out, open(a.out, "w"), indent=1)
    print("tests %d, failed %d, without a node %d, unplaced locations %d" % (
        len(runs), sum(1 for r in runs.values() if r["rc"]), sum(1 for r in runs.values() if r["node"] is None), len(unplaced)))


if __name__ == "__main__":
    main()
