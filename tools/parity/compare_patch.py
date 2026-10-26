#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the Rust and Go answers of `patch` with each other and with a model, and check the promises of ADR 0013 on the dumps, on their own (P4 step P4-3).

    compare_patch.py INPUTS RUST_OUT GO_OUT [--show N]

The inputs are tools/parity/gen_patch.py's, the outputs are the `parity_patch_dump` tests'. Checked, per input:
  parity     every answer (the diff, the patch, the patch by the empty diff, the replay, the round trip, the tampered patch) is the same in the two languages;
  the model  the diff of A to B in the scope is the bytes tools/parity/diff_model.py gives for the meaning the generator wrote beside the text; from its events the
             outcome of the patch is predicted: an unsupported event anywhere is PATCH_UNSUPPORTED_EVENT at the first one (in the canonical order); otherwise a scalar the
             generator wrote as a block scalar, a multi-line scalar or inside a flow mapping is the edit refusal it earns, at the first such event; a clean value is
             accepted; a value a plain scalar cannot carry may be accepted or refused as EDIT_STYLE_CONFLICT or EDIT_VALUE_NOT_REPRESENTABLE;
  text       an accepted patch is A with each replaced scalar swapped for the canonical spelling of its new value in the style it had (ADR 0012), and nothing else: the
             bytes outside the spans are A's, the new span holds the token, `replaced` is the paths of the events in order;
  round trip the diff of the result to B, in the same scope, has no changes (S+ of ADR 0013: within the context and the scope; byte equality with B is not promised);
  replay     the same diff applied to the result is PATCH_CONFLICT at its first event; an empty diff applied to A is A byte for byte, whatever the context says;
  tamper     the same diff with the first `previous` altered is PATCH_CONFLICT at that event, nothing applied;
  invalid A  a document that does not parse or validate is refused by the earlier phase, by both the diff and the patch.
The run must be worth something: patches accepted with several events, every edit refusal a text can earn, unsupported events, replays, empty diffs, scopes, quoted
spans, tampered diffs. Exit status 1 on any difference, any broken promise, a missing or extra line, or a run that is too thin.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diff_model as dm  # noqa: E402

EMPTY = '{"changes":[],"representation":[],"version":1}'
EDIT_CODES = ["EDIT_BLOCK_SCALAR", "EDIT_MULTILINE_SCALAR", "EDIT_FLOW_CONTEXT", "EDIT_STYLE_CONFLICT", "EDIT_VALUE_NOT_REPRESENTABLE"]
TRICKY_CODES = {"EDIT_STYLE_CONFLICT", "EDIT_VALUE_NOT_REPRESENTABLE"}
MIN = {"kindswap": 800, "accepted": 600, "multi": 250, "unsupported": 80, "conflict_replay": 400, "empty_diff": 60, "scoped": 700, "quoted": 120, "tamper": 400, "invalid": 40}
MIN_CODE = 15


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def token(value, style):
    """The token an edit writes for the value in the style, or None when the style cannot carry it (ADR 0012)."""
    if style == "single_quoted":
        return "'" + value.replace("'", "''") + "'" if isinstance(value, str) else None
    if style == "double_quoted":
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"' if isinstance(value, str) else None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) else None


def segs_of(raw):
    return [("f", s[1]) if s[0] == "f" else ("i", tuple((k, v) for k, v in s[1])) for s in raw]


