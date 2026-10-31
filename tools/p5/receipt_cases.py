#!/usr/bin/env python3
"""Writes testdata/p5/receipt.json, the shared cases of the flat receipt (P5-3, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 17).

One case per ACCEPTED case of testdata/p5/materialize.json. The expected receipt is computed HERE, with Python's own JSON writer, re.search and hashlib: from the
schema text (name, version, digest), from the fields the materialize case lists (input digest, output digest, applied defaults, their value digests). A reader
of the canonical form that has never seen the Rust or the Go code. The release and grammar fields are marked FIXTURE data: well-formed, invented, not a
release.

The one field that differs between the two implementations is `engine_identity`; the file holds the token ENGINE_IDENTITY_TOKEN there and the tests put the
identity of their own language in its place.

    python3 tools/p5/receipt_cases.py --write     rewrite the file
    python3 tools/p5/receipt_cases.py             check that the file is the one this script writes (exit 1 when not)
"""
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "testdata" / "p5" / "materialize.json"
OUT = ROOT / "testdata" / "p5" / "receipt.json"

TOKEN = "ENGINE_IDENTITY_TOKEN"
ENGINE_VERSION = "0.0.0"
# FIXTURE data. Not a release, not a build hash, not a grammar digest of anything: the digests are the SHA-256 of the words written here.
RELEASE = {
    "primitive_release": "fixture/@v0.0.0",
    "primitive_source_commit": "0000000000000000000000000000000000000000",
    "primitive_build_hash": "sha256:" + hashlib.sha256(b"fixture build hash").hexdigest(),
    "grammar_sha256": "sha256:" + hashlib.sha256(b"fixture grammar").hexdigest(),
    "grammar_schema_sha256": "sha256:" + hashlib.sha256(b"fixture grammar schema").hexdigest(),
}


def canon(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
        raw = raw.replace(ch, esc)
    return raw


def digest(value):
    return "sha256:" + hashlib.sha256(canon(value).encode()).hexdigest()


def identity(text):
    def one(key):
        return re.search(r"^    " + key + r": \"?([^\"\n]*)\"?$", text, re.M).group(1)

    return one("namespace") + ":" + one("kind"), one("version")


def receipt_for(text, projection_json, output_digest, identity_token=TOKEN):
    """The expected receipt of ONE accepted case, from the schema text and from the projection and the output digest that the materialization gives (never from the
    library under test): -> (authored_input, document, binding), the first two as canonical text, the binding as {evidence path: field name}. Used for the shared cases
    below and by tools/parity/compare_receipt.py for the generated ones."""
    name, version = identity(text)
    fields = json.loads(projection_json)["fields"]
    authored = {f["key"]: f["value"] for f in fields if f["provenance"] == "authored"}
    defaults = sorted(({"path": "$." + f["key"], "rule": "schema-default", "value_digest": digest(f["value"])}
                       for f in fields if f["provenance"] == "schema_default"), key=lambda d: d["path"].encode())
    receipt = {"materialization": {
        "receipt_schema_version": 1,
        **{k: RELEASE[k] for k in ("grammar_sha256", "grammar_schema_sha256", "primitive_release", "primitive_source_commit", "primitive_build_hash")},
        "schema_name": name,
        "schema_version": version,
        "schema_digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        "engine_identity": identity_token,
        "engine_version": ENGINE_VERSION,
        "input_digest": digest(authored),
        "output_digest": output_digest,
        "applied_defaults": defaults,
        "derived_values": [],
    }}
    return canon(authored), canon(receipt), {d["path"]: d["path"][2:] for d in defaults}


def build():
    src = json.loads(SRC.read_text())
    cases = []
    for c in src["cases"]:
        if "refused" in c["expect"]:
            continue
        authored, document, binding = receipt_for(src["schemas"][c["schema"]], c["expect"]["projection"], c["expect"]["output_digest"])
        cases.append({"id": c["id"], "authored_input": authored, "document": document, "binding": binding})
    return {"engine_identity_token": TOKEN, "engine_version": ENGINE_VERSION, "release": RELEASE, "cases": cases}


def main():
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if "--write" in sys.argv:
        OUT.write_text(text)
        return 0
    if OUT.read_text() != text:
        print("testdata/p5/receipt.json is not what tools/p5/receipt_cases.py writes", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
