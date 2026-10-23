#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the Rust and Go answers of `edit_scalar` and check the guarantees of ADR 0012 on the dumps, on their own (P3b step E4).

    compare_edit.py INPUTS RUST_OUT GO_OUT [--show N]

The inputs are tools/parity/gen_edit.py's, the outputs are the `parity_edit_dump` tests'. Checked, per input:
  guarantee 5  the two languages give the same answer for the edit, the no-op, the edit back and both orders of two edits (the text byte for byte,
               the span, whether anything changed, the code, phase, position and logical path of a refusal, the kind of an error outside the contract);
  guarantee 1  the bytes outside the span are the original's (the prefix before the original span, the suffix after it, and the new span starts
               where the old one did);
  guarantee 2  the token is written in the style the scalar had (the style before the edit, the style of the new span, and the one of the token
               are the same) and holds the new value in its canonical spelling (decimal integers, true/false, null; a quote doubled; backslash and quote escaped);
  guarantee 6  an edit to the value the scalar holds is the text byte for byte; an edit that is accepted can be edited back; the edit back restores
               the bytes when the original token already was the canonical spelling; two edits of independent positions are both accepted or both
               refused and give the same text in either order.
  guarantees 3 and 4  the result parses to the old tree with that one scalar replaced, and every position keeps its form (a mapping stays a mapping, a
               short position stays short, an expanded one stays expanded, the order of the entries is the same): the dumps carry the parsed tree of the
               original and of the result (each language's own parser, which the two languages must also agree on), and this script replaces the
               scalar in the tree of the original itself and compares. It is checked inside the library on every call too, and by the properties.
The run must also be worth something: every refusal code of the edit phase that a text can earn appears, enough edits were accepted, changed and
edited back, and enough pairs were compared. Exit status 1 on any difference, any broken guarantee, a missing or extra line, or a run that is too thin.
"""
import argparse
import json
import re
import sys

EDIT_CODES = ["EDIT_NOT_A_SCALAR", "EDIT_NO_SOURCE_VALUE", "EDIT_BLOCK_SCALAR", "EDIT_FLOW_CONTEXT", "EDIT_MULTILINE_SCALAR",
              "EDIT_VALUE_NOT_REPRESENTABLE", "EDIT_TYPE_MISMATCH", "EDIT_STYLE_CONFLICT", "EDIT_INVALID_RESULT"]
MIN_PER_CODE, MIN_ACCEPTED, MIN_CHANGED, MIN_NOOP, MIN_BACK_CANONICAL, MIN_COMMUTED = 5, 800, 400, 500, 300, 150


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def canonical(value, style):
    """The token an edit writes for the typed value in the style, or None when the style cannot carry it."""
    (kind, v), = value.items()
    if style == "single_quoted":
        return "'" + v.replace("'", "''") + "'" if kind == "string" else None
    if style == "double_quoted":
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' if kind == "string" else None
    if kind == "integer":
        return str(v)
    if kind == "boolean":
        return "true" if v else "false"
    if kind == "null":
        return "null"
    return v if kind == "string" else None


ACCESS = {"value", "access", "modify", "inherit", "default_injection", "conformance"}


def put(node, toks, new):
    """A copy of the tree with the scalar the tokens address replaced by `new` (the paths of gen_edit.py), looking through an expanded wrapper; None if it is not there."""
    if not toks:
        return new
    field, key = toks[0]
    if field and "map" in node:
        out, hit = [], False
        for k, v in node["map"]:
            if k == field and not hit:
                hit = True
                if len(toks) == 1 and "map" in v and {kk for kk, _ in v["map"]} == ACCESS:             # an expanded wrapper: its value is meant
                    v = {"map": [[kk, new if kk == "value" else vv] for kk, vv in v["map"]]}
                else:
                    v = put(v, toks[1:], new)
                if v is None:
                    return None
            out.append([k, v])
        return {"map": out} if hit else None
    if key and "seq" in node:
        out, hit = [], False
        for it in node["seq"]:
            if not hit and any(k == "to" and v == {"string": key} for k, v in it.get("map", [])):
                hit = True
                it = put(it, toks[1:], new)
                if it is None:
                    return None
            out.append(it)
        return {"seq": out} if hit else None
    return None


def style_of(token):
    return "single_quoted" if token.startswith("'") else "double_quoted" if token.startswith('"') else "plain"


def check_case(case, r, problems, stats):
    text, edit = case["yaml"].encode("utf-8"), r["edit"]            # the offsets of a span are bytes of the UTF-8 text, a BOM included
    i = case["id"]
    if edit["ok"]:
        stats["accepted"] += 1
        out, (s, e, style), changed = edit["text"].encode("utf-8"), edit["span"][:3], edit["changed"]
        stats["changed"] += changed
        before = r.get("before")
        if before is None:
            problems.append((i, "an accepted edit of a scalar whose span was not found"))
            return
        bs, be, bstyle = before
        if not (out[:bs] == text[:bs] and s == bs and out[e:] == text[be:]):                                                    # guarantee 1
            problems.append((i, "guarantee 1: the bytes outside the span changed"))
        if style != bstyle:                                                                                                       # guarantee 2: the style is kept
            problems.append((i, "guarantee 2: the style changed from %s to %s" % (bstyle, style)))
        token = out[s:e].decode("utf-8")
        if style_of(token) != style:
            problems.append((i, "guarantee 2: the span says %s, the token is %s" % (style, style_of(token))))
        if changed and token != canonical(case["value"], style):
            problems.append((i, "guarantee 2: the token %r is not the canonical spelling of %r in %s" % (token, case["value"], style)))
        if not changed and out != text:
            problems.append((i, "guarantee 6: nothing changed but the text is another"))
    if edit["ok"]:                                                                                                                # guarantees 3 and 4
        tb, ta = r.get("tree_before"), r.get("tree_after")
        expected = put(tb, re.findall(r'\.([A-Za-z_]+)|\[\{to="([^"]*)"\}\]', case["path"]), case["value"]) if tb is not None else None
        if tb is None or ta is None or expected is None:
            problems.append((i, "guarantee 3: the trees of an accepted edit are missing or the scalar is not in the tree"))
        elif ta != expected:
            problems.append((i, "guarantee 3/4: the result is not the old tree with that one scalar replaced (or a form or the order changed)"))
    noop = r.get("noop")
    if edit["ok"] and case.get("orig") is not None:
        stats["noop"] += 1
        if not noop or not noop["ok"]:
            problems.append((i, "guarantee 6: the position is editable but the edit to its own value was refused"))
        elif noop["text"].encode("utf-8") != text or noop["changed"]:
            problems.append((i, "guarantee 6: the edit to the value the scalar holds changed the text"))
        back = r.get("back")
        if case.get("back_path", case["path"]) is None:
            if back is not None:
                problems.append((i, "an edit back was made where none was asked for"))
        elif back is None or not back["ok"]:
            problems.append((i, "guarantee 6: an accepted edit cannot be edited back"))
        elif case["orig_canonical"] and edit["changed"]:
            stats["back_canonical"] += 1
            if back["text"].encode("utf-8") != text:
                problems.append((i, "guarantee 6: edit and back did not restore the bytes of a canonical original"))
    ab, ba = r.get("ab"), r.get("ba")
    if (ab is None) != (case.get("other") is None) or (ba is None) != (ab is None):
        problems.append((i, "the order dumps are missing"))
    elif ab is not None:
        if ab["ok"] != ba["ok"]:
            problems.append((i, "guarantee 6: independent edits: one order was refused and the other was not"))
        elif ab["ok"]:
            stats["commuted"] += 1
            if ab["text"] != ba["text"]:
                problems.append((i, "guarantee 6: independent edits do not commute"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=3)
    a = ap.parse_args()
    inputs, rust, go = load(a.inputs), load(a.rust), load(a.go)
    problems, stats, codes = [], {"accepted": 0, "changed": 0, "noop": 0, "back_canonical": 0, "commuted": 0}, {c: 0 for c in EDIT_CODES}
    if not (len(inputs) == len(rust) == len(go)):
        print("FAIL: %d inputs, %d Rust answers, %d Go answers" % (len(inputs), len(rust), len(go)))
        return 1
    different = 0
    for case, rr, gg in zip(inputs, rust, go):
        if rr.get("id") != case["id"] or gg.get("id") != case["id"]:
            problems.append((case["id"], "the answers are not in the order of the inputs"))
            continue
        if rr != gg:                                                                                        # guarantee 5
            different += 1
            if different <= a.show:
                print("DIFFERENT %d:\n  rust %s\n  go   %s" % (case["id"], json.dumps(rr)[:400], json.dumps(gg)[:400]))
        check_case(case, rr, problems, stats)
        e = rr["edit"]
        if not e["ok"] and "err" in e and e["err"][0][0] in codes:
            codes[e["err"][0][0]] += 1
    thin = []
    for c, n in codes.items():
        if n < MIN_PER_CODE:
            thin.append("%s appears %d times (at least %d)" % (c, n, MIN_PER_CODE))
    for key, need in (("accepted", MIN_ACCEPTED), ("changed", MIN_CHANGED), ("noop", MIN_NOOP), ("back_canonical", MIN_BACK_CANONICAL), ("commuted", MIN_COMMUTED)):
        if stats[key] < need:
            thin.append("%s: %d (at least %d)" % (key, stats[key], need))
    print("%d inputs; %d different between Rust and Go; accepted %d (changed %d), no-op checked %d, edited back to the canonical original %d, order pairs %d" %
          (len(inputs), different, stats["accepted"], stats["changed"], stats["noop"], stats["back_canonical"], stats["commuted"]))
    print("refusal codes: " + ", ".join("%s %d" % (c, n) for c, n in codes.items()))
    for i, why in problems[:a.show * 3]:
        print("PROBLEM %d: %s" % (i, why))
    for t in thin:
        print("TOO THIN: " + t)
    bad = different or problems or thin
    print("EDIT DIFFERENTIAL: " + ("FAIL (%d different, %d broken guarantees, %d thin)" % (different, len(problems), len(thin)) if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
