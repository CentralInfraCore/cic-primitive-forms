#!/usr/bin/env python3
"""Independent cross-check of rustbranch against LLVM branch coverage (evidence, not part of the gate).

    crosscheck_llvm.py RUSTBRANCH_RUN.json LLVM_EXPORT.json

For the edges that LLVM reports as branch regions (plain `if` and `while` conditions, match guards, `&&` / `||`
operands) the true and false counts of our counters must equal the counts of the LLVM region that starts at the same
position, summed over all instantiations of a generic function. Everything LLVM does not report as a branch (match
arms, `for`, `?`, `if let`, a whole `&&` / `||` condition) is not compared: that is why the measurement does not
rely on LLVM.
"""
import collections
import json
import sys


def main():
    run = json.load(open(sys.argv[1]))
    llvm = json.load(open(sys.argv[2]))["data"][0]
    regions = collections.defaultdict(lambda: [0, 0])
    for fn in llvm["functions"]:
        for b in fn["branches"]:
            regions[(b[0], b[1])][0] += b[4]
            regions[(b[0], b[1])][1] += b[5]
    ours = []
    for pkg in run["packages"]:
        for f in pkg["files"]:
            edges, hits = f["edges"], f["hits"]
            starts = collections.Counter((e["line"], e["col"]) for e in edges if e["kind"] == "operand")
            for i in range(0, len(edges) - 1):
                e = edges[i]
                if e["kind"] not in ("if", "while", "guard", "operand") or e["label"] not in ("true", "iterate"):
                    continue
                if e["kind"] != "operand" and starts[(e["line"], e["col"])]:
                    continue  # the whole condition of a && / || chain: LLVM reports only its operands
                ours.append((f["path"], e["line"], e["col"], e["kind"], hits[i], hits[i + 1]))
    bad = 0
    for path, line, col, kind, t, f in ours:
        want = regions.get((line, col))
        if want is None:
            bad += 1
            print("NO LLVM REGION  %s:%d:%d %s" % (path, line, col, kind))
        elif list(want) != [t, f]:
            bad += 1
            print("DIFFERENT       %s:%d:%d %s ours %s llvm %s" % (path, line, col, kind, [t, f], want))
    print("%d edge pairs compared with LLVM branch regions, %s" % (len(ours), "all equal" if not bad else "%d differences" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