def check_case(case, rr, problems, stats, tree):
    i = case["id"]
    text = case["yaml"].encode("utf-8")
    if case["a"] is None:
        stats["invalid"] += 1
        d, p = rr["diff"], rr["patch"]
        strip = lambda x: dict(x, err=x["err"][:4]) if "err" in x else x          # the diff names the operand, the patch has none
        if "ok" in d or "ok" in p or strip(d) != strip(p):
            problems.append((i, "invalid A: the diff %s and the patch %s are not the same refusal" % (str(d)[:100], str(p)[:100])))
        return
    scope = None if case["scope_segs"] is None else [segs_of(s) for s in case["scope_segs"]]
    stats["scoped" if scope is not None else "unscoped"] += 1
    want = dm.diff(case["a"], case["b"], tree, scope)
    if rr["diff"].get("ok") != want:
        problems.append((i, "model: the diff differs\n   impl  %s\n   model %s" % (str(rr["diff"])[:300], want[:300])))
        return
    events = json.loads(want)["changes"]
    patch = rr["patch"]
    empty = rr.get("empty", {})
    if empty.get("ok") != case["yaml"]:
        problems.append((i, "promise: the empty diff does not give A back: %s" % str(empty)[:120]))
    if not events:
        stats["empty_diff"] += 1
        if patch.get("ok", {}).get("text") != case["yaml"] or patch["ok"]["replaced"] or patch["ok"]["spans"]:
            problems.append((i, "promise: a diff without events must give A byte for byte: %s" % str(patch)[:150]))
        if rr.get("replay", {}).get("ok") != case["yaml"]:
            problems.append((i, "promise: the replay of an empty diff is not A"))
        return
    swap = rr.get("kindswap")
    stats["kindswap"] += 1
    if swap is None or swap.get("err") != ["PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", events[0]["path"], ""]:
        problems.append((i, "unsupported: an event of another kind with both values must be refused at %s, got %s" % (events[0]["path"], str(swap)[:120])))
    unsupported = next((e for e in events if e["kind"] != "value_change" or e["path"] == "$.note"), None)
    if unsupported is not None:
        stats["unsupported"] += 1
        if patch.get("err") != ["PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", unsupported["path"], ""]:
            problems.append((i, "unsupported: got %s, expected PATCH_UNSUPPORTED_EVENT at %s" % (str(patch)[:150], unsupported["path"])))
        return
    special = next((e for e in events if e["path"] in case["special"]), None)
    if "err" in patch:
        code, phase, path = patch["err"][0], patch["err"][1], patch["err"][3]
        stats["codes"][code] = stats["codes"].get(code, 0) + 1
        if special is not None and not (case["tricky"] and code in TRICKY_CODES):
            if [code, phase, path] != [case["special"][special["path"]], "edit", special["path"]]:
                problems.append((i, "edit refusal: got %s, expected %s at %s" % (patch["err"], case["special"][special["path"]], special["path"])))
        elif special is not None:
            pass                                                   # a tricky value may be refused earlier than the special position
        elif not (case["tricky"] and code in TRICKY_CODES and phase == "edit"):
            problems.append((i, "refusal: a clean patch of a clean document was refused: %s" % patch["err"]))
        return
    if special is not None:
        problems.append((i, "promise: the scalar at %s cannot be replaced and the patch was accepted" % special["path"]))
        return
    if "ok" not in patch:
        problems.append((i, "the patch gave neither a result nor a refusal: %s" % str(patch)[:100]))
        return
    ok = patch["ok"]
    stats["accepted"] += 1
    stats["multi"] += len(events) > 1
    if ok["replaced"] != [e["path"] for e in events]:
        problems.append((i, "promise: replaced %s, the events are %s" % (ok["replaced"], [e["path"] for e in events])))
        return
    # the text: A with each replaced scalar swapped for the canonical token of its new value, nothing else
    spans = sorted(zip(ok["spans"], events), key=lambda p: p[0][1][0])
    out, at, expect = ok["text"].encode("utf-8"), 0, b""
    for (path, before, after), e in spans:
        if path != e["path"]:
            problems.append((i, "the spans are not in the order of the events"))
            return
        bs, be, bstyle = before
        tok = token(e["next"], bstyle)
        if tok is None:
            problems.append((i, "promise: the new value %r cannot be written in %s but was" % (e["next"], bstyle)))
            return
        stats["quoted"] += bstyle != "plain"
        expect += text[at:bs] + tok.encode("utf-8")
        at = be
        if after[2] != bstyle:
            problems.append((i, "promise: the style changed from %s to %s" % (bstyle, after[2])))
    expect += text[at:]
    if out != expect:
        problems.append((i, "promise: the text is not A with the canonical tokens swapped in\n   got  %r\n   want %r" % (out[:200], expect[:200])))
        return
    rt = rr.get("rt", {}).get("ok")
    if rt is None or json.loads(rt)["changes"] != []:                  # ADR 0013: the changes, not the writing (a position A wrote expanded stays expanded)
        problems.append((i, "round trip: the diff of the result to B has changes: %s" % str(rr.get("rt"))[:200]))
    replay = rr.get("replay", {})
    stats["conflict_replay"] += 1
    if replay.get("err", [None] * 5)[:2] != ["PATCH_CONFLICT", "patch"] or replay["err"][3] != events[0]["path"]:
        problems.append((i, "replay: the same diff on its result must be PATCH_CONFLICT at %s, got %s" % (events[0]["path"], str(replay)[:150])))
    tamper = rr.get("tamper", {})
    stats["tamper"] += 1
    if tamper.get("err", [None, None, None, None])[:2] != ["PATCH_CONFLICT", "patch"] or tamper["err"][3] != events[0]["path"]:
        problems.append((i, "tamper: a wrong previous must be PATCH_CONFLICT at %s, got %s" % (events[0]["path"], str(tamper)[:150])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=4)
    a = ap.parse_args()
    head, *cases = load(a.inputs)
    tree = head["tree"]
    rust, go = load(a.rust), load(a.go)
    if not (len(cases) == len(rust) == len(go)):
        print("FAIL: %d inputs, %d Rust answers, %d Go answers" % (len(cases), len(rust), len(go)))
        return 1
    problems, different = [], 0
    stats = {k: 0 for k in MIN}
    stats.update({"unscoped": 0, "codes": {}})
    for c, r, g in zip(cases, rust, go):
        if not (c["id"] == r["id"] == g["id"]):
            problems.append((c["id"], "the dumps are not in the order of the inputs"))
            continue
        if r != g:
            different += 1
            if different <= a.show:
                print("DIFFERENT %d:\n  rust %s\n  go   %s" % (c["id"], json.dumps(r)[:400], json.dumps(g)[:400]))
            continue
        check_case(c, r, problems, stats, tree)
    thin = ["%s: %d < %d" % (k, stats[k], n) for k, n in MIN.items() if stats[k] < n]
    thin += ["%s: %d < %d" % (code, stats["codes"].get(code, 0), MIN_CODE) for code in EDIT_CODES if stats["codes"].get(code, 0) < MIN_CODE]
    for i, msg in problems[:a.show * 2]:
        print("PROBLEM %s: %s" % (i, msg))
    print("%d inputs, %d different between Rust and Go, %d broken promises; accepted %d (several events %d), unsupported %d, empty diffs %d, replays %d, tampered %d, scoped %d, quoted spans %d, invalid A %d; refusals %s" % (
        len(cases), different, len(problems), stats["accepted"], stats["multi"], stats["unsupported"], stats["empty_diff"], stats["conflict_replay"], stats["tamper"], stats["scoped"], stats["quoted"], stats["invalid"], stats["codes"]))
    for t in thin:
        print("TOO THIN: " + t)
    bad = different or problems or thin
    print("PATCH DIFFERENTIAL: " + ("FAIL (%d different, %d broken, %d thin)" % (different, len(problems), len(thin)) if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
