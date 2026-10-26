#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the Rust and Go answers of `diff` with each other and with the model, and check the properties of ADR 0008 on the dumps (P4 step D3b).

    compare_diff.py INPUTS RUST_OUT GO_OUT [--show N]

The inputs are tools/parity/gen_diff.py's, the outputs are the `parity_diff_dump` tests'. Checked, per input:
  parity     the four answers (A to B, B to A, A to A, the shuffled pair) are the same in the two languages, byte for byte, errors with their code, phase,
             location, path and operand;
  the model  tools/parity/diff_model.py, written separately from the ADR, gives the same canonical bytes for A to B, B to A and A to A (valid inputs);
  stability  the pair with every list and set in another order gives the bytes of the pair as drawn (ADR 0008: list and set order are no difference);
  identity   A against A is empty;
  inversion  B to A is A to B turned round: the same paths in the same order, added and removed exchanged with their fragments, previous and next
             exchanged in a value change, a declaration change, a representation entry, the two lists of a set change exchanged;
  canonical  every answer is its own canonical form (strict JSON, no floats, keys sorted, no whitespace, nothing outside the writing rules of ADR 0008);
  faults     an injected fault (an unknown field, a duplicate identity, an unsupported number, a context mismatch) gives exactly the error the generator states,
             with the operand of the side it was put on, A first.
The run must be worth something: every kind of event, representation entries, scoped and unscoped diffs, every error code and every operand appear often
enough. Exit status 1 on any difference, any broken property, a missing or extra line, or a run that is too thin.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diff_model as dm  # noqa: E402

KINDS = ["added", "removed", "value_change", "policy_declaration_change", "set_change"]
CODES = ["UNKNOWN_FIELD", "PATH_DUPLICATE_IDENTITY", "DIFF_UNSUPPORTED_VALUE", "DIFF_CONTEXT_MISMATCH"]
MIN_KIND, MIN_REPRESENTATION, MIN_SCOPED, MIN_EMPTY, MIN_NONEMPTY, MIN_CODE = 150, 100, 800, 100, 1500, 25
EMPTY = '{"changes":[],"representation":[],"version":1}'


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def segs_of(raw):
    return [("f", s[1]) if s[0] == "f" else ("i", tuple((k, v) for k, v in s[1])) for s in raw]


def mirror_event(e):
    m = dict(e)
    if e["kind"] == "added":
        m = {"kind": "removed", "path": e["path"], "previous": e["next"]}
    elif e["kind"] == "removed":
        m = {"kind": "added", "path": e["path"], "next": e["previous"]}
    elif e["kind"] == "value_change":
        m = {"kind": "value_change", "path": e["path"], "previous": e["next"], "next": e["previous"]}
    elif e["kind"] == "set_change":
        m = {"kind": "set_change", "path": e["path"], "added": e["removed"], "removed": e["added"]}
    else:
        m = {"kind": e["kind"], "path": e["path"], "changed": {k: {"previous": v["next"], "next": v["previous"]} for k, v in e["changed"].items()}}
    return m


def mirror_rep(r):
    return {"kind": r["kind"], "path": r["path"], "previous": r["next"], "next": r["previous"]}


def check_inversion(ab, ba, i, problems):
    x, y = json.loads(ab), json.loads(ba)
    if [mirror_event(e) for e in x["changes"]] != y["changes"]:
        # the order is by path then kind, and exchanging added and removed can change the order of two events at one path: compare as sets, then the order
        if sorted(canon(mirror_event(e)) for e in x["changes"]) != sorted(canon(e) for e in y["changes"]):
            problems.append((i, "inversion: B to A is not A to B turned round"))
        elif [e["path"] for e in x["changes"]] != [e["path"] for e in y["changes"]]:
            problems.append((i, "inversion: the paths are in another order"))
    if [mirror_rep(r) for r in x["representation"]] != y["representation"]:
        problems.append((i, "inversion: the representation entries"))


