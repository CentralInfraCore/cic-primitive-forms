#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Self-test of compare_diffread.py: the comparison has to turn red on each of these breaks of a correct pair of dumps.

    selftest_diffread.py INPUTS RUST_OUT GO_OUT

A comparison that was never seen to fail proves nothing (the lesson of the blind spots of the diff gate), so the dumps are broken here in the ways a defect of one
reader would break them and each break must be reported: a reason, an offset, an acceptance turned into a refusal, a diff written back with another byte, an
offset beyond the input, a reason outside the set, a version, a missing or an extra answer, and a generator that stops reaching a reason. Exit status 1 if one
break is not noticed, or if the unbroken pair is not clean.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_diffread as cd  # noqa: E402


def first(d, pred):
    for i in sorted(d):
        if pred(d[i]):
            return i
    raise SystemExit("the dumps have no input for a break of this kind")


def err(a):
    return a["r"][0] == "err"


def ok(a):
    return a["r"][0] == "ok"


def breaks(inputs, rust, go):
    """name -> (rust, go) after the break, and what has to show: 'divergent', 'problem' or 'coverage'."""
    out = {}

    def make(name, want, fn):
        r, g = copy.deepcopy(rust), copy.deepcopy(go)
        fn(r, g)
        out[name] = (r, g, want)

    i = first(rust, lambda a: err(a) and a["r"][1] == "shape")
    make("a reason of one language changed", "divergent", lambda r, g: r[i]["r"].__setitem__(1, "path"))
    i = first(rust, lambda a: err(a) and a["r"][2] > 0)
    make("an offset of one language moved", "divergent", lambda r, g: g[i]["r"].__setitem__(2, g[i]["r"][2] - 1))
    i = first(rust, ok)
    make("an acceptance turned into a refusal", "divergent", lambda r, g: g[i].update(r=["err", "shape", 0]))
    make("a diff written back with another event count", "divergent", lambda r, g: r[i]["r"].__setitem__(2, r[i]["r"][2] + 1))

    def other_byte(r, g):
        w = r[i]["r"][1]
        r[i]["r"][1] = w[:-3] + ("2" if w[-3] != "2" else "3") + w[-2:]
        g[i]["r"][1] = r[i]["r"][1]
    make("both languages write back a diff that is not the input", "problem", other_byte)
    j = first(rust, err)
    make("an offset beyond the end of the input", "problem", lambda r, g: (r[j]["r"].__setitem__(2, len(inputs[j]["hex"]) // 2 + 1), g[j]["r"].__setitem__(2, len(inputs[j]["hex"]) // 2 + 1)))
    make("a reason outside the closed set", "problem", lambda r, g: (r[j]["r"].__setitem__(1, "weird"), g[j]["r"].__setitem__(1, "weird")))
    make("a version other than 1 beside an accepted diff", "problem", lambda r, g: (r[i].update(v=["ok", 2]), g[i].update(v=["ok", 2])))
    k = first(rust, lambda a: err(a) and a["r"][1] == "version")
    make("ReadDiffVersion says 1 beside a refusal for the version", "problem", lambda r, g: (r[k].update(v=["ok", 1]), g[k].update(v=["ok", 1])))
    m = first(rust, lambda a: a["v"][0] == "err")
    make("ReadDiffVersion refuses and ReadDiff does not say the same", "problem", lambda r, g: (r[m]["v"].__setitem__(2, r[m]["v"][2] + 1), g[m]["v"].__setitem__(2, g[m]["v"][2] + 1)))
    make("an answer missing from one language", "problem", lambda r, g: g.pop(i))
    make("an answer to an input that was not given", "problem", lambda r, g: g.update({10 ** 9: copy.deepcopy(g[i])}))

    def no_depth(r, g):
        for d in (r, g):
            for a in d.values():
                if a["r"][0] == "err" and a["r"][1] == "depth":
                    a["r"][1] = "syntax"
                    if a["v"][0] == "err":
                        a["v"][1] = "syntax"
    make("a generator that no longer reaches the depth check", "coverage", no_depth)
    return out


def main():
    inputs, rust, go = cd.load(sys.argv[1]), cd.load(sys.argv[2]), cd.load(sys.argv[3])
    problems, divergences, reasons = cd.check(inputs, rust, go)
    if problems or divergences:
        print("self-test: the unbroken pair is not clean (%d problems, %d divergences)" % (len(problems), len(divergences)))
        sys.exit(1)
    missed = 0
    for name, (r, g, want) in breaks(inputs, rust, go).items():
        problems, divergences, reasons = cd.check(inputs, r, g)
        seen = {"divergent": bool(divergences), "problem": bool(problems), "coverage": any(reasons[x] == 0 for x in cd.REASONS)}[want]
        print("  %-62s %s" % (name, "red" if seen else "NOT NOTICED"))
        missed += not seen
    n = len(breaks(inputs, rust, go))
    if missed:
        print("self-test: %d of %d breaks were not noticed" % (missed, n))
        sys.exit(1)
    print("self-test: %d breaks, every one turns the comparison red" % n)


if __name__ == "__main__":
    main()
