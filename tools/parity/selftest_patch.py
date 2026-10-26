#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A gate that cannot fail proves nothing: break the patch differential outputs in the ways that must turn compare_patch.py red (P4 step P4-3).

    selftest_patch.py INPUTS RUST_OUT GO_OUT

Every break is made on a copy. A break of one side must be found by the comparison of the two languages; a break that keeps the two equal (both sides wrong in the
same way) must be found by the model or by the promise of ADR 0013 that it violates; a run that tests nothing must be found by the thinness check. A crash of the
comparator is not a finding: it counts as a miss.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EMPTY = '{"changes":[],"representation":[],"version":1}'


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")


def first(rows, pred):
    return next(i for i, r in enumerate(rows) if pred(r))


def main():
    inputs_p, rust_p, go_p = sys.argv[1:4]
    head, *cases = load(inputs_p)
    rust, go = load(rust_p), load(go_p)
    ok_patch = lambda r: "ok" in r["patch"] and r["patch"]["ok"]["replaced"]
    multi = first(rust, lambda r: ok_patch(r) and len(r["patch"]["ok"]["replaced"]) >= 2)
    one = first(rust, ok_patch)
    quoted = first(rust, lambda r: ok_patch(r) and any(s[1][2] != "plain" for s in r["patch"]["ok"]["spans"]))
    unsupported = first(rust, lambda r: r["patch"].get("err", [""])[0] == "PATCH_UNSUPPORTED_EVENT")
    special = first(rust, lambda r: r["patch"].get("err", [""])[0] in ("EDIT_BLOCK_SCALAR", "EDIT_MULTILINE_SCALAR", "EDIT_FLOW_CONTEXT") and not cases[r["id"]]["tricky"])
    clean = first(rust, lambda r: ok_patch(r) and not cases[r["id"]]["tricky"])
    empty_diff = first(rust, lambda r: "ok" in r["patch"] and not r["patch"]["ok"]["replaced"] and cases[r["id"]]["a"] is not None)
    invalid = first(rust, lambda r: cases[r["id"]]["a"] is None)

    def clone(rows):
        return json.loads(json.dumps(rows))

    def both(fn):
        a, b = clone(rust), clone(go)
        fn(a)
        fn(b)
        return a, b

    breaks = []                                                     # (name, rust rows, go rows)
    g = clone(go); t = g[one]["patch"]["ok"]["text"]; g[one]["patch"]["ok"]["text"] = t[:-1] + ("x" if t[-1] != "x" else "y")
    breaks.append(("go: one byte of a patched text", rust, g))
    g = clone(go); g[one]["patch"] = {"err": ["PATCH_CONFLICT", "patch", "1:1:0", "$.name", ""]}
    breaks.append(("go: an accepted patch becomes a refusal", rust, g))
    g = clone(go); g[one]["patch"]["ok"]["spans"][0][2][1] += 1
    breaks.append(("go: the end of a span after", rust, g))
    g = clone(go); g[one]["tamper"] = {"ok": "x"}
    breaks.append(("go: a tampered diff is accepted", rust, g))
    g = clone(go); g[one]["replay"] = {"ok": g[one]["replay"].get("ok", "")}
    breaks.append(("go: a replay is accepted", rust, g))
    r_ = clone(rust); r_[unsupported]["patch"]["err"][3] = "$.zzz"
    breaks.append(("rust: the path of an unsupported event", r_, go))
    breaks.append(("go: a line missing", rust, go[:-1]))
    breaks.append(("go: the answers in another order", rust, go[1:] + go[:1]))
    # both sides wrong in the same way
    def text_byte(rows):
        t = rows[one]["patch"]["ok"]["text"]
        rows[one]["patch"]["ok"]["text"] = "X" + t[1:]
    a, b = both(text_byte)
    breaks.append(("both: a byte outside the replaced scalars changed (minimality)", a, b))
    def other_token(rows):
        t = rows[quoted]["patch"]["ok"]["text"]
        rows[quoted]["patch"]["ok"]["text"] = t.replace("'", '"', 1) if "'" in t else t.replace('"', "'", 1)
    a, b = both(other_token)
    breaks.append(("both: a quoted scalar written in the other quote (style kept)", a, b))
    a, b = both(lambda rows: rows[multi]["patch"]["ok"].update(replaced=rows[multi]["patch"]["ok"]["replaced"][::-1]))
    breaks.append(("both: replaced is not in the order of the diff", a, b))
    def rt_changes(rows):
        rows[one]["rt"] = {"ok": '{"changes":[{"kind":"value_change","next":1,"path":"$.count","previous":2}],"representation":[],"version":1}'}
    a, b = both(rt_changes)
    breaks.append(("both: the round trip leaves a change (S+)", a, b))
    a, b = both(lambda rows: rows[one].update(replay={"ok": rows[one]["patch"]["ok"]["text"]}))
    breaks.append(("both: a replay is not a conflict", a, b))
    a, b = both(lambda rows: rows[one].update(tamper={"ok": rows[one]["patch"]["ok"]["text"]}))
    breaks.append(("both: a wrong previous is not a conflict", a, b))
    a, b = both(lambda rows: rows[one].update(empty={"ok": rows[one]["patch"]["ok"]["text"]}))
    breaks.append(("both: the empty diff changes the text", a, b))
    a, b = both(lambda rows: rows[empty_diff]["patch"]["ok"].update(text=rows[empty_diff]["patch"]["ok"]["text"] + " "))
    breaks.append(("both: a diff without events changes the text", a, b))
    def swap_code(rows):
        rows[unsupported]["patch"]["err"][0] = "EDIT_STYLE_CONFLICT"
        rows[unsupported]["patch"]["err"][1] = "edit"
    a, b = both(swap_code)
    breaks.append(("both: an unsupported event is reported as an edit refusal", a, b))
    a, b = both(lambda rows: rows[special].update(patch={"ok": {"text": cases[special]["yaml"], "replaced": [], "spans": []}}))
    breaks.append(("both: a scalar that cannot be replaced is accepted", a, b))
    a, b = both(lambda rows: rows[one].update(kindswap={"ok": ""}))
    breaks.append(("both: an event of another kind that carries both values is applied", a, b))
    a, b = both(lambda rows: rows[clean].update(patch={"err": ["EDIT_STYLE_CONFLICT", "edit", "1:1:0", "$.name", ""]}))
    breaks.append(("both: a clean patch of a clean document is refused", a, b))
    a, b = both(lambda rows: rows[invalid].update(patch={"ok": {"text": cases[invalid]["yaml"], "replaced": [], "spans": []}}))
    breaks.append(("both: an invalid document is patched", a, b))
    def span_style(rows):
        rows[quoted]["patch"]["ok"]["spans"][0][2][2] = "plain" if rows[quoted]["patch"]["ok"]["spans"][0][2][2] != "plain" else "single_quoted"
    a, b = both(span_style)
    breaks.append(("both: the style of the new span is not the style it had", a, b))
    def no_unsupported(rows):
        for r in rows:
            if r["patch"].get("err", [""])[0] == "PATCH_UNSUPPORTED_EVENT":
                r["patch"] = {"err": ["EDIT_STYLE_CONFLICT", "edit", "1:1:0", "$.name", ""]}
    a, b = both(no_unsupported)
    breaks.append(("both: nothing is ever unsupported", a, b))
    def no_accepted(rows):
        for r in rows:
            if ok_patch(r):
                r["patch"] = {"err": ["EDIT_STYLE_CONFLICT", "edit", "1:1:0", "$.name", ""]}
    a, b = both(no_accepted)
    breaks.append(("both: nothing is accepted", a, b))

    with tempfile.TemporaryDirectory() as d:
        missed = 0
        for name, r, g in breaks:
            write(os.path.join(d, "i.jsonl"), [head] + cases)
            write(os.path.join(d, "r.jsonl"), r)
            write(os.path.join(d, "g.jsonl"), g)
            res = subprocess.run([sys.executable, os.path.join(HERE, "compare_patch.py"), os.path.join(d, "i.jsonl"), os.path.join(d, "r.jsonl"), os.path.join(d, "g.jsonl")], capture_output=True, text=True)
            crashed = "Traceback" in res.stderr
            print("%-6s %s" % ("CRASH" if crashed else "red" if res.returncode else "GREEN", name))
            missed += res.returncode == 0 or crashed
        rc = subprocess.run([sys.executable, os.path.join(HERE, "compare_patch.py"), inputs_p, rust_p, go_p], capture_output=True).returncode
        if rc:
            print("the unbroken outputs do not pass")
            missed += 1
    print("selftest: %d breaks, %s" % (len(breaks), "every one turns the comparison red" if not missed else "%d NOT FOUND" % missed))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
