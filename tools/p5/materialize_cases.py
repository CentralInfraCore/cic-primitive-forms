#!/usr/bin/env python3
"""Writes testdata/p5/materialize.json, the shared cases of the flat materialization (P5-2, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 11).

The expected projection and digest of an accepted case are computed HERE, from the fields the case lists, with Python's own JSON writer and hashlib: a third
reader of the canonical form that has never seen the Rust or the Go code. A refused case lists the reason and the field the two languages must both give.

    python3 tools/p5/materialize_cases.py --write     rewrite the file
    python3 tools/p5/materialize_cases.py             check that the file is the one this script writes (exit 1 when not)
"""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "testdata" / "p5" / "materialize.json"
DEFAULTS = json.loads((ROOT / "testdata" / "p1" / "defaults.json").read_text())

HEAD = DEFAULTS["cases"][0]["schema"].split("    nodes:\n")[0] + "    nodes:\n"

SCALARS = {
    "type": "      - name: type\n        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n",
    "enabled": "      - name: enabled\n        shape_type: scalar\n        scalar_type: boolean\n        optional: true\n        default: true\n",
    "mtu": "      - name: mtu\n        shape_type: scalar\n        scalar_type: integer\n        optional: true\n        default: 1500\n",
    "label": "      - name: label\n        shape_type: scalar\n        scalar_type: string\n        optional: true\n        default: x\n",
    "maybe": "      - name: maybe\n        shape_type: scalar\n        scalar_type: string\n        optional: true\n        nullable: true\n        default: d\n",
    "note": "      - name: note\n        shape_type: scalar\n        scalar_type: string\n        optional: true\n",
    # two names whose byte order is not their UTF-16 order: U+E000 (EE 80 80) sorts before U+1F600 (F0 9F 98 80), but as UTF-16 the surrogate D83D sorts before E000
    "\ue000": "      - name: \"\ue000\"\n        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n",
    "\U0001f600": "      - name: \"\U0001f600\"\n        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n",
    "zone": "      - name: zone\n        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n",
}
NESTED = {
    "ip": "      - name: ip\n        shape_type: composite\n        optional: true\n        nullable: true\n        fields:\n          - name: gateway\n            shape_type: scalar\n            scalar_type: string\n            optional: true\n            default: 10.0.0.1\n",
    "tags": "      - name: tags\n        shape_type: collection\n        collection_variant: set\n        item_scalar_type: string\n        optional: true\n        role:\n          authority: config\n        default: [a]\n",
    "routes": "      - name: routes\n        shape_type: collection\n        collection_variant: list\n        optional: true\n        item_fields:\n          - name: to\n            shape_type: scalar\n            scalar_type: string\n            role: key\n",
}


def schema(*names):
    return HEAD + "".join((SCALARS | NESTED)[n] for n in names)


SCHEMAS = {
    "flat": schema("type", "enabled", "mtu", "label", "maybe"),
    "flat-note": schema("type", "enabled", "note"),
    "flat-keyed": schema("zone", "type"),
    "flat-utf16": schema("\U0001f600", "\ue000"),
    "nested": schema("type", "ip"),
    "with-set": schema("type", "tags"),
    "with-list": schema("type", "routes"),
}
CAP = {"\ue000": "implemented", "\U0001f600": "not_implemented", "type": "implemented", "enabled": "not_implemented", "mtu": "deprecated", "label": "implemented", "maybe": "implemented", "note": "implemented", "zone": "implemented", "ip": "implemented", "tags": "implemented", "routes": "implemented"}


def caps(*names, **over):
    c = {n: CAP[n] for n in names}
    c.update(over)
    return c


FLAT5 = ("type", "enabled", "mtu", "label", "maybe")


def ok(id, schema_id, yaml, capabilities, fields):
    return {"id": id, "schema": schema_id, "yaml": yaml, "capabilities": capabilities, "fields": fields}


def no(id, schema_id, yaml, capabilities, reason, field):
    return {"id": id, "schema": schema_id, "yaml": yaml, "capabilities": capabilities, "refused": [reason, field]}


def f(key, prov, value):
    return {"key": key, "capability": CAP[key], "provenance": prov, "value": value}


