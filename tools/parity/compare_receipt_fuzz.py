#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the dumps of the two receipt fuzz replays (rust/tests/receipt_fuzz_seeds.rs, go/receipt_fuzz_test.go) with each other.

    compare_receipt_fuzz.py DIR [--show N]

DIR holds rust.trace, go.trace (one line per mutation of the deterministic round: its number, how it ended, the input in hex, and for an input left to the parser the reason),
rust.jsonl, go.jsonl (the receipts the seeds and the mutations made, the dumps of the engine harness) written by the two replays. Exit status 1 when

 * the two rounds did not draw the same inputs (the mutation is the same algorithm from the same state in both languages: a difference is a defect of the replay);
 * the two languages ended differently on an input, except for the ONE quarantined case below;
 * the records are not exactly the ones the traces call for, in EACH language: every seed has its record, and every mutation that the language's own trace says ended as a
   receipt has its record (up to the cap of the dump, the same constant in both replays), and no record is there without that; a record missing from both languages is as red
   as one missing from one (comparing the two dumps with each other cannot see it);
 * a receipt that both made is not the same bytes, the engine identity apart (decision M: the only fields that may differ are engine_identity and engine_version, and the dumps
   hold the fixture version of both);
 * the rounds did not reach every end, or no receipt was made.

The quarantined case, found by this very comparison on the first run (see docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 19), is ONE direction of ONE parser difference: the
Rust parser refuses with the reason `syntax: invalid indentation in quoted scalar` (the reason is in its trace, not guessed) and the Go parser reads the same input, and the
input has a line break inside a quote followed by a character that is not a space (`a: "x`, newline, `y"`: Go reads "x y"). It is a difference of the parsers, not of the receipt,
and it is NOT normalized away: it is counted and listed on every run. The other direction (Go refuses, Rust reads), the same direction for another Rust reason, and an input
without that line break are red. The parsers' own differential is the place that has to close it.
"""
import argparse
import collections
import json
import os
import sys


# the one quarantined case: the Rust reason (from its trace) of the one direction of the parsers' difference
QUARANTINED_RUST_REASON = "syntax: invalid indentation in quoted scalar"
# the cap of the receipts a replay dumps for the mutations; the constant DUMPED / receiptFuzzDumped of the two replays
DUMP_CAP = 1500
MUTATION_ID = 1_000_000


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


def check(rust_trace, go_trace, rust_receipts, go_receipts, seed_count):
    """-> (problems, explained, ends): lists of (id, text) and a Counter of the ends of the Rust round."""
    problems, explained, ends = [], [], collections.Counter()
    if len(rust_trace) != len(go_trace):
        problems.append((-1, "the rounds have %d and %d mutations" % (len(rust_trace), len(go_trace))))
    for a, b in zip(rust_trace, go_trace):
        n = a[0]
        if len(a) != 4 or len(b) != 4:
            problems.append((n, "a trace line that is not number, end, input, reason"))
            continue
        if a[0] != b[0] or a[2] != b[2]:
            problems.append((n, "the two rounds drew different inputs: %s and %s" % (a[2][:60], b[2][:60])))
            continue
        ends[a[1]] += 1
        if a[1] == b[1]:
            continue
        text = bytes.fromhex(a[2])[2:].decode("utf-8", "replace")
        if a[1] == "Left" and b[1] != "Left" and a[3] == QUARANTINED_RUST_REASON and quoted_line_break_at_column_zero(text):
            explained.append((n, "rust %s (%s), go %s: %r" % (a[1], a[3], b[1], text[:80])))
        else:
            problems.append((n, "rust ends as %s (%s), go as %s (%s): %r" % (a[1], a[3], b[1], b[3], text[:80])))
    # the records: exactly what each language's own trace and the seeds call for
    for lang, trace, records in (("rust", rust_trace, rust_receipts), ("go", go_trace, go_receipts)):
        want = set(range(seed_count))
        made = [MUTATION_ID + int(t[0]) for t in trace if len(t) == 4 and t[1] == "Receipt"]
        want |= set(made[:DUMP_CAP])
        for i in sorted(want - set(records)):
            problems.append((i, "%s: the record of the %s %s is missing" % (lang, "seed" if i < MUTATION_ID else "mutation", i if i < MUTATION_ID else i - MUTATION_ID)))
        for i in sorted(set(records) - want):
            problems.append((i, "%s: a record for the input %s that its own trace does not call for" % (lang, i)))
        for i in sorted(set(records) & want):
            if i >= MUTATION_ID and records[i][0] != "ok":
                problems.append((i, "%s: the record of the mutation %s is not a receipt" % (lang, i - MUTATION_ID)))
    for i in sorted(set(rust_receipts) & set(go_receipts)):
        if rust_receipts[i] != go_receipts[i]:
            problems.append((i, "the receipt of the input %s is not the same bytes" % i))
    return problems, explained, ends


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    d = args.dir.rstrip("/")
    seeds = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "testdata", "p5", "receipt-fuzz", "seeds.json")
    with open(seeds, encoding="utf-8") as f:
        seed_count = len(json.load(f))
    problems, explained, ends = check(split_lines(d + "/rust.trace"), split_lines(d + "/go.trace"), receipts(d + "/rust.jsonl", "cic-primitive-forms-rust"),
                                      receipts(d + "/go.jsonl", "cic-primitive-forms-go"), seed_count)
    bad = False
    print("mutations: %d; ends: %s" % (sum(ends.values()), ", ".join("%s %d" % kv for kv in sorted(ends.items()))))
    for e in REACHED:
        if ends[e] == 0:
            print("COVERAGE: the round never ends as %s" % e)
            bad = True
    print("quarantined parser difference (rust refuses \"%s\", go reads; not normalized away): %d inputs" % (QUARANTINED_RUST_REASON, len(explained)))
    for n, text in explained[: args.show]:
        print("  #%s %s" % (n, text))
    if problems:
        bad = True
        print("DIVERGENT: %d" % len(problems))
        for n, text in problems[: args.show]:
            print("  #%s %s" % (n, text))
    if bad:
        sys.exit(1)
    print("receipt fuzz replay: rust and go drew the same inputs, ended the same on every one but the quarantined parser case, has every record its traces call for, and made the same receipts")


if __name__ == "__main__":
    main()
