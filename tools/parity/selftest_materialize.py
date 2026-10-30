#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Self-test of compare_materialize.py: the comparison has to turn red on each of these breaks of a correct pair of dumps.

    selftest_materialize.py INPUTS RUST_OUT GO_OUT

A comparison that was never seen to fail proves nothing, so the dumps are broken here in the ways a defect of one implementation would break them, and each break must be
reported: a projection that differs in one language, a digest, an acceptance turned into a refusal and the reverse, a refusal named as another, the field of a refusal
named wrongly, a reason outside the set, both languages wrong in the same way (only the reference sees that), both languages losing the same key from the expected keys (only
the generator's own list sees that), a missing or an extra answer, a record id repeated in the inputs or in a dump, and a generator that stops reaching a kind of answer. Exit status 1 if one break is not noticed, or if the
unbroken pair is not clean.
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_materialize as cm  # noqa: E402


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

    i = first(rust, lambda a: ok(a) and len(a["r"][2]) > 20 and len(a["r"][1]) > 1)
    make("one language writes another byte of the projection", "divergent", lambda r, g: r[i]["r"].__setitem__(2, r[i]["r"][2][:-3] + "x" + r[i]["r"][2][-2:]))
    make("one language gives another digest", "divergent", lambda r, g: g[i]["r"].__setitem__(3, "sha256:" + "0" * 64))
    make("an acceptance turned into a refusal", "divergent", lambda r, g: g[i].update(r=["err", "omitted_optional", "a"]))
    n = first(rust, refused("not_flat"))
    make("a refusal turned into an acceptance", "divergent", lambda r, g: r[n].update(r=copy.deepcopy(go[first(go, ok)]["r"])))
    make("one refusal named as another in one language", "divergent", lambda r, g: g[n]["r"].__setitem__(1, "set"))
    make("the field of a refusal differs in one language", "divergent", lambda r, g: g[n]["r"].__setitem__(2, g[n]["r"][2] + "x"))
    make("both languages name another refusal (the order of the layers)", "problem", lambda r, g: (r[n]["r"].__setitem__(1, "set"), g[n]["r"].__setitem__(1, "set")))
    make("both languages name another field", "problem", lambda r, g: (r[n]["r"].__setitem__(2, ""), g[n]["r"].__setitem__(2, "")))
    make("a reason outside the closed set", "problem", lambda r, g: (r[n]["r"].__setitem__(1, "weird"), g[n]["r"].__setitem__(1, "weird")))

    def both_wrong(r, g):
        for x in (r, g):
            x[i]["r"][2] = x[i]["r"][2][:-3] + "x" + x[i]["r"][2][-2:]
    make("both languages write the same wrong byte", "problem", both_wrong)

    def both_lose_a_key(r, g):
        for x in (r, g):
            x[i]["r"][1] = x[i]["r"][1][1:]
    make("both languages lose the same key from the expected keys", "problem", both_lose_a_key)

    def both_add_a_key(r, g):
        for x in (r, g):
            x[i]["r"][1] = x[i]["r"][1] + ["~extra"]
    make("both languages add a key to the expected keys", "problem", both_add_a_key)
    make("an answer in no known form", "problem", lambda r, g: (r[i].update(r=["maybe"]), g[i].update(r=["maybe"])))
    make("an answer missing from one language", "problem", lambda r, g: g.pop(i))
    make("an answer to an input that was not given", "problem", lambda r, g: g.update({10 ** 9: copy.deepcopy(g[i])}))

    def no_set(r, g):
        for x in (r, g):
            for a in x.values():
                if a["r"][0] == "err" and a["r"][1] == "set":
                    a["r"][1] = "not_flat"
    make("a generator that no longer reaches the set refusal", "coverage", no_set)
    return out


def repeated_id_is_refused(path, which):
    """The loader has to refuse a file in which one id appears twice, whether the repeat is identical or differs (the second record would replace the first)."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    noticed = []
    for name, extra in (("identical", lines[0]), ("different", lines[0].replace('"ok"', '"err"', 1) if '"ok"' in lines[0] else lines[0] + " ")):
        with tempfile.TemporaryDirectory() as d:
            broken = os.path.join(d, "broken.jsonl")
            with open(broken, "w", encoding="utf-8") as f:
                f.write("\n".join(lines + [extra]) + "\n")
            try:
                cm.load(broken)
                noticed.append(False)
            except cm.DuplicateId:
                noticed.append(True)
    return all(noticed)


def main():
    inputs, rust, go = cm.load(sys.argv[1]), cm.load(sys.argv[2]), cm.load(sys.argv[3])
    problems, divergences, answers = cm.check(inputs, rust, go)
    if problems or divergences:
        print("self-test: the unbroken pair is not clean (%d problems, %d divergences)" % (len(problems), len(divergences)))
        sys.exit(1)
    missed = 0
    all_breaks = breaks(inputs, rust, go)
    for name, (r, g, want) in all_breaks.items():
        problems, divergences, answers = cm.check(inputs, r, g)
        seen = {"divergent": bool(divergences), "problem": bool(problems), "coverage": any(answers[x] == 0 for x in cm.REACHED)}[want]
        print("  %-66s %s" % (name, "red" if seen else "NOT NOTICED"))
        missed += not seen
    total = len(all_breaks)
    for label, path in (("a repeated id in the inputs", sys.argv[1]), ("a repeated id in the dump of rust", sys.argv[2]), ("a repeated id in the dump of go", sys.argv[3])):
        seen = repeated_id_is_refused(path, label)
        print("  %-66s %s" % (label, "red" if seen else "NOT NOTICED"))
        missed += not seen
        total += 1
    if missed:
        print("self-test: %d of %d breaks were not noticed" % (missed, total))
        sys.exit(1)
    print("self-test: %d breaks, every one turns the comparison red" % total)


if __name__ == "__main__":
    main()