def check_case(case, rr, gr, problems, stats):
    i = case["id"]
    for name in ("ab", "ba", "aa", "abs"):
        if rr[name] != gr[name]:
            problems.append((i, "parity: %s differs: rust %s / go %s" % (name, canon(rr[name])[:200], canon(gr[name])[:200])))
            return
    inj = case["inject"]
    scope = None if case["scope_segs"] is None else [segs_of(s) for s in case["scope_segs"]]
    stats["scoped" if scope is not None else "unscoped"] += 1
    if inj:
        want = [inj["code"], inj["phase"], inj["loc"], inj["path"]]
        side = inj["side"]
        flip = {"a": "b", "b": "a", "": ""}
        expected = {"ab": side, "ba": flip[side], "abs": side}
        stats["code"][inj["code"]] += 1
        for name, operand in expected.items():
            got = rr[name].get("err")
            if got != want + [operand]:
                problems.append((i, "faults: %s is %s, expected %s" % (name, got, want + [operand])))
        if side:
            stats["operand"][side] += 1
        if inj["kind"] == "ctx":
            return
        if side == "a":
            if rr["aa"].get("err") != want + ["a"]:
                problems.append((i, "faults: aa is %s" % rr["aa"]))
        elif rr["aa"].get("ok") != EMPTY:
            problems.append((i, "faults: A against A is not empty"))
        return
    for name in ("ab", "ba", "aa", "abs"):
        if "ok" not in rr[name]:
            problems.append((i, "a valid pair was refused (%s): %s" % (name, rr[name])))
            return
    ab, ba, aa, abs_ = (rr[n]["ok"] for n in ("ab", "ba", "aa", "abs"))
    try:
        if os.environ.get("COMPARE_DIFF_NO_MODEL"):          # (a hook for selftest_diff.py only: it proves that each property is checked without the model)
            raise dm.Model("skipped")
        want = {"ab": dm.diff(case["a"], case["b"], TREE, scope), "ba": dm.diff(case["b"], case["a"], TREE, scope), "aa": dm.diff(case["a"], case["a"], TREE, scope),
                "abs": dm.diff(case["a2"], case["b2"], TREE, scope)}
    except dm.Model as e:
        if not os.environ.get("COMPARE_DIFF_NO_MODEL"):
            problems.append((i, "the model refuses a valid pair: %s" % e))
            return
        want = None
    for name, got in (("ab", ab), ("ba", ba), ("aa", aa), ("abs", abs_)):
        if want is not None and got != want[name]:
            problems.append((i, "model: %s differs\n   impl  %s\n   model %s" % (name, got[:300], want[name][:300])))
    if abs_ != ab:
        problems.append((i, "stability: the shuffled pair gives other bytes"))
    if aa != EMPTY:
        problems.append((i, "identity: A against A is not empty"))
    for name in ("ab", "ba", "aa", "abs"):
        s = rr[name]["ok"]
        try:
            if canon(json.loads(s, parse_float=_no_float)) != s:
                problems.append((i, "canonical: %s is not its own canonical form" % name))
        except ValueError as e:                                  # a float, or not JSON at all
            problems.append((i, "canonical: %s: %s" % (name, e)))
    check_inversion(ab, ba, i, problems)
    out = json.loads(ab)
    for e in out["changes"]:
        stats["kind"][e["kind"]] += 1
    stats["representation"] += len(out["representation"])
    stats["empty" if not out["changes"] and not out["representation"] else "nonempty"] += 1


def _no_float(text):
    raise ValueError("a float in the canonical bytes: " + text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=5)
    a = ap.parse_args()
    global TREE
    inputs = load(a.inputs)
    TREE = inputs[0]["tree"]
    cases, rust, go = inputs[1:], load(a.rust), load(a.go)
    problems = []
    stats = {"kind": {k: 0 for k in KINDS}, "code": {c: 0 for c in CODES}, "operand": {"a": 0, "b": 0}, "representation": 0, "scoped": 0, "unscoped": 0, "empty": 0, "nonempty": 0}
    if not (len(cases) == len(rust) == len(go)):
        print("the dumps do not have one line per input: %d inputs, rust %d, go %d" % (len(cases), len(rust), len(go)))
        return 1
    for c, r, g in zip(cases, rust, go):
        if not (c["id"] == r["id"] == g["id"]):
            problems.append((c["id"], "the dumps are not in the order of the inputs"))
            continue
        check_case(c, r, g, problems, stats)
    thin = []
    for k, n in stats["kind"].items():
        if n < MIN_KIND:
            thin.append("%s events: %d < %d" % (k, n, MIN_KIND))
    for k, n in stats["code"].items():
        if n < MIN_CODE:
            thin.append("%s: %d < %d" % (k, n, MIN_CODE))
    for k, n in stats["operand"].items():
        if n < MIN_CODE:
            thin.append("faults on operand %s: %d < %d" % (k, n, MIN_CODE))
    for label, n, need in (("representation entries", stats["representation"], MIN_REPRESENTATION), ("scoped diffs", stats["scoped"], MIN_SCOPED), ("empty diffs", stats["empty"], MIN_EMPTY), ("non-empty diffs", stats["nonempty"], MIN_NONEMPTY)):
        if n < need:
            thin.append("%s: %d < %d" % (label, n, need))
    for i, msg in problems[:a.show]:
        print("input %s: %s" % (i, msg))
    print("compared %d inputs (x4 diffs): %d problems; events %s, representation %d, scoped %d, empty %d, non-empty %d, faults %s" % (
        len(cases), len(problems), stats["kind"], stats["representation"], stats["scoped"], stats["empty"], stats["nonempty"], stats["code"]))
    if thin:
        print("THIN RUN: " + "; ".join(thin))
    return 1 if problems or thin else 0


TREE = None

if __name__ == "__main__":
    sys.exit(main())
