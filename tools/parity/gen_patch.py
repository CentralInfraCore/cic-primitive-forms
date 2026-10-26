#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the patch differential (P4 step P4-3: ADR 0013, Rust against Go against a model).

    gen_patch.py OUT_JSONL [--count N] [--seed S]

Deterministic. The first line is `{"tree": ...}`, the schema in the tree notation of the corpus (the dumps read it); each other line is
    {"id", "yaml": text of A, "a": A as a plain JSON tree (null if the text is not a valid document), "b": B as a plain JSON tree, "scope": [text]|null,
     "scope_segs": [[seg]]|null, "special": {path: code}, "tricky": bool}
`a` is what the generator MEANS by the text, written alongside the text and not read back from it, so it is a second statement of the same document.

A is a document of the tree of corpus/edit (name, count, enabled, ip, routes, tags) with an opaque `note`, drawn like tools/parity/gen_edit.py draws it: plain,
single and double quoted scalars, comments, non-ASCII, LF, CRLF and lone CR line ends, a BOM, no final newline, an expanded wrapper, and the scalars a patch
cannot replace: a block scalar, a multi-line scalar, a position in a flow mapping (`special` names the refusal each earns). B is A with a few scalar edits
(the case a patch can apply), or with a structural change, a change of the opaque value, an independent draw, or nothing; the new values are clean words,
integers and booleans, and in one case in six strings a plain scalar cannot carry (`tricky`). The scope is drawn from the paths of A and B. One case in twenty has
an A that does not parse or validate.
"""
import argparse
import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_model as dm  # noqa: E402  (only path_text: nothing of the diff itself)

TREE = {
    "name": {"scalar": "string"},
    "count": {"scalar": "integer"},
    "enabled": {"scalar": "boolean"},
    "ip": {"composite": {"gateway": {"scalar": "string"}, "prefix": {"scalar": "integer"}}},
    "routes": {"list": {"key": ["to"], "fields": {"to": {"scalar": "string"}, "metric": {"scalar": "integer"}}}},
    "tags": {"set": "string"},
    "note": "opaque",
}
EXPANDED_TAIL = ["  access: inherit", "  modify: inherit", "  inherit: true", "  default_injection: ~", "  conformance: implemented"]
WORDS = ["alpha", "beta", "gamma", "s7", "x", "host-1", "a.b", "w3", "tűrő", "árvíz"]
TRICKY = ["it's", 'say "hi" \\', "a: b", "a #b", " lead", "trail ", "true", "12", "null", "~", "-dash", "?q", "", "é", "tab\there", "a\nb", "a\x07b", "yes", "1.5", "a:", "[x", "{y", "a'b", 'a"b']
INTS = [0, 3, 7, 24, -5, 1000, 65535, -9223372036854775808, 9223372036854775807]
NOTES = [None, 0, False, "s", [1, 2], {"x": 1}, {"y": [1]}]
DEFAULT_DECL = {"access": "inherit", "modify": "inherit", "inherit": True, "default_injection": None, "conformance": "implemented"}


def scalar_token(r, kind):
    """(token text, value) of one scalar written in a drawn style."""
    if kind == "string":
        v = r.choice(WORDS)
        style = r.choice(["plain", "plain", "plain", "single", "double", "comment"])
        if style == "single":
            return "'" + v.replace("'", "''") + "'", v
        if style == "double":
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"', v
        return v + ("   # a comment" if style == "comment" else ""), v
    if kind == "integer":
        n = r.choice([0, 3, 7, 24, -5, 1000, 65535])
        return ("+" + str(n) if n >= 0 and r.random() < 0.3 else str(n)), n
    b = r.choice([True, False])
    return (("True" if b else "False") if r.random() < 0.3 else ("true" if b else "false")), b


class Doc:
    """A document: its text lines, its meaning as JSON, the expanded positions, and the refusals its scalars earn."""

    def __init__(self):
        self.lines = []
        self.a = {}
        self.special = {}
        self.expanded = set()             # paths written as an expanded wrapper
        self.block = False                # a block scalar is there: the parser refuses it in a text without a final line break

    def put_scalar(self, r, path_key, label, kind, parent, indent=""):
        tok, v = scalar_token(r, kind)
        roll = r.random()
        if roll < 0.08:                                           # an expanded wrapper that holds only the value
            self.lines += [indent + label + ":", indent + "  value: " + tok.split("   #")[0]] + [indent + x for x in EXPANDED_TAIL]
            parent[path_key] = dict(DEFAULT_DECL, value=v)
            return "expanded"
        if kind == "string" and roll < 0.14:                      # a block scalar
            self.lines += [indent + label + ": " + r.choice(["|", ">"]), indent + "  " + v]
            self.block = True
            parent[path_key] = v + "\n"
            return "EDIT_BLOCK_SCALAR"
        if kind == "string" and roll < 0.18:                      # a multi-line plain scalar
            self.lines += [indent + label + ": " + v, indent + "  " + v + "2"]
            parent[path_key] = v + " " + v + "2"
            return "EDIT_MULTILINE_SCALAR"
        self.lines.append(indent + label + ": " + tok)
        parent[path_key] = v
        return None


def build(r):
    d = Doc()
    if r.random() < 0.15:
        d.lines.append("# a leading comment")
    for name, kind in (("name", "string"), ("count", "integer"), ("enabled", "boolean")):
        if r.random() < 0.85:
            how = d.put_scalar(r, name, name, kind, d.a)
            if how == "expanded":
                d.expanded.add("$." + name)
            elif how:
                d.special["$." + name] = how
    if r.random() < 0.6:
        if r.random() < 0.12:                                      # a position in a flow mapping
            g, p = r.choice(WORDS), r.choice([0, 3, 24])
            d.lines.append("ip: {gateway: %s, prefix: %s}" % (g, p))
            d.a["ip"] = {"gateway": g, "prefix": p}
            d.special["$.ip.gateway"] = d.special["$.ip.prefix"] = "EDIT_FLOW_CONTEXT"
        else:
            ip = {}
            d.lines.append("ip:")
            for n, k in (("gateway", "string"), ("prefix", "integer")):
                how = d.put_scalar(r, n, n, k, ip, "  ")
                if how == "expanded":
                    d.expanded.add("$.ip." + n)
                elif how:
                    d.special["$.ip." + n] = how
            d.a["ip"] = ip
    if r.random() < 0.5:
        d.lines.append("routes:")
        routes = []
        for to in r.sample(["a", "b", "c"], r.randint(1, 3)):
            rec = {"to": to}
            d.lines.append("  - to: " + to)
            tok, v = scalar_token(r, "integer")
            d.lines.append("    metric: " + tok)
            rec["metric"] = v
            routes.append(rec)
        d.a["routes"] = routes
    if r.random() < 0.15:
        d.lines += ["tags:", "  - t1", "  - t2"]
        d.a["tags"] = ["t1", "t2"]
    if r.random() < 0.15:
        note = r.choice(NOTES[1:])
        d.lines.append("note: " + json.dumps(note))
        d.a["note"] = note
    return d


def render(r, lines, block=False):
    eol = r.choice(["\n", "\n", "\n", "\r\n", "\r"])
    text = eol.join(lines) + (eol if block or r.random() < 0.85 else "")
    if r.random() < 0.1:
        text = chr(0xFEFF) + text
    return text


# --- B ----------------------------------------------------------------------------------------------------------------------------------------

def value_for(r, kind, tricky):
    if kind == "integer":
        return r.choice(INTS)
    if kind == "boolean":
        return r.choice([True, False])
    if tricky:
        return r.choice(TRICKY)
    return r.choice(WORDS + ["new value", "n-1", "x y"])


KINDS = {"name": "string", "count": "integer", "enabled": "boolean", "gateway": "string", "prefix": "integer", "metric": "integer"}


def scalar_places(a):
    """[(container dict, key, kind, path)] of the scalars a patch could replace, in the document."""
    out = []
    for n in ("name", "count", "enabled"):
        if n in a:
            out.append((a, n, KINDS[n], "$." + n))
    for n in ("gateway", "prefix"):
        if "ip" in a and n in a["ip"]:
            out.append((a["ip"], n, KINDS[n], "$.ip." + n))
    for rec in a.get("routes", []):
        out.append((rec, "metric", "integer", '$.routes[{to="%s"}].metric' % rec["to"]))
    return out


def make_b(r, a, expanded):
    """(B as logical JSON with the wrapper form of A kept, tricky?)"""
    b = copy.deepcopy(a)
    tricky = False
    roll = r.random()
    places = scalar_places(b)
    if roll < 0.06:
        pass                                                    # B is A
    elif roll < 0.14:
        b = gen_logical_only(r)                                 # an independent draw
    elif roll < 0.22:
        kind = r.choice(["note", "remove", "tags", "route"])
        if kind == "note":
            b["note"] = r.choice([n for n in NOTES if n != a.get("note", "x")])
        elif kind == "remove" and places:
            c, k, _, _ = r.choice(places)
            if not isinstance(c.get(k), dict):
                c.pop(k, None)
        elif kind == "tags":
            b["tags"] = r.choice([["t1"], ["t2", "t3"], []])
        else:
            b.setdefault("routes", []).append({"to": r.choice(["z", "y"]), "metric": 1})
    else:
        tricky = r.random() < 1 / 6
        for c, k, kind, path in r.sample(places, min(len(places), r.randint(1, 3))) if places else []:
            new = value_for(r, kind, tricky and kind == "string")
            if isinstance(c[k], dict) and "value" in c[k]:       # an expanded wrapper keeps its declaration
                c[k]["value"] = new
            else:
                c[k] = new
    return b, tricky


def gen_logical_only(r):
    d = build(r)
    return d.a


# --- scope -------------------------------------------------------------------------------------------------------------------------------------

def paths(a):
    out = []
    for n in a:
        out.append([("f", n)])
        if n == "ip":
            out += [[("f", "ip"), ("f", k)] for k in a["ip"]]
        if n == "routes":
            for rec in a["routes"]:
                el = [("f", "routes"), ("i", (("to", rec["to"]),))]
                out.append(el)
                out += [el + [("f", k)] for k in rec if k != "to"]
    return out


def draw_scope(r, a, b):
    if r.random() < 0.55:
        return None, None
    pool = paths(a) + paths(b)
    segs = [r.choice(pool) for _ in range(r.randint(1, 3))] if pool else []
    if r.random() < 0.1:
        segs.append([])
    r.shuffle(segs)
    return [dm.path_text(s) for s in segs], [[list(x) if x[0] == "f" else ["i", [list(c) for c in x[1]]] for x in s] for s in segs]


def case(r, i):
    d = build(r)
    if not d.a:                                                 # a text with no field at all is the null document, not an empty mapping
        d.put_scalar(r, "name", "name", "string", d.a)
    b, tricky = make_b(r, d.a, d.expanded)
    text = render(r, d.lines, d.block)
    a_json = d.a
    invalid = r.random() < 0.05
    if invalid:
        d.lines.append(r.choice(["bogus: 1", "count: three", "enabled: maybe"]))
        text = render(r, d.lines, d.block)
        a_json = None
    scope, scope_segs = draw_scope(r, d.a, b)
    return {"id": i, "yaml": text, "a": a_json, "b": b, "scope": scope, "scope_segs": scope_segs, "special": d.special, "tricky": tricky}


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
