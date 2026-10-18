#!/usr/bin/env python3
"""Compare the hand-counted branches of the samples with what the tools report (spike evidence).

    compare.py --rust-full F --rust-reduced F --gobco-full F --gobco-reduced F [--gobco-branch-full F --gobco-branch-reduced F]

Inputs are the tool outputs (raw, or trimmed as in evidence/): `cargo llvm-cov --json` (built with -Zcoverage-options=condition) and
`gobco -stats`. Output: a Markdown table per language. Measured outcomes are assigned to a function by the
line of the source they sit on; that is spike evidence only, not an adapter.
"""
import argparse
import collections
import json
import re
import sys

HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."


def functions(path, pat):
    out, cur = [], None
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
        m = re.match(pat, line)
        if m:
            cur = m.group(1)
            out.append((i, cur))
    return out


def owner(fns, line):
    name = None
    for l, n in fns:
        if l <= line:
            name = n
    return name


def rust_outcomes(path, fns):
    """{function: [outcomes, covered]} from llvm branch regions: each region has a true and a false outcome."""
    raw = json.load(open(path))
    d = raw["data"][0] if "data" in raw else raw  # raw llvm export, or the trimmed evidence file
    acc = collections.defaultdict(lambda: [0, 0])
    for fn in d["functions"]:
        for b in fn["branches"]:
            f = owner(fns, b[0])
            if f is None:
                continue
            acc[f][0] += 2
            acc[f][1] += (b[4] > 0) + (b[5] > 0)
    return acc


def go_outcomes(path, fns):
    acc = collections.defaultdict(lambda: [0, 0])
    for c in json.load(open(path)):
        f = owner(fns, int(c["Start"].split(":")[1]))
        acc[f][0] += 2
        acc[f][1] += (c["TrueCount"] > 0) + (c["FalseCount"] > 0)
    return acc


def table(title, golden, full, red, fns_order):
    print("### %s\n" % title)
    print("| function | category | hand edges | hand covered full / reduced | tool outcomes | tool covered full / reduced |")
    print("|---|---|---|---|---|---|")
    tot = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    for fn in fns_order:
        cat, n, cf, cr = golden[fn]
        tn, tcf = full.get(fn, [0, 0])
        _, tcr = red.get(fn, [0, 0])
        print("| %s | %s | %d | %d / %d | %d | %d / %d |" % (fn, cat, n, cf, cr, tn, tcf, tcr))
        t = tot[cat]
        t[0] += n; t[1] += cf; t[2] += cr; t[3] += tn; t[4] += tcf
    print()
    print("| category | hand edges | tool outcomes | hand edges the tool does not report |")
    print("|---|---|---|---|")
    for cat in ("conditional", "match", "loop", "short_circuit", "error_path"):
        t = tot[cat]
        print("| %s | %d | %d | %d |" % (cat, t[0], t[3], t[0] - t[3]))
    allh = sum(t[0] for t in tot.values()); allt = sum(t[3] for t in tot.values())
    print("| **all** | %d | %d | %d |\n" % (allh, allt, allh - allt))


def main():
    ap = argparse.ArgumentParser()
    for a in ("rust-full", "rust-reduced", "gobco-full", "gobco-reduced"):
        ap.add_argument("--" + a, required=True)
    args = ap.parse_args()
    golden = json.load(open(HERE + "/golden.json"))
    rf = functions(HERE + "/rust/src/lib.rs", r"pub fn (\w+)")
    gf = functions(HERE + "/go/sample.go", r"func (\w+)")
    table("Rust: `cargo llvm-cov` with `-Zcoverage-options=condition`", golden["rust"],
          rust_outcomes(args.rust_full, rf), rust_outcomes(args.rust_reduced, rf), list(golden["rust"]))
    table("Go: `gobco` (condition coverage)", golden["go"],
          go_outcomes(args.gobco_full, gf), go_outcomes(args.gobco_reduced, gf), list(golden["go"]))


if __name__ == "__main__":
    sys.exit(main())
