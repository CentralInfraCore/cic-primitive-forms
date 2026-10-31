#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Self-test of compare_receipt.py: the comparison has to turn red on each of these breaks of a correct pair of dumps.

    selftest_receipt.py INPUTS RUST_OUT GO_OUT

A comparison that was never seen to fail proves nothing. The dumps are broken here in the ways a defect of one implementation (or of both) would break the receipt: the
layout version, the output digest taken as the schema digest, the input digest taken after the defaults, an applied default listed or dropped or its value digest changed,
the evidence order, the binding of a path to a field, the schema identity (digest, name, version), the engine fields written in each other's place, the authored input,
the projection, an acceptance turned into a refusal and the reverse, a refusal named wrongly, an answer missing or extra or in no known form, a record id repeated, and
a generator that stops reaching a kind of answer. Each break is made in ONE language (only the comparison between the two sees it) and in BOTH (only the reference sees
it). Exit status 1 if one break is not noticed, or if the unbroken pair is not clean.
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_receipt as cr  # noqa: E402
import selftest_materialize as sm  # noqa: E402
from compare_receipt import rc  # noqa: E402


def ok(a):
    return a["r"][0] == "ok"


def with_defaults(n):
    return lambda a: ok(a) and len(json.loads(a["r"][1])["materialization"]["applied_defaults"]) >= n


def edit(answer, fn):
    """Apply fn to the parsed body of the receipt document and write it back in the canonical form."""
    body = json.loads(answer["r"][1])
    fn(body["materialization"])
    answer["r"][1] = rc.canon(body)


