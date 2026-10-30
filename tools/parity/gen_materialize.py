#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the flat-materialization differential (P5-2; rust/src/materialize.rs and go/materialize.go, engine MaterializedObject).

    gen_materialize.py OUT_JSONL [--count N] [--seed S]

Deterministic. Each line is {"id", "tag", "schema", "doc", "caps", "keys", "want"}: the text of a schema, the text of a document, the capability map, the names the
schema declares (the generator's own list: it is what the engine's try_new is given as the key set to be complete against, so a key that both languages lose is
seen), the family, and what a reference written here, from the contract of docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 12 and from the generator's own knowledge of
the case, says the outcome is: ["ok", expected_keys, projection, digest] or ["err", reason, field].

The reference does not read the schema or the document text. Every case is built from a structure (the fields, which of them the document writes, at most ONE fault
of the document) and both texts are written from it, so the reference is never a second parser. The projection is written by tools/parity/gen_canonical.py (a
writer of the canonical form that shares no code with either language). A document has at most one fault, because the order among the faults of a document is the
validation's, not this contract's; the order among the faults of different layers (the schema, the capabilities, the document, the omitted field) is what the
families `mixed` and `layers` exercise.
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_canonical as gc  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULTS = json.load(open(os.path.join(ROOT, "testdata", "p1", "defaults.json"), encoding="utf-8"))
HEAD = DEFAULTS["cases"][0]["schema"].split("    nodes:\n")[0] + "    nodes:\n"

# Names are compared by byte, so the ones that sort differently by byte, by code point and by UTF-16 are here on purpose.
NAMES = ["alpha", "beta", "gamma", "Zed", "a_b", "b2", "_x", "é", "ÿ", "€", "z", "Z", "a", "B", "10", "9"]
# What a string value may hold: the characters the YAML subset reads back as written, and the five the canonical form escapes.
CHARS = ["a", "Z", "0", " ", "\"", "\\", "/", "\t", "\n", "<", ">", "&", "\u2028", "\u2029", "é", "ü", "€", "\U0001f600", "'", "-", "_", ":"]
KINDS = ["string", "integer", "boolean"]
NESTED = ["composite", "set", "list"]
CAPS = ["implemented", "not_implemented", "deprecated"]


def dq(s):
    """A YAML double-quoted scalar. U+2028, U+2029 and every control character are written as escapes, so no line break of the YAML 1.2 set is raw."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif o < 0x20 or o == 0x7F or ch in "\u2028\u2029\u0085":
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def scalar_text(v):
    if v is None:
        return "~"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    return dq(v)


def value(rng, kind):
    if kind == "string":
        return "".join(rng.choice(CHARS) for _ in range(rng.randint(0, 6)))
    if kind == "integer":
        return rng.choice([0, 1, -1, 7, 255, 65535, 2**53 + 1, gc.I64_MAX, gc.I64_MIN, rng.randint(-1000, 1000)])
    return rng.random() < 0.5


def wrong_value(rng, kind):
    """A scalar of another type than the field's."""
    return {"string": 12, "integer": "big", "boolean": "x"}[kind] if kind != "string" else rng.choice([True, 12])


def schema_text(fields):
    out = [HEAD]
    for f in fields:
        out.append("      - name: %s\n" % dq(f["name"]))
        kind = f["kind"]
        if kind in KINDS:
            out.append("        shape_type: scalar\n        scalar_type: %s\n" % kind)
            out.append("        mandatory: true\n" if f["presence"] == "mandatory" else "        optional: true\n")
            if f["nullable"]:
                out.append("        nullable: true\n")
            if f["default"] is not None:
                out.append("        default: %s\n" % scalar_text(f["default"][0]))
        elif kind == "composite":
            out.append("        shape_type: composite\n        optional: true\n        fields:\n          - name: g\n            shape_type: scalar\n"
                       "            scalar_type: string\n            optional: true\n")
        elif kind == "set":
            out.append("        shape_type: collection\n        collection_variant: set\n        item_scalar_type: string\n        optional: true\n"
                       "        role:\n          authority: config\n")
        else:
            out.append("        shape_type: collection\n        collection_variant: list\n        optional: true\n        item_fields:\n"
                       "          - name: to\n            shape_type: scalar\n            scalar_type: string\n            role: key\n")
    return "".join(out)


def doc_text(fields, written, fault, rng):
    """The document: the fields written (a name -> value map, `~` for null), in a shuffled order, with at most one fault."""
    lines = [(n, v) for n, v in written]
    rng.shuffle(lines)
    return "".join("%s: %s\n" % (dq(n), scalar_text(v)) for n, v in lines) or "{}\n"


def projection(fields_out):
    """The projection and its digest by the independent writer of the canonical form."""
    items = []
    for key, cap, prov, v in sorted(fields_out, key=lambda x: x[0].encode("utf-8")):
        spec = v if v is None or isinstance(v, (bool, str)) else {"i": str(v)}
        items.append({"m": [["key", key], ["capability", cap], ["provenance", prov], ["value", spec]]})
    return gc.want({"m": [["fields", items]]})