CASES = [
    ok("flat.minimal", "flat", "type: t\n", caps(*FLAT5), [
        f("enabled", "schema_default", True), f("label", "schema_default", "x"), f("maybe", "schema_default", "d"),
        f("mtu", "schema_default", 1500), f("type", "authored", "t")]),
    ok("flat.all-written", "flat", "type: t\nenabled: false\nmtu: 0\nlabel: \"\"\nmaybe: ~\n", caps(*FLAT5), [
        f("enabled", "authored", False), f("label", "authored", ""), f("maybe", "authored", None),
        f("mtu", "authored", 0), f("type", "authored", "t")]),
    ok("flat.equal-to-default-is-authored", "flat", "type: t\nenabled: true\nmtu: 1500\n", caps(*FLAT5), [
        f("enabled", "authored", True), f("label", "schema_default", "x"), f("maybe", "schema_default", "d"),
        f("mtu", "authored", 1500), f("type", "authored", "t")]),
    ok("flat.escaping", "flat", "type: \"a<b>&c\\u2028\\td\"\nlabel: \"é/ü\"\n", caps(*FLAT5), [
        f("enabled", "schema_default", True), f("label", "authored", "é/ü"), f("maybe", "schema_default", "d"),
        f("mtu", "schema_default", 1500), f("type", "authored", "a<b>&c\u2028\td")]),
    ok("flat.note-written", "flat-note", "type: t\nnote: n\n", caps("type", "enabled", "note"), [
        f("enabled", "schema_default", True), f("note", "authored", "n"), f("type", "authored", "t")]),
    ok("flat.keys-sort-by-bytes-not-schema-order", "flat-keyed", "zone: z\ntype: t\n", caps("zone", "type"), [
        f("type", "authored", "t"), f("zone", "authored", "z")]),
    ok("flat.names-sort-by-bytes-not-by-utf16", "flat-utf16", "\"\U0001f600\": a\n\"\ue000\": b\n", caps("\U0001f600", "\ue000"), [
        f("\ue000", "authored", "b"), f("\U0001f600", "authored", "a")]),
    no("refused.omitted-optional-without-default", "flat-note", "type: t\n", caps("type", "enabled", "note"), "omitted_optional", "note"),
    no("refused.nested-schema", "nested", "type: t\n", caps("type"), "not_flat", "ip"),
    no("refused.nested-schema-even-when-the-document-is-fine", "nested", "type: t\nip:\n  gateway: g\n", caps("type", "ip"), "not_flat", "ip"),
    no("refused.set-schema", "with-set", "type: t\n", caps("type"), "set", "tags"),
    no("refused.list-schema", "with-list", "type: t\n", caps("type"), "not_flat", "routes"),
    no("refused.capability-missing", "flat", "type: t\n", caps("type", "enabled", "mtu", "label"), "capability_missing", "maybe"),
    no("refused.capability-empty-map", "flat", "type: t\n", {}, "capability_missing", "type"),
    no("refused.capability-unknown-spelling", "flat", "type: t\n", caps(*FLAT5, mtu="Implemented"), "capability_unknown", "mtu"),
    no("refused.capability-unverified", "flat", "type: t\n", caps(*FLAT5, label="unverified"), "capability_unknown", "label"),
    no("refused.capability-undeclared-key", "flat", "type: t\n", caps(*FLAT5, zzz="implemented", aaa="implemented"), "capability_undeclared", "aaa"),
    no("refused.invalid-document-unknown-field", "flat", "type: t\nbogus: 1\n", caps(*FLAT5), "invalid_document", ""),
    no("refused.invalid-document-mandatory-missing", "flat", "label: l\n", caps(*FLAT5), "invalid_document", ""),
    no("refused.wrong-scalar-type-is-unsupported-by-the-validation", "flat", "type: t\nmtu: big\n", caps(*FLAT5), "unsupported", ""),
]


def build():
    cases = []
    for c in CASES:
        c = dict(c)
        if "fields" in c:
            projection = {"fields": c["fields"]}
            raw = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
                raw = raw.replace(ch, esc)
            c["expect"] = {
                "expected_keys": sorted(x["key"] for x in c["fields"]),
                "projection": raw,
                "output_digest": "sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
            }
            del c["fields"]
        else:
            c["expect"] = {"refused": c.pop("refused")}
        cases.append(c)
    return {
        "grammar": DEFAULTS["grammar"],
        "access_keys": DEFAULTS["access_keys"],
        "schemas": SCHEMAS,
        "cases": cases,
    }


def main():
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if "--write" in sys.argv:
        OUT.write_text(text)
        return 0
    if OUT.read_text() != text:
        print("testdata/p5/materialize.json is not what tools/p5/materialize_cases.py writes", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
