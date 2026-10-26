#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the diff differential (P4 step D3b: ADR 0008, Rust against Go against the model of diff_model.py).

    gen_diff.py OUT_JSONL [--count N] [--seed S]

Deterministic: the same seed gives the same file. The first line is `{"tree": ...}`, the synthetic schema in the tree notation of the corpus (the Rust and Go
dumps read it, so there is one definition); each other line is
    {"id", "a", "b", "a2", "b2", "scope": [text]|null, "scope_segs": [[seg]]|null, "ctx_b": 0|1, "inject": null|{"side","kind","code","phase","loc","path"}}
`a`/`b` are the two documents as plain JSON trees (mapping, list, scalar; `{"__u": "1.5"}` is an unsupported number, which the dumps turn into one), `a2`/`b2` the
same two documents with the order of every list and set changed, `scope` the observed paths (texts, for the implementations) and `scope_segs` the same paths as
segments (for the model).

The documents are drawn over a tree that has every shape ADR 0008 distinguishes: scalars of three types, an opaque value, a composite with a set, sets of integers
and booleans, a list by a composite string key, a list by an integer key, an item_choice list. Positions are written short or expanded, expanded ones with
non-default declaration fields (a list, the empty list, `inherit: 0`, `default_injection: 0`, other conformance values). B is A changed by a few edits (a value, a
field appearing or disappearing, a set, a list element added, removed, renamed or changed, a declaration, the writing) or an independent draw. The scope is drawn
from the paths of both documents, with nested and repeated entries and a few that neither document has. One case in ten carries one injected fault (an unknown
field, a duplicate identity, an unsupported number, a context mismatch) whose answer the generator states itself.
"""
import argparse
import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_model as dm  # noqa: E402  (only path_text and the segment helpers: nothing of the diff itself)

TREE = {
    "mtu": {"scalar": "integer"},
    "name": {"scalar": "string"},
    "on": {"scalar": "boolean"},
    "note": "opaque",
    "ip": {"composite": {"gw": {"scalar": "string"}, "addrs": {"set": "string"}}},
    "ids": {"set": "integer"},
    "flags": {"set": "boolean"},
    "nics": {"list": {"key": ["name", "zone"], "fields": {"name": {"scalar": "string"}, "zone": {"scalar": "string"}, "subnet": {"scalar": "string"}}}},
    "ports": {"list": {"key": ["port"], "fields": {"port": {"scalar": "integer"}, "label": {"scalar": "string"}}}},
    "vlans": {"list": {"choice": [
        {"name": "referenced", "key": ["ref"], "fields": {"ref": {"scalar": "string"}}},
        {"name": "inline", "key": ["vlan_id"], "fields": {"vlan_id": {"scalar": "integer"}, "mode": {"scalar": "string"}}}]}},
}
FIELDS = dm.parse_tree(TREE)

WORDS = ["a", "b", "c", "x1", "é", 'q"q', "b\\s", "host-1", "w3", "tűrő", "c\x01\x7f\t\n", "\u2028 /"]
KEYS = ["n1", "n2", "n3", "é", 'k"q', "k\\b", "z"]
INTS = [0, 1, -1, 2, 1500, 65535, 9223372036854775807, -9223372036854775808]
PORTS = [1, 2, 3, 9, 10, -3]
REFS = ["r1", "r2", "x"]
ACCESS = ["inherit", ["OU=ops"], ["OU=a", "OU=b"], []]
INHERIT = [True, False, 0, 1]
DEFAULT_INJECTION = [None, 0, "d"]
CONFORMANCE = ["implemented", "not_implemented", "deprecated"]
OPAQUE = [None, 0, False, True, "s", "", [1, 2], [2, 1], {"x": 1, "y": 2}, {"y": 2, "x": 1}, {"x": {"y": [1]}}, [], {}, -5, "0"]


def P(value, expanded=False, decl=None):
    return {"v": value, "x": expanded, "d": decl or {}}


def draw_decl(r):
    d = {}
    for k, choices in (("access", ACCESS), ("modify", ACCESS), ("inherit", INHERIT), ("default_injection", DEFAULT_INJECTION), ("conformance", CONFORMANCE)):
        if r.random() < 0.3:
            d[k] = r.choice(choices)
    return d


def pos(r, value):
    if r.random() < 0.2:
        return P(value, True, draw_decl(r) if r.random() < 0.5 else {})
    return P(value)


def scalar_value(r, kind):
    return {"string": lambda: r.choice(WORDS), "integer": lambda: r.choice(INTS), "boolean": lambda: r.choice([True, False])}[kind]()


def gen_value(r, spec):
    kind, body = spec
    if kind == "scalar":
        return scalar_value(r, body)
    if kind == "opaque":
        return copy.deepcopy(r.choice(OPAQUE))
    if kind == "set":
        pool = {"string": WORDS, "integer": [0, 1, 2, 3, -4, 10, 9], "boolean": [True, False]}[body]
        return r.sample(pool, r.randint(0, min(len(pool), 4)))
    if kind == "comp":
        return gen_record(r, body, 0.5)
    return gen_list(r, body)


def gen_record(r, fields, p):
    return {n: pos(r, gen_value(r, s)) for n, s in fields.items() if r.random() < p}


def key_value(r, name, case):
    return {"name": r.choice(KEYS), "zone": r.choice(["z", "y"]), "port": r.choice(PORTS), "ref": r.choice(REFS), "vlan_id": r.choice([7, 8, 100])}[name]


def gen_element(r, cases):
    ci = r.randrange(len(cases))
    _, key, fields = cases[ci]
    rec = {}
    for n, s in fields.items():
        if n in key:
            rec[n] = pos(r, key_value(r, n, ci))
        elif r.random() < 0.6:
            rec[n] = pos(r, gen_value(r, s))
    return rec


def ident(rec, cases):
    for _, key, fields in cases:
        if all(k in fields for k in rec) and all(k in rec for k in key):
            return tuple((k, rec[k]["v"]) for k in key)
    raise AssertionError(rec)


def gen_list(r, body):
    cases = body[1]
    out, seen = [], set()
    for _ in range(r.randint(0, 4)):
        rec = gen_element(r, cases)
        i = json.dumps(ident(rec, cases))
        if i not in seen:
            seen.add(i)
            out.append(rec)
    return out


def gen_doc(r):
    return {n: pos(r, gen_value(r, s)) for n, s in FIELDS.items() if r.random() < 0.55}


# --- the edits that make B out of A ---------------------------------------------------------------------------------------------------------

def edit(r, doc):
    doc = copy.deepcopy(doc)
    for _ in range(r.randint(1, 4)):
        name = r.choice(sorted(FIELDS))
        spec = FIELDS[name]
        roll = r.random()
        if name not in doc:
            doc[name] = pos(r, gen_value(r, spec))
        elif roll < 0.12:
            del doc[name]
        elif roll < 0.3:
            doc[name]["x"] = not doc[name]["x"]
        elif roll < 0.45:
            doc[name]["x"] = True
            doc[name]["d"] = draw_decl(r)
        else:
            edit_value(r, doc[name], spec)
    return doc


def edit_value(r, p, spec):
    kind, body = spec
    if kind in ("scalar", "opaque"):
        p["v"] = gen_value(r, spec)
    elif kind == "set":
        pool = {"string": WORDS, "integer": [0, 1, 2, 3, -4, 10, 9], "boolean": [True, False]}[body]
        s = list(p["v"])
        if s and r.random() < 0.5:
            s.remove(r.choice(s))
        else:
            s += [x for x in [r.choice(pool)] if x not in s]
        p["v"] = s
    elif kind == "comp":
        for _ in range(r.randint(1, 2)):
            n = r.choice(sorted(body))
            roll = r.random()
            if n not in p["v"]:
                p["v"][n] = pos(r, gen_value(r, body[n]))
            elif roll < 0.2:
                del p["v"][n]
            elif roll < 0.4:
                p["v"][n]["x"] = not p["v"][n]["x"]
            else:
                edit_value(r, p["v"][n], body[n])
    else:
        cases = body[1]
        els = p["v"]
        roll = r.random()
        if els and roll < 0.25:
            els.remove(r.choice(els))
        elif roll < 0.5:
            new = gen_element(r, cases)
            if json.dumps(ident(new, cases)) not in {json.dumps(ident(e, cases)) for e in els}:
                els.append(new)
        elif els and roll < 0.7:
            rec = r.choice(els)
            ci = next(i for i, (_, key, _f) in enumerate(cases) if all(k in rec for k in key) and all(k in cases[i][2] for k in rec))
            n = r.choice(cases[ci][1])                   # a key component changes: one identity goes, another comes
            old = rec[n]["v"]
            rec[n]["v"] = key_value(r, n, ci)
            if json.dumps(ident(rec, cases)) in {json.dumps(ident(e, cases)) for e in els if e is not rec}:
                rec[n]["v"] = old
        elif els:
            rec = r.choice(els)
            ci = next(i for i, (_, key, _f) in enumerate(cases) if all(k in rec for k in key) and all(k in cases[i][2] for k in rec))
            free = [n for n in cases[ci][2] if n not in cases[ci][1]]
            if free:
                n = r.choice(free)
                if n in rec and r.random() < 0.3:
                    del rec[n]
                elif n in rec:
                    edit_value(r, rec[n], cases[ci][2][n])
                else:
                    rec[n] = pos(r, gen_value(r, cases[ci][2][n]))
            else:
                els.reverse()


# --- rendering ----------------------------------------------------------------------------------------------------------------------------------

def render_value(v, spec, shuffle):
    kind, body = spec
    if v is None:
        return None
    if kind in ("scalar", "opaque"):
        return copy.deepcopy(v)
    if kind == "set":
        out = list(v)
        return out[::-1] if shuffle else out
    if kind == "comp":
        return render_record(v, body, shuffle)
    els = [render_record(e, next(c[2] for c in body[1] if all(k in e for k in c[1]) and all(k in c[2] for k in e)), shuffle) for e in v]
    return els[::-1] if shuffle else els


def render_position(p, spec, shuffle):
    value = render_value(p["v"], spec, shuffle)
    if not p["x"]:
        return value
    w = {"value": value, "access": "inherit", "modify": "inherit", "inherit": True, "default_injection": None, "conformance": "implemented"}
    w.update(copy.deepcopy(p["d"]))
    return w


def render_record(rec, fields, shuffle):
    items = list(rec.items())
    if shuffle:
        items = items[::-1]
    return {n: render_position(p, fields[n], shuffle) for n, p in items}


def render(doc, shuffle=False):
    return render_record(doc, FIELDS, shuffle)


# --- scope ---------------------------------------------------------------------------------------------------------------------------------------

def place_paths(doc):
    """Every path that is a place of the document: positions, inside composites, list elements and their fields. [(segs)]"""
    out = []

    def rec_paths(rec, fields, base):
        for n, p in rec.items():
            here = base + [("f", n)]
            out.append(here)
            spec = fields[n]
            if spec[0] == "comp":
                rec_paths(p["v"], spec[1], here)
            elif spec[0] == "list":
                for e in p["v"]:
                    c = next(c for c in spec[1][1] if all(k in e for k in c[1]) and all(k in c[2] for k in e))
                    el = here + [("i", tuple((k, e[k]["v"]) for k in c[1]))]
                    out.append(el)
                    rec_paths(e, c[2], el)

    rec_paths(doc, FIELDS, [])
    return out


def draw_scope(r, a, b):
    if r.random() < 0.45:
        return None, None
    pool = place_paths(a) + place_paths(b)
    segs = [r.choice(pool) for _ in range(r.randint(1, 3))] if pool else []
    if r.random() < 0.15:
        segs.append([("f", "nics"), ("i", (("name", "nope"), ("zone", "z")))])           # an element neither document has
    if r.random() < 0.1:
        segs.append([])                                                                    # the root
    if r.random() < 0.2 and segs:
        segs.append(list(segs[0]))                                                         # a repeated entry
    if r.random() < 0.2 and segs and len(segs[0]) > 1:
        segs.append(list(segs[0][:1]))                                                     # an entry that contains another
    r.shuffle(segs)
    return [dm.path_text(s) for s in segs], [[list(x) if x[0] == "f" else ["i", [list(c) for c in x[1]]] for x in s] for s in segs]


# --- injected faults ------------------------------------------------------------------------------------------------------------------------------

def inject(r, a_docs, b_docs):
    """One fault, put into every rendering of the chosen side (the plain and the shuffled one), and what the answer must be."""
    side = r.choice(["a", "b"])
    docs = a_docs if side == "a" else b_docs
    kind = r.choice(["unknown", "dup", "float", "bigint", "ctx"])
    if kind == "unknown":
        v = r.choice([1, "x"])
        for d in docs:
            d["bogus"] = v
        return {"side": side, "kind": kind, "code": "UNKNOWN_FIELD", "phase": "validate", "loc": "unavailable", "path": "$.bogus"}, 0
    if kind == "dup":
        for d, rev in zip(docs, (False, True)):
            els = [{"name": "d", "zone": "d"}, {"zone": "d", "name": "d", "subnet": "s"}]
            d["nics"] = els[::-1] if rev else els
        return {"side": side, "kind": kind, "code": "PATH_DUPLICATE_IDENTITY", "phase": "path_of", "loc": "unavailable", "path": '$.nics[{name="d",zone="d"}]'}, 0
    if kind in ("float", "bigint"):
        field = r.choice(["note", "mtu"])
        for d in docs:
            d[field] = {"__u": "1.5" if kind == "float" else "9223372036854775808"}
        return {"side": side, "kind": kind, "code": "DIFF_UNSUPPORTED_VALUE", "phase": "diff", "loc": "not_applicable", "path": "$." + field}, 0
    return {"side": "", "kind": "ctx", "code": "DIFF_CONTEXT_MISMATCH", "phase": "diff", "loc": "not_applicable", "path": ""}, 1


def case(r, i):
    a = gen_doc(r)
    b = gen_doc(r) if r.random() < 0.15 else edit(r, a)
    scope, scope_segs = draw_scope(r, a, b)
    aj, bj, a2, b2 = render(a), render(b), render(a, True), render(b, True)
    inj, ctx = None, 0
    if r.random() < 0.10:
        inj, ctx = inject(r, [aj, a2], [bj, b2])
    return {"id": i, "a": aj, "b": bj, "a2": a2, "b2": b2, "scope": scope, "scope_segs": scope_segs, "ctx_b": ctx, "inject": inj}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20261026)
    a = ap.parse_args()
    r = random.Random(a.seed)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"tree": TREE}, sort_keys=True) + "\n")
        for i in range(a.count):
            f.write(json.dumps(case(r, i), sort_keys=True, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
