#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the dumps of the ReadDiff differential (rust/tests/parity_diffread_dump.rs, go/parity_diffread_dump_test.go) and check the promises of the reader.

    compare_diffread.py INPUTS RUST_OUT GO_OUT [--show N]

Exit status 1 when the two languages answer differently on any input (there is no list of known differences: a difference is decided, not allowed), when an answer
breaks a promise of the reader that holds in both, or when the inputs do not reach a reason of the reader.

The promises, checked on each language's answer by itself:
  * a refusal names a reason of the closed set, and an offset that is not beyond the end of the input;
  * a diff that is read is written back as the bytes it was read from (the reader accepts exactly the canonical writing), and its version is 1;
  * ReadDiffVersion refuses what ReadDiff refuses for the same reason and offset when it refuses at all, and says a version other than 1 exactly when ReadDiff
    refuses with the reason "version";
  * the input is valid UTF-8 when a diff is read.
The coverage: every reason of the closed set is the answer to at least one input, so that the generator reaches the check it is meant to exercise.
"""
import argparse
import collections
import json
import sys

REASONS = ("not_utf8", "syntax", "depth", "duplicate_key", "unsupported_number", "shape", "path", "order", "no_change", "not_canonical", "version")


def load(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out[d["id"]] = d
    return out


def promises(name, case, ans):
    """The problems of one language's answer to one input."""
    b = bytes.fromhex(case["hex"])
    r, v = ans["r"], ans["v"]
    out = []
    if r[0] == "err":
        if r[1] not in REASONS:
            out.append("%s: reason %r is not in the closed set" % (name, r[1]))
        if not isinstance(r[2], int) or r[2] < 0 or r[2] > len(b):
            out.append("%s: offset %r is outside the input of %d bytes" % (name, r[2], len(b)))
    elif r[0] == "ok":
        try:
            text = b.decode("utf-8")
        except UnicodeDecodeError:
            out.append("%s: a diff was read from bytes that are not UTF-8" % name)
            text = None
        if text is not None and r[1] != text:
            out.append("%s: the diff read is not written back as it was read" % name)
        if v != ["ok", 1]:
            out.append("%s: a diff was read and the version is %s" % (name, v))
    else:
        out.append("%s: the answer %r is neither ok nor err" % (name, r[0]))
    if v[0] == "err":
        if r != v:
            out.append("%s: ReadDiffVersion refuses (%s) and ReadDiff says %s" % (name, v[1:], r[:3] if r[0] == "err" else "ok"))
    elif v[0] == "ok":
        if (v[1] != 1) != (r[0] == "err" and r[1] == "version"):
            out.append("%s: version %s, and ReadDiff answers %s" % (name, v[1], r[:2]))
    return out


def check(inputs, rust, go):
    """-> (problems, divergences, reasons): lists of (id, text) and a Counter of the reasons the inputs reach."""
    problems, divergences, reasons = [], [], collections.Counter()
    for i, case in inputs.items():
        if i not in rust or i not in go:
            problems.append((i, "no answer from %s" % ("rust" if i not in rust else "go")))
            continue
        for name, ans in (("rust", rust[i]), ("go", go[i])):
            problems += [(i, p) for p in promises(name, case, ans)]
        if rust[i]["r"] != go[i]["r"] or rust[i]["v"] != go[i]["v"]:
            divergences.append((i, "rust %s | %s   go %s | %s" % (str(rust[i]["r"])[:100], rust[i]["v"], str(go[i]["r"])[:100], go[i]["v"])))
        if rust[i]["r"][0] == "err":
            reasons[rust[i]["r"][1]] += 1
        else:
            reasons["ok"] += 1
    for extra in sorted(set(rust) | set(go)):
        if extra not in inputs:
            problems.append((extra, "an answer to an input that was not given"))
    return problems, divergences, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    inputs, rust, go = load(args.inputs), load(args.rust), load(args.go)
    problems, divergences, reasons = check(inputs, rust, go)
    bad = False
    print("inputs: %d; answers by reason: %s" % (len(inputs), ", ".join("%s %d" % kv for kv in sorted(reasons.items()))))
    tags = collections.Counter(inputs[i]["tag"] for i, _ in divergences)
    for missing in [r for r in REASONS if reasons[r] == 0]:
        print("COVERAGE: no input is refused with the reason %r" % missing)
        bad = True
    if divergences:
        bad = True
        print("DIVERGENT: %d inputs, by family: %s" % (len(divergences), dict(tags)))
        for i, text in divergences[: args.show]:
            print("  #%d [%s] %r\n      %s" % (i, inputs[i]["tag"], bytes.fromhex(inputs[i]["hex"])[:120], text))
    if problems:
        bad = True
        print("PROMISES: %d broken" % len(problems))
        for i, text in problems[: args.show]:
            print("  #%d [%s] %s" % (i, inputs[i]["tag"] if i in inputs else "?", text))
    if bad:
        sys.exit(1)
    print("ReadDiff: rust and go agree on every input, the promises hold, every reason is reached")


if __name__ == "__main__":
    main()
