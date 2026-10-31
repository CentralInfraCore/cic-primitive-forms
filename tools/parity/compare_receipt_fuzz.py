#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the dumps of the two receipt fuzz replays (rust/tests/receipt_fuzz_seeds.rs, go/receipt_fuzz_test.go) with each other.

    compare_receipt_fuzz.py DIR [--show N]

DIR holds rust.trace, go.trace (one line per mutation of the deterministic round: its number, how it ended, the input in hex), rust.jsonl, go.jsonl (the receipts the seeds and the
mutations made, the dumps of the engine harness) written by the two replays. Exit status 1 when

 * the two rounds did not draw the same inputs (the mutation is the same algorithm from the same state in both languages: a difference is a defect of the replay);
 * the two languages ended differently on an input, except for the ONE class the parsers are known to disagree on (below);
 * a receipt that both made is not the same bytes, the engine identity apart (decision M: the only fields that may differ are engine_identity and engine_version, and the dumps
   hold the fixture version of both);
 * the rounds did not reach every end, or no receipt was made.

The one class, found by this very comparison on the first run (see docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 19): a quoted scalar continued on a line that starts at
column 0 (`a: "x` and then `y"`). The Go parser reads it ("x y"), the Rust parser refuses it ("invalid indentation in quoted scalar"). It is a difference of the parsers, not of the
receipt, and it is NOT normalized away: it is counted and listed here, only an input that has such a line break inside a quote may end as Left in one language and as anything
else in the other, and the number is printed on every run. The parsers' own differential is the place that has to close it.
"""
import argparse
import collections
import json
import sys


def split_lines(path):
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n").split("\t") for line in f]


def quoted_line_break_at_column_zero(text):
    """True when a line break inside a single or double quoted scalar is followed by a character that is not a space or a line break."""
    double = single = False
    i = 0
    while i < len(text):
        c = text[i]
        if double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                double = False
            elif c == "\n" and i + 1 < len(text) and text[i + 1] not in " \n":
                return True
        elif single:
            if c == "'":
                single = False
            elif c == "\n" and i + 1 < len(text) and text[i + 1] not in " \n":
                return True
        elif c == '"':
            double = True
        elif c == "'":
            single = True
        i += 1
    return False


def receipts(path, identity):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d["id"] in out:
                raise SystemExit("%s: the id %r is there twice" % (path, d["id"]))
            r = d["r"]
            if r[0] == "ok":
                r = [r[0], r[1].replace('"engine_identity":"%s"' % identity, '"engine_identity":"X"')] + r[2:]
            out[d["id"]] = r
    return out


REACHED = ("Left", "Identity", "Version", "Release", "Engine", "Document", "Capability", "Repeated", "Segment", "Receipt")


def check(rust_trace, go_trace, rust_receipts, go_receipts):
    """-> (problems, explained, ends): lists of (n, text) and a Counter of the ends of the Rust round."""
    problems, explained, ends = [], [], collections.Counter()
    if len(rust_trace) != len(go_trace):
        problems.append((-1, "the rounds have %d and %d mutations" % (len(rust_trace), len(go_trace))))
    for a, b in zip(rust_trace, go_trace):
        n = a[0]
        if a[0] != b[0] or a[2] != b[2]:
            problems.append((n, "the two rounds drew different inputs: %s and %s" % (a[2][:60], b[2][:60])))
            continue
        ends[a[1]] += 1
        if a[1] == b[1]:
            continue
        text = bytes.fromhex(a[2])[2:].decode("utf-8", "replace")
        if "Left" in (a[1], b[1]) and quoted_line_break_at_column_zero(text):
            explained.append((n, "rust %s, go %s: %r" % (a[1], b[1], text[:80])))
        else:
            problems.append((n, "rust ends as %s, go as %s: %r" % (a[1], b[1], text[:80])))
    explained_ids = {1_000_000 + int(n) for n, _ in explained}
    for i in sorted(set(rust_receipts) & set(go_receipts)):
        if rust_receipts[i] != go_receipts[i]:
            problems.append((i, "the receipt of the input %s is not the same bytes" % i))
    for i in sorted(set(rust_receipts) ^ set(go_receipts)):
        if i not in explained_ids:   # the receipt of an input that only one language reads is the explained class and nothing else
            problems.append((i, "only one language dumped a receipt for the input %s" % i))
    return problems, explained, ends


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    d = args.dir.rstrip("/")
    problems, explained, ends = check(split_lines(d + "/rust.trace"), split_lines(d + "/go.trace"), receipts(d + "/rust.jsonl", "cic-primitive-forms-rust"),
                                      receipts(d + "/go.jsonl", "cic-primitive-forms-go"))
    bad = False
    print("mutations: %d; ends: %s" % (sum(ends.values()), ", ".join("%s %d" % kv for kv in sorted(ends.items()))))
    for e in REACHED:
        if ends[e] == 0:
            print("COVERAGE: the round never ends as %s" % e)
            bad = True
    print("parser difference (quoted scalar continued at column 0, not normalized away): %d inputs" % len(explained))
    for n, text in explained[: args.show]:
        print("  #%s %s" % (n, text))
    if problems:
        bad = True
        print("DIVERGENT: %d" % len(problems))
        for n, text in problems[: args.show]:
            print("  #%s %s" % (n, text))
    if bad:
        sys.exit(1)
    print("receipt fuzz replay: rust and go drew the same inputs, ended the same on every one but the named parser class, and made the same receipts")


if __name__ == "__main__":
    main()
