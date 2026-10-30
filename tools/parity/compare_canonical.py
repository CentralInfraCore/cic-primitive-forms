#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the dumps of the canonical-bytes differential (rust/tests/parity_canonical_dump.rs, go/parity_canonical_dump_test.go) with each other and with the reference.

    compare_canonical.py INPUTS RUST_OUT GO_OUT [--show N]

Exit status 1 when the two languages answer differently on any input (there is no list of known differences: a difference is decided, not allowed), when either
differs from the reference of the generator (tools/parity/gen_canonical.py, written from engine section A and sharing no code with either), when an answer is not
of the form ["ok", bytes, digest] or ["err", reason] with a reason of the closed set, or when the inputs do not reach every family of answer: an accepted value,
and both refusals.
"""
import argparse
import collections
import json
import sys

REASONS = ("unsupported_value", "duplicate_key")


def load(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out[d["id"]] = d
    return out


def check(inputs, rust, go):
    """-> (problems, divergences, answers): lists of (id, text) and a Counter of what the inputs reach."""
    problems, divergences, answers = [], [], collections.Counter()
    for i, case in inputs.items():
        if i not in rust or i not in go:
            problems.append((i, "no answer from %s" % ("rust" if i not in rust else "go")))
            continue
        for name, ans in (("rust", rust[i]), ("go", go[i])):
            r = ans["r"]
            if r[0] == "err":
                if len(r) != 2 or r[1] not in REASONS:
                    problems.append((i, "%s: the refusal %r is not of the closed set" % (name, r)))
            elif r[0] != "ok" or len(r) != 3:
                problems.append((i, "%s: the answer %r is neither ok nor err" % (name, str(r)[:80])))
            if r != case["want"]:
                problems.append((i, "%s answers %s, the reference %s" % (name, str(r)[:100], str(case["want"])[:100])))
        if rust[i]["r"] != go[i]["r"]:
            divergences.append((i, "rust %s   go %s" % (str(rust[i]["r"])[:100], str(go[i]["r"])[:100])))
        r0 = rust[i]["r"]
        answers[r0[0] if r0[0] == "ok" or len(r0) < 2 else r0[1]] += 1
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
    inputs, rust, go = load(args.inputs), load(args.rust), load(args.go)
    problems, divergences, answers = check(inputs, rust, go)
    bad = False
    print("inputs: %d; answers: %s" % (len(inputs), ", ".join("%s %d" % kv for kv in sorted(answers.items()))))
    for missing in [a for a in ("ok",) + REASONS if answers[a] == 0]:
        print("COVERAGE: no input is answered with %r" % missing)
        bad = True
    if divergences:
        bad = True
        tags = collections.Counter(inputs[i]["tag"] for i, _ in divergences)
        print("DIVERGENT: %d inputs, by family: %s" % (len(divergences), dict(tags)))
        for i, text in divergences[: args.show]:
            print("  #%d [%s] %s\n      %s" % (i, inputs[i]["tag"], json.dumps(inputs[i]["spec"])[:160], text))
    if problems:
        bad = True
        print("PROMISES: %d broken" % len(problems))
        for i, text in problems[: args.show]:
            print("  #%d [%s] %s" % (i, inputs[i]["tag"] if i in inputs else "?", text))
    if bad:
        sys.exit(1)
    print("canonical bytes: rust and go agree with each other and with the reference on every input, every kind of answer is reached")


if __name__ == "__main__":
    main()
