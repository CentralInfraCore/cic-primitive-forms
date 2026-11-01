"""Implicit calls the Rust graph may not follow: one crate in two states, one test per mechanism.

    python3 lab/measure/repro_rust_dispatch.py SCIPDEPS_BINARY TOOLCHAIN --expect vulnerable|fixed|report [--env NAME=VALUE ...] [--record OUT.json]

lab/testdata/rust-dispatch/{a,b}: in b the impl method behind each implicit call changes (Display through to_string, a trait object and an
`impl Display` argument; `?` through From; `for` through Iterator::next; `+`, `==`, `[]` through Add, PartialEq, Index; a method found through
Deref; Drop; sort through Ord). Every test passes on a and fails on b. A case is RED when the key of its test is the SAME on both (the
older rule would reuse a pass that is wrong now), GREEN when it moves.

--expect report prints the state and exits 0; --expect vulnerable requires the cases listed as RED in KNOWN_RED to be RED and the others
GREEN (the state before a fix); --expect fixed requires every case GREEN.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402
import rust_graph as rg  # noqa: E402

TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata", "rust-dispatch")
TESTS = {
    "t_to_string": "Display::fmt through to_string()", "t_dyn_display": "Display::fmt through Box<dyn Display>",
    "t_impl_arg": "Display::fmt through an `impl Display` argument", "t_question_from": "From::from through `?`",
    "t_for_next": "Iterator::next through `for`", "t_add": "Add::add through `+`", "t_eq": "PartialEq::eq through `==`",
    "t_index": "Index::index through `[]`", "t_deref": "Deref::deref through a method call", "t_drop": "Drop::drop at the end of a scope",
    "t_sort_ord": "Ord::cmp through sort()",
}
KNOWN_RED = {"t_drop"}  # the state before the fix; see lab/testdata/rust-dispatch/README.md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scipdeps")
    ap.add_argument("toolchain")
    ap.add_argument("--expect", choices=["vulnerable", "fixed", "report"], required=True)
    ap.add_argument("--env", action="append", default=[], help="NAME=VALUE for the extractor (SCIPDEPS_PLANTED_NO_IMPL=1)")
    ap.add_argument("--record")
    a = ap.parse_args()
    env = dict(e.split("=", 1) for e in a.env)
    ga, gb = (rg.graph(os.path.join(TD, v), a.scipdeps, a.toolchain, env) for v in "ab")
    ka, kb = merkle.keys(ga)[0], merkle.keys(gb)[0]
    pa, pb = rg.passes(os.path.join(TD, "a"), a.toolchain), rg.passes(os.path.join(TD, "b"), a.toolchain)
    nb = {n["id"]: n for n in gb["nodes"]}
    out, bad = {}, []
    for t, what in TESTS.items():
        tid = "tests/dispatch::%s()." % t
        state = "GREEN" if ka.get(tid) and kb.get(tid) and ka[tid] != kb[tid] else "RED"
        out[t] = {"mechanism": what, "state": state, "constructs": nb.get(tid, {}).get("constructs")}
        want = "GREEN" if a.expect == "fixed" else ("RED" if (a.expect == "vulnerable" and t in KNOWN_RED) else "GREEN")
        flag = "" if a.expect == "report" or state == want else "   <-- not as expected (%s)" % want
        print("%-5s %-16s %s%s" % (state, t, what, flag))
        if flag:
            bad.append(t)
    print("the tests pass on a: %s, fail on b: %s" % (pa, not pb))
    if not (pa and not pb):
        sys.exit("the fixture is wrong")
    if a.record:
        json.dump({"extractor_env": env, "cases": out}, open(a.record, "w"), indent=1, sort_keys=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