def reference(fields, caps, written, fault):
    """-> ["ok", expected_keys, projection, digest] or ["err", reason, field], by the contract, in the order the contract fixes."""
    for f in fields:
        if f["kind"] in ("composite", "list"):
            return ["err", "not_flat", f["name"]]
        if f["kind"] == "set":
            return ["err", "set", f["name"]]
    declared = [f["name"] for f in fields]
    for k in sorted(caps, key=lambda k: k.encode("utf-8")):
        if k not in declared:
            return ["err", "capability_undeclared", k]
    for n in declared:
        if n not in caps:
            return ["err", "capability_missing", n]
        if caps[n] not in CAPS:
            return ["err", "capability_unknown", n]
    if fault in ("unknown_field", "missing_mandatory", "null_not_allowed"):
        return ["err", "invalid_document", ""]
    if fault == "wrong_type":
        return ["err", "unsupported", ""]
    got = dict(written)
    out = []
    for f in fields:
        n = f["name"]
        if n in got:
            out.append((n, caps[n], "authored", got[n]))
        elif f["default"] is not None:
            out.append((n, caps[n], "schema_default", f["default"][0]))
        else:
            return ["err", "omitted_optional", n]
    w = projection(out)
    return ["ok", sorted(declared, key=lambda k: k.encode("utf-8")), w[1], w[2]]


def make_fields(rng, nested_p):
    names = rng.sample(NAMES, rng.randint(1, 6))
    fields = []
    for n in names:
        if rng.random() < nested_p:
            fields.append({"name": n, "kind": rng.choice(NESTED), "presence": "optional", "nullable": False, "default": None})
            continue
        kind = rng.choice(KINDS)
        presence = rng.choice(["mandatory", "optional", "optional"])
        nullable = rng.random() < 0.25
        default = None
        if presence == "optional" and rng.random() < 0.6:
            default = (value(rng, kind),)
        fields.append({"name": n, "kind": kind, "presence": presence, "nullable": nullable, "default": default})
    return fields


def make_doc(rng, fields, omit_p, fault_p):
    """-> (written [(name, value)], fault). A mandatory field is written unless the fault is its absence; a field with a default or without may be left out."""
    written = []
    scalar = [f for f in fields if f["kind"] in KINDS]
    fault = None
    if scalar and rng.random() < fault_p:
        fault = rng.choice(["unknown_field", "missing_mandatory", "null_not_allowed", "wrong_type"])
        if fault == "missing_mandatory" and not any(f["presence"] == "mandatory" for f in scalar):
            fault = "unknown_field"
        if fault == "null_not_allowed" and not any(not f["nullable"] for f in scalar):
            fault = "wrong_type"
    victim = None
    if fault == "missing_mandatory":
        victim = rng.choice([f for f in scalar if f["presence"] == "mandatory"])["name"]
    elif fault == "null_not_allowed":
        victim = rng.choice([f for f in scalar if not f["nullable"]])["name"]
    elif fault == "wrong_type":
        victim = rng.choice(scalar)["name"]
    for f in scalar:
        n = f["name"]
        if n == victim and fault == "missing_mandatory":
            continue
        if n == victim and fault == "null_not_allowed":
            written.append((n, None))
        elif n == victim and fault == "wrong_type":
            written.append((n, wrong_value(rng, f["kind"])))
        elif f["presence"] == "mandatory" or rng.random() >= omit_p:
            written.append((n, None if f["nullable"] and rng.random() < 0.2 else value(rng, f["kind"])))
    if fault == "unknown_field":
        extra = rng.choice([n for n in NAMES + ["unknown", "k"] if n not in [f["name"] for f in fields]])
        written.append((extra, value(rng, "string")))
    return written, fault


def make_caps(rng, fields, fault_p):
    caps = {f["name"]: rng.choice(CAPS) for f in fields}
    if rng.random() < fault_p:
        k = rng.choice(["missing", "unknown", "undeclared", "undeclared_many"])
        names = [f["name"] for f in fields]
        if k == "missing":
            del caps[rng.choice(names)]
        elif k == "unknown":
            caps[rng.choice(names)] = rng.choice(["Implemented", "unverified", "", "implemented ", "unknown"])
        else:
            for x in rng.sample([n for n in NAMES + ["q", "r", "s"] if n not in names], 3 if k == "undeclared_many" else 1):
                caps[x] = rng.choice(CAPS)
    return caps


# tag -> (probability of a nested field, of an omitted optional field, of a fault in the document, of a fault in the capabilities)
FAMILIES = {
    "valid": (0.0, 0.3, 0.0, 0.0),
    "omitted": (0.0, 0.7, 0.0, 0.0),
    "capabilities": (0.0, 0.2, 0.0, 1.0),
    "nested": (0.5, 0.2, 0.0, 0.0),
    "document": (0.0, 0.2, 1.0, 0.0),
    "layers": (0.3, 0.5, 0.5, 0.5),
    "mixed": (0.15, 0.4, 0.3, 0.3),
}
TAGS = [("valid", 6), ("omitted", 2), ("capabilities", 3), ("nested", 2), ("document", 3), ("layers", 3), ("mixed", 3)]


def make(rng, tag):
    nested_p, omit_p, doc_p, cap_p = FAMILIES[tag]
    fields = make_fields(rng, nested_p)
    caps = make_caps(rng, fields, cap_p)
    written, fault = make_doc(rng, fields, omit_p, doc_p)
    keys = sorted((f["name"] for f in fields), key=lambda k: k.encode("utf-8"))
    return {"tag": tag, "schema": schema_text(fields), "doc": doc_text(fields, written, fault, rng), "caps": caps, "keys": keys,
            "want": reference(fields, caps, written, fault)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20261030)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    tags = [t for t, w in TAGS for _ in range(w)]
    with open(a.out, "w", encoding="utf-8") as f:
        for i in range(a.count):
            case = make(rng, tags[i % len(tags)])
            case["id"] = i
            f.write(json.dumps(case, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