def breaks(inputs, rust, go):
    """name -> (inputs, rust, go, what has to show): 'divergent', 'problem' or 'coverage'."""
    out = {}

    def make(name, want, fn):
        r, g = copy.deepcopy(rust), copy.deepcopy(go)
        fn(r, g)
        out[name] = (inputs, r, g, want)

    i = sm.first(rust, ok)
    d = sm.first(rust, with_defaults(1))
    d2 = sm.first(rust, with_defaults(2))
    n = sm.first(rust, lambda a: a["r"][0] == "err" and a["r"][1] == "not_flat")
    zero = "sha256:" + "0" * 64

    def one_and_both(name, fn, idx=None):
        """The same break in one language (the comparison sees it) and in both (the reference sees it)."""
        k = idx if idx is not None else i
        make("%s (one language)" % name, "divergent", lambda r, g: fn(g[k]))
        make("%s (both languages)" % name, "problem", lambda r, g: (fn(r[k]), fn(g[k])))

    one_and_both("the version of the receipt layout is 2", lambda a: edit(a, lambda b: b.__setitem__("receipt_schema_version", 2)))
    one_and_both("the output digest is the schema digest", lambda a: edit(a, lambda b: b.__setitem__("output_digest", b["schema_digest"])))
    one_and_both("the input digest is not the one of the authored input", lambda a: edit(a, lambda b: b.__setitem__("input_digest", zero)))
    one_and_both("the schema digest is taken over other bytes", lambda a: edit(a, lambda b: b.__setitem__("schema_digest", zero)))
    one_and_both("the schema name is written as the version", lambda a: edit(a, lambda b: b.__setitem__("schema_name", b["schema_version"])))
    one_and_both("the schema version is empty", lambda a: edit(a, lambda b: b.__setitem__("schema_version", "")))
    one_and_both("an applied default is dropped", lambda a: edit(a, lambda b: b["applied_defaults"].pop(0)), d)
    one_and_both("an applied default is listed twice", lambda a: edit(a, lambda b: b["applied_defaults"].append(b["applied_defaults"][0])), d)
    one_and_both("the value digest of an applied default is over a part of the value", lambda a: edit(a, lambda b: b["applied_defaults"][0].__setitem__("value_digest", zero)), d)
    one_and_both("the rule of an applied default is not the literal", lambda a: edit(a, lambda b: b["applied_defaults"][0].__setitem__("rule", "default")), d)
    one_and_both("the evidence entries are not in order", lambda a: edit(a, lambda b: b["applied_defaults"].reverse()), d2)
    one_and_both("every field is listed as defaulted", lambda a: edit(a, lambda b: b["applied_defaults"].append({"path": "$.~", "rule": "schema-default", "value_digest": zero})))
    one_and_both("a derived value is listed", lambda a: edit(a, lambda b: b["derived_values"].append("x")))
    one_and_both("the release fields are written in each other's place", lambda a: edit(a, lambda b: b.__setitem__("primitive_release", b["primitive_build_hash"])))
    one_and_both("the authored input is another text", lambda a: a["r"].__setitem__(2, a["r"][2] + " "))
    one_and_both("the binding maps a path to itself", lambda a: a["r"][3].__setitem__(next(iter(a["r"][3])), next(iter(a["r"][3]))), d)
    one_and_both("the binding lacks a path", lambda a: a["r"][3].pop(next(iter(a["r"][3]))), d)
    one_and_both("the projection is another text", lambda a: a["r"].__setitem__(5, a["r"][5][:-3] + "x" + a["r"][5][-2:]))
    one_and_both("the expected keys lose a key", lambda a: a["r"].__setitem__(4, a["r"][4][1:]))
    # the two fields that may differ, and only they
    make("the engine identity is the other language's", "problem", lambda r, g: edit(g[i], lambda b: b.__setitem__("engine_identity", cr.IDENTITY["rust"])))
    make("the engine identity is empty", "problem", lambda r, g: edit(r[i], lambda b: b.__setitem__("engine_identity", "")))
    make("the engine version is empty", "problem", lambda r, g: edit(g[i], lambda b: b.__setitem__("engine_version", "")))
    make("the engine version is the engine identity", "problem", lambda r, g: edit(g[i], lambda b: b.__setitem__("engine_version", b["engine_identity"])))

    def other_version(r, g):
        edit(g[i], lambda b: b.__setitem__("engine_version", "9.9.9"))
    make("a different engine version is permitted and not reported", "none", other_version)
    # refusals
    make("an acceptance turned into a refusal (one language)", "divergent", lambda r, g: g[i].update(r=["err", "omitted_optional", "a"]))
    make("an acceptance turned into a refusal (both languages)", "problem", lambda r, g: (r[i].update(r=["err", "set", "a"]), g[i].update(r=["err", "set", "a"])))
    make("a refusal turned into a receipt (one language)", "divergent", lambda r, g: r[n].update(r=copy.deepcopy(rust[i]["r"])))
    make("a refusal turned into a receipt (both languages)", "problem", lambda r, g: (r[n].update(r=copy.deepcopy(rust[i]["r"])), g[n].update(r=copy.deepcopy(go[i]["r"]))))
    make("one refusal named as another in one language", "divergent", lambda r, g: g[n]["r"].__setitem__(1, "set"))
    make("both languages name another refusal", "problem", lambda r, g: (r[n]["r"].__setitem__(1, "set"), g[n]["r"].__setitem__(1, "set")))
    make("the field of a refusal differs in one language", "divergent", lambda r, g: g[n]["r"].__setitem__(2, g[n]["r"][2] + "x"))
    make("both languages name another field", "problem", lambda r, g: (r[n]["r"].__setitem__(2, ""), g[n]["r"].__setitem__(2, "")))
    make("a reason outside the closed set", "problem", lambda r, g: (r[n]["r"].__setitem__(1, "weird"), g[n]["r"].__setitem__(1, "weird")))
    make("an answer in no known form", "problem", lambda r, g: (r[i].update(r=["maybe"]), g[i].update(r=["maybe"])))
    make("an answer missing from one language", "problem", lambda r, g: g.pop(i))
    make("an answer to an input that was not given", "problem", lambda r, g: g.update({10 ** 9: copy.deepcopy(g[i])}))

    def no_defaults(r, g):
        # a generator that no longer reaches a receipt with an applied default: those inputs and their answers are gone
        gone = [k for k in rust if with_defaults(1)(rust[k])]
        for k in gone:
            r.pop(k)
            g.pop(k)
        return gone
    r2, g2 = copy.deepcopy(rust), copy.deepcopy(go)
    gone = no_defaults(r2, g2)
    out["a generator that no longer reaches a receipt with an applied default"] = ({k: v for k, v in inputs.items() if k not in gone}, r2, g2, "coverage")
    return out


def main():
    inputs, rust, go = cr.load(sys.argv[1]), cr.load(sys.argv[2]), cr.load(sys.argv[3])
    problems, divergences, answers = cr.check(inputs, rust, go)
    if problems or divergences:
        print("self-test: the unbroken pair is not clean (%d problems, %d divergences)" % (len(problems), len(divergences)))
        sys.exit(1)
    missed = 0
    all_breaks = breaks(inputs, rust, go)
    for name, (ins, r, g, want) in all_breaks.items():
        problems, divergences, answers = cr.check(ins, r, g)
        seen = {"divergent": bool(divergences), "problem": bool(problems), "coverage": any(answers[x] == 0 for x in cr.REACHED),
                "none": not divergences and not problems}[want]
        print("  %-76s %s" % (name, ("red" if want != "none" else "clean") if seen else "NOT NOTICED"))
        missed += not seen
    total = len(all_breaks)
    for label, path in (("a repeated id in the inputs", sys.argv[1]), ("a repeated id in the dump of rust", sys.argv[2]), ("a repeated id in the dump of go", sys.argv[3])):
        seen = sm.repeated_id_is_refused(path, label)
        print("  %-76s %s" % (label, "red" if seen else "NOT NOTICED"))
        missed += not seen
        total += 1
    if missed:
        print("self-test: %d of %d breaks were not noticed" % (missed, total))
        sys.exit(1)
    print("self-test: %d breaks, every one turns the comparison red (and the permitted difference does not)" % total)


if __name__ == "__main__":
    main()
