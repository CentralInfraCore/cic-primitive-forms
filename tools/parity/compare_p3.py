#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the Rust and Go outcomes of the P3 differential (tools/parity/gen_p3.py, the `parity_p3_dump` tests).

    compare_p3.py INPUTS RUST_OUT GO_OUT [--show N]

Both sides must answer every input, in the same order, with the same outcome: the stage that refused it, or, for an accepted one, the
emitted normalized object, the origin of every value, and the rule, layer, source path and reason of every position for both
operations. Maps are compared as maps (the order of their keys is not a result). Exit status 1 on any difference, a missing line, an
unexpected stage, or a run in which nothing was accepted (a comparison of refusals proves nothing).
"""
import argparse
import json
import sys

STAGES = {"ok", "compile", "parse", "normalize", "resolve", "grammar"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=3)
    a = ap.parse_args()
    ins, rust, go = load(a.inputs), load(a.rust), load(a.go)
    bad = []
    if not (len(ins) == len(rust) == len(go)):
        print("COUNT: inputs %d, rust %d, go %d" % (len(ins), len(rust), len(go)))
        return 1
    stages = {}
    for i, (case, r, g) in enumerate(zip(ins, rust, go)):
        for side, o in (("rust", r), ("go", g)):
            if o.get("id") != case["id"] or o.get("stage") not in STAGES:
                bad.append((i, "INVALID " + side, o.get("stage")))
        if r != g:
            bad.append((i, "DIFFERENT", {"rust": r, "go": g}))
        stages[r.get("stage")] = stages.get(r.get("stage"), 0) + 1
    print("p3 differential: %d inputs, outcomes %s, %d differences" % (len(ins), dict(sorted(stages.items())), len(bad)))
    for i, why, what in bad[: a.show]:
        print("  input %d: %s" % (i, why))
        print("    " + json.dumps(ins[i], sort_keys=True)[:600])
        print("    " + json.dumps(what, sort_keys=True)[:900])
    if stages.get("ok", 0) == 0:
        print("NOTHING ACCEPTED: the comparison proves nothing")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
