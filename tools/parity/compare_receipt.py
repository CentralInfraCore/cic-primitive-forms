#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the dumps of the flat-receipt differential (rust/tests/parity_receipt_dump.rs, go/parity_receipt_dump_test.go) with each other and with a reference.

    compare_receipt.py INPUTS RUST_OUT GO_OUT [--show N]

The inputs are the ones of the materialization differential (tools/parity/gen_materialize.py). The reference of an accepted case is the receipt that
tools/p5/receipt_cases.py computes from the schema text and from the projection and output digest that the generator's own reference gives (`want`), with Python's JSON
writer and hashlib, sharing no code with either language; the reference of a refused case is the refusal of the materialization (a receipt is refused where the
materialization is, with the same reason and field). Fixture data: the release fields and the engine are not a release (decision L of docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md).

Exit status 1 when the two languages answer differently on any input (the only two fields that may differ are `engine_identity` and `engine_version`, decision M; each
has to be the one its language is declared to write, and nothing else in the answer is masked), when either differs from the reference, when an answer is not of the form
["ok", document, authored_input, binding, expected_keys, projection] or ["err", reason, field] with a reason of the closed set, when a record id is repeated in a file, or when the
inputs do not reach every kind of answer: every refusal of the materialization, a receipt with no applied default, one with one, and one with several.
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "p5"))
import compare_materialize as cm  # noqa: E402
import receipt_cases as rc  # noqa: E402

DuplicateId = cm.DuplicateId
load = cm.load
REASONS = cm.REASONS + ("path_not_one_segment",)
# the names the two implementations write in `engine_identity`; the version is theirs too and may differ
IDENTITY = {"rust": "cic-primitive-forms-rust", "go": "cic-primitive-forms-go"}
REACHED = cm.REACHED + ("defaults:0", "defaults:1", "defaults:2+")

_IDENTITY_FRAGMENT = re.compile(r'"engine_identity":"([^"\\]*)"')
_VERSION_FRAGMENT = re.compile(r'"engine_version":"([^"\\]*)"')


def masked(lang, answer):
    """-> (answer with the two permitted fields replaced by the reference's, None) or (None, why not): the identity has to be the language's own, the version a text."""
    r = answer["r"]
    if r[0] != "ok":
        return r, None
    doc = r[1]
    ids, versions = _IDENTITY_FRAGMENT.findall(doc), _VERSION_FRAGMENT.findall(doc)
    if ids != [IDENTITY[lang]]:
        return None, "the engine_identity is %r, not %r" % (ids, IDENTITY[lang])
    if len(versions) != 1 or not versions[0] or versions[0] in IDENTITY.values():
        return None, "the engine_version is %r (a version text of its own, not empty and not an engine identity)" % (versions,)
    doc = _IDENTITY_FRAGMENT.sub('"engine_identity":"%s"' % rc.TOKEN, doc)
    doc = _VERSION_FRAGMENT.sub('"engine_version":"%s"' % rc.ENGINE_VERSION, doc)
    return [r[0], doc] + list(r[2:]), None


def reference(case):
    want = case["want"]
    if want[0] != "ok":
        return want
    authored, document, binding = rc.receipt_for(case["schema"], want[2], want[3])
    return ["ok", document, authored, binding, case["keys"], want[2]]


def check(inputs, rust, go):
    """-> (problems, divergences, answers): lists of (id, text) and a Counter of what the inputs reach."""
    problems, divergences, answers = [], [], collections.Counter()
    for i, case in inputs.items():
        if i not in rust or i not in go:
            problems.append((i, "no answer from %s" % ("rust" if i not in rust else "go")))
            continue
        want = reference(case)
        seen = {}
        for lang, ans in (("rust", rust[i]), ("go", go[i])):
            r = ans["r"]
            if r[0] == "err":
                if len(r) != 3 or r[1] not in REASONS or not isinstance(r[2], str):
                    problems.append((i, "%s: the refusal %r is not of the closed set" % (lang, str(r)[:80])))
            elif r[0] != "ok" or len(r) != 6:
                problems.append((i, "%s: the answer %r is neither ok nor err" % (lang, str(r)[:80])))
            m, why = masked(lang, ans) if (r[0] == "err" or (r[0] == "ok" and len(r) == 6 and isinstance(r[1], str))) else (None, "no document")
            if why:
                problems.append((i, "%s: %s" % (lang, why)))
            elif m != want:
                problems.append((i, "%s answers %s, the reference %s" % (lang, str(m)[:150], str(want)[:150])))
            seen[lang] = m
        if seen["rust"] != seen["go"]:
            divergences.append((i, "rust %s   go %s" % (str(seen["rust"])[:120], str(seen["go"])[:120])))
        r0 = want
        if r0[0] == "ok":
            n = len(json.loads(r0[1])["materialization"]["applied_defaults"])
            answers["ok"] += 1
            answers["defaults:%s" % ("2+" if n > 1 else n)] += 1
        else:
            answers[r0[1]] += 1
    for extra in sorted(set(rust) | set(go)):
        if extra not in inputs:
            problems.append((extra, "an answer to an input that was not given"))
    return problems, divergences, answers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    try:
        inputs, rust, go = load(args.inputs), load(args.rust), load(args.go)
    except DuplicateId as e:
        print("PROMISES: %s" % e)
        sys.exit(1)
    problems, divergences, answers = check(inputs, rust, go)
    bad = False
    print("inputs: %d; answers: %s" % (len(inputs), ", ".join("%s %d" % kv for kv in sorted(answers.items()))))
    for missing in [a for a in REACHED if answers[a] == 0]:
        print("COVERAGE: no input is answered with %r" % missing)
        bad = True
    if divergences:
        bad = True
        tags = collections.Counter(inputs[i]["tag"] for i, _ in divergences)
        print("DIVERGENT: %d inputs, by family: %s" % (len(divergences), dict(tags)))
        for i, text in divergences[: args.show]:
            print("  #%d [%s] %s\n      %s" % (i, inputs[i]["tag"], inputs[i]["doc"][:100].replace("\n", " | "), text))
    if problems:
        bad = True
        print("PROMISES: %d broken" % len(problems))
        for i, text in problems[: args.show]:
            print("  #%d [%s] %s" % (i, inputs[i]["tag"] if i in inputs else "?", text))
    if bad:
        sys.exit(1)
    print("flat receipt: rust and go agree with each other and with the reference on every input (only engine_identity and engine_version may differ), every kind of answer is reached")


if __name__ == "__main__":
    main()
