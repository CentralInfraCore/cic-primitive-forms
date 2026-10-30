#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Self-test of compare_canonical.py: the comparison has to turn red on each of these breaks of a correct pair of dumps.

    selftest_canonical.py INPUTS RUST_OUT GO_OUT

A comparison that was never seen to fail proves nothing, so the dumps are broken here in the ways a defect of one writer would break them and each break must be
reported: bytes that differ in one language, a digest, an acceptance turned into a refusal and the reverse, one refusal named as the other (the order in which the
faults are met), a reason outside the set, both languages wrong in the same way (only the reference sees that), a missing or an extra answer, and a generator that
stops reaching a kind of answer. Exit status 1 if one break is not noticed, or if the unbroken pair is not clean.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_canonical as cc  # noqa: E402


def first(d, pred):
    for i in sorted(d):
        if pred(d[i]):
            return i
    raise SystemExit("the dumps have no input for a break of this kind")


def ok(a):
    return a["r"][0] == "ok"


def refused(reason):
    return lambda a: a["r"][0] == "err" and a["r"][1] == reason


def breaks(inputs, rust, go):
    """name -> (rust, go, what has to show): 'divergent', 'problem' or 'coverage'."""
    out = {}

    def make(name, want, fn):
        r, g = copy.deepcopy(rust), copy.deepcopy(go)
        fn(r, g)
        out[name] = (r, g, want)

    i = first(rust, lambda a: ok(a) and len(a["r"][1]) > 4)
    make("one language writes another byte", "divergent", lambda r, g: r[i]["r"].__setitem__(1, r[i]["r"][1][:-2] + "x" + r[i]["r"][1][-1]))
    make("one language gives another digest", "divergent", lambda r, g: g[i]["r"].__setitem__(2, "sha256:" + "0" * 64))
    make("an acceptance turned into a refusal", "divergent", lambda r, g: g[i].update(r=["err", "unsupported_value"]))
    u = first(rust, refused("unsupported_value"))
    make("a refusal turned into an acceptance", "divergent", lambda r, g: r[u].update(r=copy.deepcopy(go[first(go, ok)]["r"])))
    d = first(rust, refused("duplicate_key"))
    make("one refusal named as the other in one language", "divergent", lambda r, g: g[d]["r"].__setitem__(1, "unsupported_value"))
    make("both languages name the other refusal (the order of the faults)", "problem", lambda r, g: (r[d]["r"].__setitem__(1, "unsupported_value"), g[d]["r"].__setitem__(1, "unsupported_value")))
    make("a reason outside the closed set", "problem", lambda r, g: (r[d]["r"].__setitem__(1, "weird"), g[d]["r"].__setitem__(1, "weird")))

    def both_wrong(r, g):
        for x in (r, g):
            x[i]["r"][1] = x[i]["r"][1][:-2] + "x" + x[i]["r"][1][-1]
    make("both languages write the same wrong byte", "problem", both_wrong)
    make("an answer in no known form", "problem", lambda r, g: (r[i].update(r=["maybe"]), g[i].update(r=["maybe"])))
    make("an answer missing from one language", "problem", lambda r, g: g.pop(i))
    make("an answer to an input that was not given", "problem", lambda r, g: g.update({10 ** 9: copy.deepcopy(g[i])}))

    def no_duplicate(r, g):
        for x in (r, g):
            for a in x.values():
                if a["r"][0] == "err" and a["r"][1] == "duplicate_key":
                    a["r"][1] = "unsupported_value"
    make("a generator that no longer reaches the duplicate key", "coverage", no_duplicate)
    return out


def main():
    inputs, rust, go = cc.load(sys.argv[1]), cc.load(sys.argv[2]), cc.load(sys.argv[3])
    problems, divergences, answers = cc.check(inputs, rust, go)
    if problems or divergences:
        print("self-test: the unbroken pair is not clean (%d problems, %d divergences)" % (len(problems), len(divergences)))
        sys.exit(1)
    missed = 0
    all_breaks = breaks(inputs, rust, go)
    for name, (r, g, want) in all_breaks.items():
        problems, divergences, answers = cc.check(inputs, r, g)
        seen = {"divergent": bool(divergences), "problem": bool(problems), "coverage": any(answers[x] == 0 for x in ("ok",) + cc.REASONS)}[want]
        print("  %-66s %s" % (name, "red" if seen else "NOT NOTICED"))
        missed += not seen
    if missed:
        print("self-test: %d of %d breaks were not noticed" % (missed, len(all_breaks)))
        sys.exit(1)
    print("self-test: %d breaks, every one turns the comparison red" % len(all_breaks))


if __name__ == "__main__":
    main()
