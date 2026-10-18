#!/usr/bin/env python3
"""Spike-only threshold on a tool's own numbers: exit 1 unless covered*100 > total*90.

    threshold.py rust evidence/rust-full.json      (branch outcomes of llvm branch regions)
    threshold.py go   evidence/gobco-full.json     (condition outcomes of gobco)

This is NOT the coverage gate of ADR 0009 (it knows no categories, files or digests). It only shows,
on real tool output, that a below-threshold sample turns a CI step red.
"""
import json
import sys


def main():
    lang, path = sys.argv[1], sys.argv[2]
    raw = json.load(open(path))
    t = c = 0
    if lang == "rust":
        d = raw["data"][0] if "data" in raw else raw
        for fn in d["functions"]:
            for b in fn["branches"]:
                t += 2
                c += (b[4] > 0) + (b[5] > 0)
    else:
        for x in raw:
            t += 2
            c += (x["TrueCount"] > 0) + (x["FalseCount"] > 0)
    ok = t > 0 and c * 100 > t * 90
    print("%s: %d/%d outcomes covered -> %s" % (lang, c, t, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
