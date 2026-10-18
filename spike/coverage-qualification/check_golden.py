#!/usr/bin/env python3
"""Compare a measurement run with the hand-counted golden tables, function by function and category by category.

    check_golden.py go RUN.json go/sample.go golden.json go full|reduced
    check_golden.py go RUN.json go-complex/complex.go golden-go-complex.json go-complex full|reduced

Exit 0 only if every function and every category equal the hand count (numerator and denominator).
"""
import collections
import json
import re
import sys


def owner_table(path, pat):
    out = []
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
        m = re.match(pat, line)
        if m:
            out.append((i, m.group(1)))
    return out


def owner(table, line):
    name = None
    for l, n in table:
        if l <= line:
            name = n
    return name


def hand(golden, key):
    out = {}
    for fn, rows in golden[key].items():
        if rows and isinstance(rows[0], str):  # the simple format: [category, edges, full, reduced]
            rows = [rows]
        out[fn] = {r[0]: r[1:] for r in rows}
    return out


def main():
    lang, run, src, golden_path, key, mode = sys.argv[1:7]
    idx = 1 if mode == "full" else 2
    pat = r"pub fn (\w+)" if lang == "rust" else r"func (\w+)"
    table = owner_table(src, pat)
    want = hand(json.load(open(golden_path)), key)
    got = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for p in json.load(open(run))["packages"]:
        for f in p["files"]:
            for e, h in zip(f["edges"], f["hits"]):
                c = got[owner(table, e["line"])][e["category"]]
                c[0] += 1
                c[1] += 1 if h > 0 else 0
    bad = 0
    for fn in sorted(set(want) | set(got)):
        for cat in sorted(set(want.get(fn, {})) | set(got.get(fn, {}))):
            w = want.get(fn, {}).get(cat)
            g = got.get(fn, {}).get(cat)
            wv = [w[0], w[idx]] if w else None
            if wv != g:
                bad += 1
                print("MISMATCH %s %s: hand %s, measured %s" % (fn, cat, wv, g))
    tot = sum(v[0] for f in got.values() for v in f.values()), sum(v[1] for f in got.values() for v in f.values())
    print("%s %s: %d functions, %d edges, %d covered, %s" % (key, mode, len(want), tot[0], tot[1], "EQUAL to the hand counts" if not bad else "%d MISMATCHES" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
