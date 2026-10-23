#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A gate that cannot fail proves nothing: break the edit differential outputs in the ways that must turn compare_edit.py red (P3b step E4).

    selftest_edit.py INPUTS RUST_OUT GO_OUT

Every break is made on a copy. A break that keeps the two languages equal (both sides wrong in the same way) must still be found by the guarantees that
compare_edit.py checks on its own; a break of one side must be found by the comparison; a run that tests nothing must be found by the thinness check.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def first(rows, pred):
    return next(i for i, r in enumerate(rows) if pred(r))


def main():
    inputs_p, rust_p, go_p = sys.argv[1:4]
    inputs, rust, go = load(inputs_p), load(rust_p), load(go_p)
    case = {c["id"]: c for c in inputs}
    ok_changed = lambda r: r["edit"]["ok"] and r["edit"]["changed"] and r["edit"]["span"][2] == "plain"
    canon_back = lambda r: r["edit"]["ok"] and r["edit"]["changed"] and case[r["id"]]["orig_canonical"] and r["back"] and r["back"]["ok"]
    commuted = lambda r: r["ab"] and r["ba"] and r["ab"]["ok"] and r["ba"]["ok"]
    quoted_ok = lambda r: r["edit"]["ok"] and r["edit"]["changed"] and r["edit"]["span"][2] != "plain"
    i_changed, i_back, i_comm, i_quoted = (first(rust, ok_changed), first(rust, canon_back), first(rust, commuted), first(rust, quoted_ok))
    i_noop = first(rust, lambda r: r["noop"] and r["noop"]["ok"])
    code_of = lambda r: r["edit"]["err"][0][0] if not r["edit"]["ok"] and "err" in r["edit"] else None

    def mut_text(rows, i, key, fn):
        rows = json.loads(json.dumps(rows))
        rows[i][key]["text"] = fn(rows[i][key]["text"])
        return rows

    breaks = []                                                                       # (name, rust rows, go rows)
    # one side differs: guarantee 5
    breaks.append(("go: one byte of an edited text", rust, mut_text(go, i_changed, "edit", lambda t: t[:-1] + ("x" if t[-1] != "x" else "y"))))
    g = json.loads(json.dumps(go)); g[i_changed]["edit"]["span"][1] += 1
    breaks.append(("go: the end of a span", rust, g))
    g = json.loads(json.dumps(go)); g[i_changed]["edit"] = {"ok": False, "err": [["EDIT_STYLE_CONFLICT", "edit", "1:1:0", "$.name"]]}
    breaks.append(("go: an accepted edit becomes a refusal", rust, g))
    g = json.loads(json.dumps(go)); g[i_changed]["edit"]["changed"] = False
    breaks.append(("go: changed becomes false", rust, g))
    breaks.append(("go: a line missing", rust, go[:-1]))
    breaks.append(("go: the answers in another order", rust, go[1:] + go[:1]))
    r_ = json.loads(json.dumps(rust)); j = first(rust, lambda r: code_of(r) == "EDIT_TYPE_MISMATCH"); r_[j]["edit"]["err"][0][2] = "9:9:9"
    breaks.append(("rust: the position of a refusal", r_, go))
    # both sides wrong in the same way: the guarantees on their own
    def both(fn):
        a, b = json.loads(json.dumps(rust)), json.loads(json.dumps(go))
        fn(a)
        fn(b)
        return a, b
    a, b = both(lambda rows: rows[i_changed]["edit"].update(text="X" + rows[i_changed]["edit"]["text"][1:]) if rows[i_changed]["edit"]["span"][0] > 0 else None)
    breaks.append(("both: a byte before the span changed (guarantee 1)", a, b))
    a, b = both(lambda rows: rows[i_changed]["edit"].update(text=rows[i_changed]["edit"]["text"] + "tail"))
    breaks.append(("both: bytes after the span added (guarantee 1)", a, b))
    a, b = both(lambda rows: rows[i_quoted]["edit"]["span"].__setitem__(2, "plain"))
    breaks.append(("both: the style of the span is not the style of the token (guarantee 2)", a, b))
    def respell(rows):
        e = rows[i_changed]["edit"]
        s, en = e["span"][0], e["span"][1]
        raw = e["text"].encode("utf-8")
        e["text"] = (raw[:s] + b"'" + raw[s:en] + b"'" + raw[en:]).decode("utf-8")
        e["span"][1] = en + 2
        e["span"][2] = "single_quoted"
    a, b = both(respell)
    breaks.append(("both: a plain value written quoted (guarantee 2: not the canonical spelling)", a, b))
    a, b = both(lambda rows: rows[i_noop]["noop"].update(text=rows[i_noop]["noop"]["text"] + " ", changed=False))
    breaks.append(("both: the edit to the same value changes the text (guarantee 6)", a, b))
    a, b = both(lambda rows: rows[i_noop]["noop"].update(ok=False, err=[["EDIT_STYLE_CONFLICT", "edit", "1:1:0", "$.x"]]))
    breaks.append(("both: the edit to the same value is refused (guarantee 6)", a, b))
    a, b = both(lambda rows: rows[i_back]["back"].update(text=rows[i_back]["back"]["text"] + "\n#"))
    breaks.append(("both: edit and back does not restore a canonical original (guarantee 6)", a, b))
    a, b = both(lambda rows: rows[i_comm]["ba"].update(text=rows[i_comm]["ba"]["text"] + "\n#"))
    breaks.append(("both: two independent edits do not commute (guarantee 6)", a, b))
    a, b = both(lambda rows: rows[i_comm]["ba"].update(ok=False, err=[["EDIT_STYLE_CONFLICT", "edit", "1:1:0", "$.x"]]))
    breaks.append(("both: one order of independent edits is refused (guarantee 6)", a, b))
    # guarantees 3 and 4: the trees of the result (both sides wrong in the same way)
    i_tree = first(rust, lambda r: r["edit"]["ok"] and r["tree_after"] and len(r["tree_after"].get("map", [])) >= 3 and r["edit"]["changed"])
    changed_at = lambda r: [j for j, (x, y) in enumerate(zip(r["tree_before"]["map"], r["tree_after"]["map"])) if x != y]
    def other_scalar(rows):
        r = rows[i_tree]
        j = next(j for j in range(len(r["tree_after"]["map"])) if j not in changed_at(r) and "integer" in r["tree_after"]["map"][j][1])
        r["tree_after"]["map"][j][1] = {"integer": 99999}
    a, b = both(other_scalar)
    breaks.append(("both: another scalar of the result changed (guarantee 3)", a, b))
    def reorder(rows):
        rows[i_tree]["tree_after"]["map"].reverse()
    a, b = both(reorder)
    breaks.append(("both: the entries of the result are in another order (guarantee 4)", a, b))
    def form(rows):
        m = rows[i_tree]["tree_after"]["map"]
        j = next(j for j in range(len(m)) if j not in changed_at(rows[i_tree]))
        m[j][1] = {"string": "short now"}
    a, b = both(form)
    breaks.append(("both: a position changed its form (guarantee 4)", a, b))
    a, b = both(lambda rows: rows[i_tree].update(tree_after=None))
    breaks.append(("both: the tree of the result is missing", a, b))
    # a run that is worth nothing
    def only_refusals(rows):
        for r in rows:
            r["edit"] = {"ok": False, "err": [["EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$.x"]]}
    a, b = both(only_refusals)
    breaks.append(("both: nothing is accepted", a, b))
    def lose_code(rows):
        for r in rows:
            if code_of(r) == "EDIT_INVALID_RESULT":
                r["edit"]["err"][0][0] = "EDIT_TYPE_MISMATCH"
    a, b = both(lose_code)
    breaks.append(("both: a refusal code never appears (EDIT_INVALID_RESULT)", a, b))

    with tempfile.TemporaryDirectory() as d:
        missed = 0
        for name, r, g in breaks:
            write(os.path.join(d, "r.jsonl"), r)
            write(os.path.join(d, "g.jsonl"), g)
            rc = subprocess.run([sys.executable, os.path.join(HERE, "compare_edit.py"), inputs_p, os.path.join(d, "r.jsonl"), os.path.join(d, "g.jsonl")], capture_output=True).returncode
            print("%-6s %s" % ("red" if rc else "GREEN", name))
            missed += rc == 0
        # and the unbroken outputs must pass
        rc = subprocess.run([sys.executable, os.path.join(HERE, "compare_edit.py"), inputs_p, rust_p, go_p], capture_output=True).returncode
        if rc:
            print("the unbroken outputs do not pass")
            missed += 1
    print("selftest: %d breaks, %s" % (len(breaks), "every one turns the comparison red" if not missed else "%d NOT FOUND" % missed))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
