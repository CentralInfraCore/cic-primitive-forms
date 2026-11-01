#!/usr/bin/env python3
"""Writes testdata/p5/end-to-end.json, the cases of the end-to-end proof of P5-3 (docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 21).

P5-3 end-to-end proof WITHOUT REFERENCE RESOLUTION: schema source bytes -> verified compile and identity; authored document -> parse -> materialize -> receipt_flat_fixture ->
the pinned engine's verifier. The capability map is an explicit caller input (the P5-2 contract); where it comes from is not claimed to be authenticated.

Each case is run twice: with its capability map ("base") and with ONE capability changed ("alt"). The expected values of both runs are computed HERE, in Python, from the
fields the case lists (never from the Rust or the Go code): the canonical bytes of the projection and its digest, the authored input and its digest, the default evidence,
the `$.field -> field` binding and the whole receipt. Python's own JSON writer and hashlib are the reference.

The case with two defaulted fields of EQUAL value (`twin-defaults`) is there for the swapped binding: the engine's consistency check cannot tell the two bindings apart.

    python3 tools/p5/end_to_end_cases.py --write     rewrite the file
    python3 tools/p5/end_to_end_cases.py             check that the file is the one this script writes (exit 1 when not)
"""
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import materialize_cases as mc  # noqa: E402
import receipt_cases as rc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "testdata" / "p5" / "end-to-end.json"

CAP = dict(mc.CAP, active="implemented")
ACTIVE = "      - name: active\n        shape_type: scalar\n        scalar_type: boolean\n        optional: true\n        default: true\n"
SCHEMAS = {
    "flat": mc.SCHEMAS["flat"],
    "flat-note": mc.SCHEMAS["flat-note"],
    # `enabled` and `active` are both defaulted to true: two evidence entries with one value digest
    "flat-twin": mc.HEAD + mc.SCALARS["type"] + mc.SCALARS["enabled"] + ACTIVE,
}


def field(key, provenance, value, capability=None):
    return {"key": key, "capability": capability or CAP[key], "provenance": provenance, "value": value}


def caps_of(fields):
    return {x["key"]: x["capability"] for x in fields}


# (id, schema, yaml, fields, the field whose capability the alt run changes, and what it changes to)
CASES = [
    ("zero-defaults", "flat", "type: t\nenabled: false\nmtu: 0\nlabel: \"\"\nmaybe: ~\n", [
        field("enabled", "authored", False), field("label", "authored", ""), field("maybe", "authored", None),
        field("mtu", "authored", 0), field("type", "authored", "t")], "type", "deprecated"),
    ("one-default", "flat-note", "type: t\nnote: n\n", [
        field("enabled", "schema_default", True), field("note", "authored", "n"), field("type", "authored", "t")], "note", "not_implemented"),
    ("many-defaults", "flat", "type: t\n", [
        field("enabled", "schema_default", True), field("label", "schema_default", "x"), field("maybe", "schema_default", "d"),
        field("mtu", "schema_default", 1500), field("type", "authored", "t")], "mtu", "implemented"),
    ("twin-defaults", "flat-twin", "type: t\n", [
        field("active", "schema_default", True), field("enabled", "schema_default", True), field("type", "authored", "t")], "active", "deprecated"),
]


def expected(schema_text, fields):
    """-> the expected values of ONE run, from the listed fields only."""
    raw = rc.canon({"fields": fields})
    output_digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    authored, document, binding = rc.receipt_for(schema_text, raw, output_digest)
    authored_value = {x["key"]: x["value"] for x in fields if x["provenance"] == "authored"}
    return {
        "capabilities": caps_of(fields),
        "expected_keys": sorted(x["key"] for x in fields),
        "projection": raw,
        "output_digest": output_digest,
        "authored_input": authored,
        "input_digest": rc.digest(authored_value),
        "document": document,
        "binding": binding,
    }


def build():
    cases = []
    for cid, schema_id, yaml, fields, key, to in CASES:
        text = SCHEMAS[schema_id]
        alt = [dict(x, capability=to) if x["key"] == key else x for x in fields]
        assert alt != fields, cid
        cases.append({
            "id": cid,
            "schema": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "yaml": yaml,
            "base": expected(text, fields),
            "alt": expected(text, alt),
            "alt_changes": {"field": key, "to": to},
        })
    return {
        "engine_identity_token": rc.TOKEN,
        "engine_version": rc.ENGINE_VERSION,
        "release": rc.RELEASE,
        "grammar": mc.DEFAULTS["grammar"],
        "access_keys": mc.DEFAULTS["access_keys"],
        "cases": cases,
    }


def main():
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if "--write" in sys.argv:
        OUT.write_text(text)
        return 0
    if OUT.read_text() != text:
        print("testdata/p5/end-to-end.json is not what tools/p5/end_to_end_cases.py writes", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
