#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""An independent model of `diff` (ADR 0008), written in Python from the ADR, for the differential of P4 step D3b.

    diff_model.py --selftest          run the model on every vector of corpus/diff (tree `main`): it must reproduce all 66 canonical strings

The model is the third opinion next to the Rust and the Go implementations. It shares no code with them: it works on plain JSON-like trees (dict = mapping,
list = sequence, `{"__u": "1.5"}` = an unsupported number), writes the canonical bytes with `json.dumps` (sorted keys, no whitespace, no ASCII escaping: the
rule of ADR 0008 for the strings the model meets), and reads the schema in the tree notation of the corpus (`trees:` of corpus/diff/vectors.yaml).
Its author is the author of the implementations, so it is a cross-check against transcription errors and shared bugs of the code, not an independent reading of
the ADR; what makes it worth having is that it has to reproduce the 66 hand-derived vectors first (the same bar as the code), and that its structure is not the
code's (events are built bottom-up from the expanded trees, the order is one sort at the end, the canonical bytes are not written by hand).

It models VALID operands only. A refusal (invalid view, unsupported number, context mismatch) is checked by the generator's own expectation, not here.
"""
import json
import sys

DECL = ["access", "modify", "inherit", "default_injection", "conformance"]
DEFAULTS = {"access": "inherit", "modify": "inherit", "inherit": True, "default_injection": None, "conformance": "implemented"}
A_KEYS = set(DECL) | {"value"}


class Model(Exception):
    pass


def tj(v):
    """A typed, canonical text of a JSON value: 1 and true are different."""
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- the schema in the notation of the corpus ----------------------------------------------------------------------------------------------

def parse_field(spec):
    if spec == "opaque":
        return ("opaque", None)
    (kind, body), = spec.items()
    if kind == "scalar":
        return ("scalar", body)
    if kind == "set":
        return ("set", body)
    if kind == "composite":
        return ("comp", {n: parse_field(s) for n, s in body.items()})
    if "choice" in body:
        cases = [(c["name"], c["key"], {n: parse_field(s) for n, s in c["fields"].items()}) for c in body["choice"]]
        return ("list", (True, cases))
    return ("list", (False, [("", body["key"], {n: parse_field(s) for n, s in body["fields"].items()})]))


def parse_tree(tree):
    return {n: parse_field(s) for n, s in tree.items()}


# --- reading a view: the expanded form ---------------------------------------------------------------------------------------------------

def is_wrapper(x):
    return isinstance(x, dict) and set(x) == A_KEYS


def through(x):
    return x["value"] if is_wrapper(x) else x


def identify(rec, choice, cases):
    """(case index, identity) of a record: the case is selected by field presence (ADR 0002)."""
    fits = []
    for i, (_, key, fields) in enumerate(cases):
        if all(k in fields for k in rec) and all(k in rec for k in key):
            fits.append(i)
    if not choice:
        fits = [0]
    if len(fits) != 1:
        raise Model("no single case")
    key = cases[fits[0]][1]
    return fits[0], tuple((k, through(rec[k])) for k in key)


def expand_position(raw, spec):
    expanded = is_wrapper(raw)
    value = raw["value"] if expanded else raw
    decl = {k: raw[k] for k in DECL} if expanded else dict(DEFAULTS)
    return {"decl": decl, "value": expand_value(value, spec), "expanded": expanded}


def expand_record(raw, fields):
    return {n: expand_position(v, fields[n]) for n, v in raw.items()}


def expand_value(v, spec):
    kind, body = spec
    if v is None or kind in ("scalar", "opaque", "set"):
        return v
    if kind == "comp":
        return expand_record(v, body)
    choice, cases = body
    out = []
    for rec in v:
        ci, ident = identify(rec, choice, cases)
        out.append((ci, ident, expand_record(rec, cases[ci][2])))
    return out


# --- paths ---------------------------------------------------------------------------------------------------------------------------------------

def key_text(v):
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def path_text(segs):
    out = "$"
    for s in segs:
        if s[0] == "f":
            out += "." + s[1]
        else:
            out += "[{" + ",".join("%s=%s" % (k, key_text(v)) for k, v in s[1]) + "}]"
    return out


def seg_order(s):
    """The order of ADR 0007: fields (by UTF-8 bytes) before items; items by case components in declared order, strings by bytes, integers numerically."""
    if s[0] == "f":
        return (0, s[1].encode("utf-8"))
    return (1, tuple((k.encode("utf-8"), (0, v.encode("utf-8")) if isinstance(v, str) else (1, v)) for k, v in s[1]))


def path_order(segs):
    return tuple(seg_order(s) for s in segs)


# --- fragments -----------------------------------------------------------------------------------------------------------------------------------

def scalar_order(v):
    if v is None:
        return (0, 0)
    if isinstance(v, bool):
        return (1, int(v))
    if isinstance(v, int):
        return (2, v)
    return (3, v.encode("utf-8"))


def sorted_set(items):
    seen, out = set(), []
    for x in sorted(items, key=scalar_order):
        if tj(x) not in seen:
            seen.add(tj(x))
            out.append(x)
    return out


def frag_value(v, spec):
    kind, body = spec
    if v is None:
        return None
    if kind == "set":
        return sorted_set(v)
    if kind == "comp":
        return frag_record(v, body)
    if kind == "list":
        order = sorted(v, key=lambda e: (e[0], tuple((k.encode("utf-8"), scalar_order(x)) for k, x in e[1])))
        return [frag_record(rec, body[1][ci][2]) for ci, _, rec in order]
    return v


def frag_position(p, spec):
    out = dict(p["decl"])
    out["value"] = frag_value(p["value"], spec)
    return out


def frag_record(rec, fields):
    return {n: frag_position(p, fields[n]) for n, p in rec.items()}


# --- the diff -------------------------------------------------------------------------------------------------------------------------------------

class Diff:
    def __init__(self):
        self.changes = []
        self.rep = []

    def event(self, segs, kind, **fields):
        self.changes.append((path_order(segs), kind, dict(fields, kind=kind, path=path_text(segs))))

    def gone(self, kind, segs, frag):
        self.event(segs, kind, **({"next": frag} if kind == "added" else {"previous": frag}))

    def record(self, fields, ra, rb, segs):
        for name, spec in fields.items():
            here = segs + [("f", name)]
            if name in ra and name in rb:
                self.position(spec, ra[name], rb[name], here)
            elif name in ra:
                self.gone("removed", here, frag_position(ra[name], spec))
            elif name in rb:
                self.gone("added", here, frag_position(rb[name], spec))

    def position(self, spec, pa, pb, segs):
        changed = {k: {"previous": pa["decl"][k], "next": pb["decl"][k]} for k in DECL if tj(pa["decl"][k]) != tj(pb["decl"][k])}
        if changed:
            self.event(segs, "policy_declaration_change", changed=changed)
        if pa["expanded"] != pb["expanded"]:
            self.rep.append((path_order(segs), {"kind": "representation_change", "path": path_text(segs),
                                                "previous": "expanded" if pa["expanded"] else "short",
                                                "next": "expanded" if pb["expanded"] else "short"}))
        self.value(spec, pa["value"], pb["value"], segs)

    def value(self, spec, va, vb, segs):
        kind, body = spec
        if kind in ("scalar", "opaque"):
            if tj(va) != tj(vb):
                self.event(segs, "value_change", previous=va, next=vb)
            return
        if va is None or vb is None:
            if va is None and vb is None:
                return
            raise Model("null against a collection")
        if kind == "set":
            have_a, have_b = {tj(x) for x in va}, {tj(x) for x in vb}
            added = sorted_set([x for x in vb if tj(x) not in have_a])
            removed = sorted_set([x for x in va if tj(x) not in have_b])
            if added or removed:
                self.event(segs, "set_change", added=added, removed=removed)
        elif kind == "comp":
            self.record(body, va, vb, segs)
        else:
            cases = body[1]
            ka = {(ci, ident): rec for ci, ident, rec in va}
            kb = {(ci, ident): rec for ci, ident, rec in vb}
            for (ci, ident), rec in ka.items():
                here = segs + [("i", ident)]
                if (ci, ident) in kb:
                    self.record(cases[ci][2], rec, kb[(ci, ident)], here)
                else:
                    self.gone("removed", here, frag_record(rec, cases[ci][2]))
            for (ci, ident), rec in kb.items():
                if (ci, ident) not in ka:
                    self.gone("added", segs + [("i", ident)], frag_record(rec, cases[ci][2]))


def locate(root_rec, fields, segs):
    """The place a scope entry names in an expanded tree: ('record', rec, fields) or ('position', p, spec); None when the tree does not have it."""
    cur = ("record", root_rec, fields)
    for s in segs:
        if s[0] == "f":
            if cur[0] == "record":
                rec, flds = cur[1], cur[2]
            else:
                if cur[2][0] != "comp" or cur[1]["value"] is None:
                    raise Model("below a value that is not a mapping")
                rec, flds = cur[1]["value"], cur[2][1]
            if s[1] not in rec:
                return None
            cur = ("position", rec[s[1]], flds[s[1]])
        else:
            p, spec = cur[1], cur[2]
            if cur[0] != "position" or spec[0] != "list" or p["value"] is None:
                raise Model("a list element below a value that is not a list")
            hit = [(ci, ident, rec) for ci, ident, rec in p["value"] if ident == s[1]]
            if not hit:
                return None
            ci, _, rec = hit[0]
            cur = ("record", rec, spec[1][1][ci][2])
    return cur


def reduce_scope(entries):
    keep = []
    for i, e in enumerate(entries):
        inside = any(len(o) < len(e) and e[:len(o)] == o for o in entries)
        repeated = any(o == e for o in entries[:i])
        if not inside and not repeated:
            keep.append(e)
    return keep


def diff(a_doc, b_doc, tree, scope=None):
    """The canonical bytes of the diff of two valid documents. `scope` is None or a list of segment lists."""
    fields = parse_tree(tree)
    ea, eb = expand_record(a_doc, fields), expand_record(b_doc, fields)
    d = Diff()
    for e in ([[]] if scope is None else reduce_scope(scope)):
        la, lb = locate(ea, fields, e), locate(eb, fields, e)
        if la is None and lb is None:
            continue
        if la is not None and lb is not None:
            if la[0] == "record":
                d.record(la[2], la[1], lb[1], e)
            else:
                d.position(la[2], la[1], lb[1], e)
            continue
        kind, here = ("removed", la) if la is not None else ("added", lb)
        frag = frag_record(here[1], here[2]) if here[0] == "record" else frag_position(here[1], here[2])
        d.gone(kind, e, frag)
    changes = [c[2] for c in sorted(d.changes, key=lambda c: (c[0], c[1].encode("utf-8")))]
    rep = [r[1] for r in sorted(d.rep, key=lambda r: r[0])]
    return json.dumps({"changes": changes, "representation": rep, "version": 1}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- the self-test against the vectors --------------------------------------------------------------------------------------------------------

def parse_path_text(text):
    """The paths of the vectors' scope: $ followed by .name and [{k="v",k=n}] segments (no escapes beyond \\" and \\\\)."""
    segs, i = [], 1
    while i < len(text):
        if text[i] == ".":
            j = i + 1
            while j < len(text) and text[j] not in ".[":
                j += 1
            segs.append(("f", text[i + 1:j]))
            i = j
        else:
            j = text.index("}]", i)
            comps, body, k = [], text[i + 2:j], 0
            while k < len(body):
                eq = body.index("=", k)
                name = body[k:eq]
                if body[eq + 1] == '"':
                    m, val = eq + 2, ""
                    while body[m] != '"':
                        if body[m] == "\\":
                            m += 1
                        val += body[m]
                        m += 1
                    k = m + 1
                else:
                    m = eq + 1
                    while m < len(body) and body[m] != ",":
                        m += 1
                    val, k = int(body[eq + 1:m]), m
                if k < len(body) and body[k] == ",":
                    k += 1
                comps.append((name, val))
            segs.append(("i", tuple(comps)))
            i = j + 2
    return segs


def selftest():
    import os
    import yaml
    here = os.path.dirname(os.path.abspath(__file__))
    suite = yaml.safe_load(open(os.path.join(here, "..", "..", "corpus", "diff", "vectors.yaml"), encoding="utf-8"))
    tree = suite["trees"]["main"]
    ok = bad = 0
    for c in suite["cases"]:
        if c["polarity"] != "positive" or c["kind"] not in ("diff", "diff_stability"):
            continue
        for v in (c["variants"] if c["kind"] == "diff_stability" else [c]):
            a = yaml.safe_load(v["a_yaml"]) if "a_yaml" in v else v["a"]
            b = yaml.safe_load(v["b_yaml"]) if "b_yaml" in v else v["b"]
            scope = [parse_path_text(p) for p in c["scope"]] if "scope" in c else None
            got = diff(a, b, tree, scope)
            if got == c["expect"]["canonical"]:
                ok += 1
            else:
                bad += 1
                print("MISMATCH %s\n got  %s\n want %s" % (c["id"], got, c["expect"]["canonical"]))
    print("model: %d vectors reproduced, %d differ" % (ok, bad))
    return 1 if bad or ok < 58 else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
