#!/usr/bin/env python3
"""Build the regression corpus both parsers run (testdata/p1/parity.json).

    make_corpus.py inputs OUT_HEX                      write HAND + EXTRA of gen_inputs.py, hex-encoded, one per line
    make_corpus.py merge IN_HEX RUST GO OUT_JSON       pin the outcomes the Rust and the Go dump gave for them

An input both parsers agree on is a case with one expected outcome; an input they disagree on goes to
`known_divergences` with both outcomes, so a divergence stays visible and a fix shows up as a failing
pin. Contract and refusal outcomes agree when they are the same class (the same non-contract code is not
required: YAML_SYNTAX against UNSUPPORTED_SYNTAX is the same refusal for a different reason); the case
then keeps both codes under `rust` and `go` as `refused`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare  # noqa: E402
import gen_inputs  # noqa: E402


def inputs():
    seen, out = set(), []
    for t in gen_inputs.HAND + gen_inputs.EXTRA:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def same(r, g):
    if r == g:
        return True
    return "err" in r and "err" in g and "CONTRACT" not in (r["err"], g["err"])


def depth(j):
    if isinstance(j, dict):
        return 1 + max((depth(v) for v in j.values()), default=0)
    if isinstance(j, list):
        return 1 + max((depth(v) for v in j), default=0)
    return 0


def main():
    if sys.argv[1] == "inputs":
        with open(sys.argv[2], "w") as f:
            for t in inputs():
                f.write(t.encode("utf-8").hex() + "\n")
        return
    _, _, in_hex, rust, go, out = sys.argv
    ins = [bytes.fromhex(l.strip()).decode("utf-8") for l in open(in_hex)]
    rs = [json.loads(l) for l in open(rust)]
    gs = [json.loads(l) for l in open(go)]
    assert len(ins) == len(rs) == len(gs)
    for side, outs in (("rust", rs), ("go", gs)):
        for t, o in zip(ins, outs):
            why = compare.invalid(o)
            assert not why, "%s dump of %r is not in the dump format: %s" % (side, t[:60], why)
    cases, known = [], []
    for t, r, g in zip(ins, rs, gs):
        # serde_json refuses to read JSON nested deeper than 128; the deep trees are covered by the limit
        # tests, and the differential (compare.py) reads them without that bound.
        if max(depth(r), depth(g)) > 60:
            continue
        if r == g:
            cases.append({"input": t, "expect": r})
        elif same(r, g):
            cases.append({"input": t, "rust": r, "go": g, "refused": True})
        else:
            known.append({"input": t, "rust": r, "go": g})
    json.dump({"cases": cases, "known_divergences": known}, open(out, "w"), indent=1, ensure_ascii=True)
    open(out, "a").write("\n")
    print(len(cases), "agreed,", len(known), "known divergences")


main()
