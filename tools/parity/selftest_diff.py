#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A gate that cannot fail proves nothing: break the diff differential outputs in the ways that must turn compare_diff.py red (P4 step D3b).

    selftest_diff.py INPUTS RUST_OUT GO_OUT

Every break is made on a copy. A break of one side must be found by the comparison of the two languages; a break that keeps the two equal (both sides wrong in
the same way) must be found by the model or, with the model switched off (COMPARE_DIFF_NO_MODEL, a hook for this script only), by the property that is meant to
catch it: stability, identity, inversion, the canonical form, the injected faults; a run that tests nothing must be found by the thinness check.
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
    plain = lambda r: not cases[r["id"]]["inject"] and "ok" in r["ab"]
    with_events = lambda n: (lambda r: plain(r) and json.loads(r["ab"]["ok"])["changes"] and any(e["kind"] == n for e in json.loads(r["ab"]["ok"])["changes"]))
    two_events = first(rust, lambda r: plain(r) and len(json.loads(r["ab"]["ok"])["changes"]) >= 2 and json.loads(r["ab"]["ok"])["changes"][0]["path"] != json.loads(r["ab"]["ok"])["changes"][1]["path"])
    nonempty = first(rust, lambda r: plain(r) and json.loads(r["ab"]["ok"])["changes"])
    with_added = first(rust, with_events("added"))
    with_rep = first(rust, lambda r: plain(r) and json.loads(r["ab"]["ok"])["representation"])
    injected = first(rust, lambda r: cases[r["id"]]["inject"] and cases[r["id"]]["inject"]["kind"] in ("unknown", "dup", "float") and cases[r["id"]]["inject"]["side"] == "a")
    injected_b = first(rust, lambda r: cases[r["id"]]["inject"] and cases[r["id"]]["inject"]["kind"] in ("unknown", "dup", "float") and cases[r["id"]]["inject"]["side"] == "b")

    def clone(rows):
        return json.loads(json.dumps(rows))

    def both(fn):
        a, b = clone(rust), clone(go)
        fn(a)
        fn(b)
        return a, b

    breaks = []                                                   # (name, rust rows, go rows, model off?)
    g = clone(go); g[nonempty]["ab"]["ok"] = g[nonempty]["ab"]["ok"].replace('"path":"$', '"path":"#', 1)
    breaks.append(("go: one byte of an answer", rust, g, False))
    g = clone(go); g[nonempty]["ab"] = {"err": ["UNKNOWN_FIELD", "validate", "unavailable", "$.x", "a"]}
    breaks.append(("go: an answer becomes a refusal", rust, g, False))
    r_ = clone(rust); r_[injected]["ab"]["err"][4] = "b"
    breaks.append(("rust: the operand of a refusal", r_, go, False))
    r_ = clone(rust); r_[injected]["ab"]["err"][2] = "1:1:0"
    breaks.append(("rust: the location of a refusal", r_, go, False))
    breaks.append(("go: a line missing", rust, go[:-1], False))
    breaks.append(("go: the answers in another order", rust, go[1:] + go[:1], False))
    g = clone(go); g[0]["aa"] = g[nonempty]["ab"]
    breaks.append(("go: A against A answers like another pair", rust, g, False))
    # both sides wrong in the same way, found by the model
    def swap_events(rows):
        s = json.loads(rows[two_events]["ab"]["ok"])
        s["changes"][0], s["changes"][1] = s["changes"][1], s["changes"][0]
        rows[two_events]["ab"]["ok"] = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    a, b = both(swap_events)
    breaks.append(("both: two events in the wrong order", a, b, False))
    def other_value(rows):
        s = json.loads(rows[nonempty]["ab"]["ok"])
        s["changes"][0]["path"] = s["changes"][0]["path"] + "x"
        rows[nonempty]["ab"]["ok"] = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    a, b = both(other_value)
    breaks.append(("both: an event at another path", a, b, False))
    # both wrong in the same way, found by the property alone (the model is off)
    a, b = both(lambda rows: rows[nonempty]["abs"].update(ok=EMPTY))
    breaks.append(("both: the shuffled pair gives other bytes (stability)", a, b, True))
    a, b = both(lambda rows: rows[nonempty]["aa"].update(ok=rows[nonempty]["ab"]["ok"]))
    breaks.append(("both: A against A is not empty (identity)", a, b, True))
    a, b = both(lambda rows: rows[with_added]["ba"].update(ok=EMPTY))
    breaks.append(("both: B to A is empty though A to B is not (inversion)", a, b, True))
    a, b = both(lambda rows: rows[with_added]["ba"].update(ok=rows[with_added]["ab"]["ok"]))
    breaks.append(("both: B to A is A to B, not turned round (inversion)", a, b, True))
    def lose_rep(rows):
        s = json.loads(rows[with_rep]["ba"]["ok"])
        s["representation"] = [dict(x, previous=x["next"]) for x in s["representation"]]
        rows[with_rep]["ba"]["ok"] = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    a, b = both(lose_rep)
    breaks.append(("both: a representation entry of B to A is not turned round (inversion)", a, b, True))
    a, b = both(lambda rows: rows[nonempty]["ab"].update(ok=rows[nonempty]["ab"]["ok"].replace(",", ", ", 1)))
    breaks.append(("both: an answer with a space in it (canonical form)", a, b, True))
    a, b = both(lambda rows: rows[nonempty]["ab"].update(ok=rows[nonempty]["ab"]["ok"].replace('"version":1', '"version":1.0')))
    breaks.append(("both: a float in an answer (canonical form)", a, b, True))
    def wrong_code(rows):
        rows[injected]["ab"]["err"][0] = "UNKNOWN_FIELD" if rows[injected]["ab"]["err"][0] != "UNKNOWN_FIELD" else "PATH_KEY_TYPE"
    a, b = both(wrong_code)
    breaks.append(("both: an injected fault gives another code (faults)", a, b, True))
    def wrong_operand(rows):
        o = rows[injected_b]["ab"]["err"]
        o[4] = "a" if o[4] == "b" else "b"
    a, b = both(wrong_operand)
    breaks.append(("both: an injected fault names the other operand (faults)", a, b, True))
    a, b = both(lambda rows: rows[nonempty].update(ab={"err": ["UNKNOWN_FIELD", "validate", "unavailable", "$.x", "a"]}))
    breaks.append(("both: a valid pair is refused", a, b, True))
    # a run that is worth nothing
    def no_events(rows):
        for r in rows:
            if "ok" in r["ab"]:
                r["ab"]["ok"] = EMPTY
                r["ba"]["ok"] = EMPTY
                r["abs"]["ok"] = EMPTY
    a, b = both(no_events)
    breaks.append(("both: no event anywhere", a, b, True))
    def no_rep(rows):
        for r in rows:
            for n in ("ab", "ba", "abs"):
                if "ok" in r[n]:
                    s = json.loads(r[n]["ok"])
                    s["representation"] = []
                    r[n]["ok"] = json.dumps(s, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    a, b = both(no_rep)
    breaks.append(("both: no representation entry anywhere", a, b, True))

    with tempfile.TemporaryDirectory() as d:
        missed = 0
        base_env = {k: v for k, v in os.environ.items() if k != "COMPARE_DIFF_NO_MODEL"}
        for name, r, g, no_model in breaks:
            write(os.path.join(d, "i.jsonl"), [head] + cases)
            write(os.path.join(d, "r.jsonl"), r)
            write(os.path.join(d, "g.jsonl"), g)
            env = dict(base_env, **({"COMPARE_DIFF_NO_MODEL": "1"} if no_model else {}))
            res = subprocess.run([sys.executable, os.path.join(HERE, "compare_diff.py"), os.path.join(d, "i.jsonl"), os.path.join(d, "r.jsonl"), os.path.join(d, "g.jsonl")], capture_output=True, text=True, env=env)
            crashed = "Traceback" in res.stderr             # a crash exits 1 too: that is not the comparison finding the break
            print("%-6s %s%s" % ("CRASH" if crashed else "red" if res.returncode else "GREEN", name, "  [model off]" if no_model else ""))
            missed += res.returncode == 0 or crashed
        for label, env in (("with the model", base_env), ("without the model", dict(base_env, COMPARE_DIFF_NO_MODEL="1"))):
            rc = subprocess.run([sys.executable, os.path.join(HERE, "compare_diff.py"), inputs_p, rust_p, go_p], capture_output=True, env=env).returncode
            if rc:
                print("the unbroken outputs do not pass %s" % label)
                missed += 1
    print("selftest: %d breaks, %s" % (len(breaks), "every one turns the comparison red" if not missed else "%d NOT FOUND" % missed))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
