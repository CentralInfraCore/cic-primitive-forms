#!/usr/bin/env python3
"""The reference of the release record to receipt digest mapping (docs/P5-DIGEST-MAPPING-CONTRACT.md).

A pure function: it reads a record that somebody else has already authenticated and a pin, and returns the three receipt digests or refuses with one named reason. It opens
no file, runs no command, verifies no signature and decides nothing about the release being genuine (the contract, section 7). It is the yardstick that the Rust and the Go
implementations have to meet on testdata/p5/digest-mapping.json; it is not itself a Forms API.

    map_release(record, pin) -> {"primitive_build_hash": ..., "grammar_sha256": ..., "grammar_schema_sha256": ...}     or raises Refusal(reason, field)
    to_receipt_digest(value)  -> "sha256:" + 64 lowercase hex                                                         or raises Refusal
"""
import base64
import re

CHECK_GRAMMAR = "proposals/atom-grammar/check_grammar.py"
GRAMMAR_SCHEMA = "proposals/atom-grammar/instance-grammar.schema.yaml"
# receipt field -> (path of the artifact, name of the provenance member that has to agree with it)
GRAMMAR_FIELDS = (
    ("grammar_sha256", CHECK_GRAMMAR, "grammar_sha256"),
    ("grammar_schema_sha256", GRAMMAR_SCHEMA, "grammar_schema_sha256"),
)
# the whole string, nothing around it: 43 standard-alphabet characters and one padding character. The check is a FULL match (no end anchor that lets a final newline through).
BASE64_32_BYTES = re.compile(r"[A-Za-z0-9+/]{43}=")
RECEIPT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

REASONS = (
    "envelope_not_3", "field_missing", "field_type", "base64_shape", "base64_not_canonical", "base64_length", "digest_form",
    "artifact_absent", "artifact_duplicate", "provenance_artifacts_disagree", "record_pin_disagree",
)


class Refusal(Exception):
    def __init__(self, reason, field=""):
        assert reason in REASONS, reason
        super().__init__("%s%s" % (reason, (": " + field) if field else ""))
        self.reason = reason
        self.field = field


def _get(mapping, path):
    """-> the value at a dotted path, or the marker MISSING when a member on the way is absent or the parent is not a mapping."""
    cur = mapping
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return MISSING
        cur = cur[part]
    return cur


MISSING = object()


def _text(record, path):
    value = _get(record, path)
    if value is MISSING or value is None:
        raise Refusal("field_missing", path)
    if not isinstance(value, str):
        raise Refusal("field_type", path)
    return value


def to_receipt_digest(value, field=""):
    """Section 3 of the contract: a base64 SHA-256 of exactly 32 bytes, in its one canonical spelling, as `sha256:` and lowercase hex."""
    if not isinstance(value, str):
        raise Refusal("field_type", field)
    if not BASE64_32_BYTES.fullmatch(value):
        raise Refusal("base64_shape", field)
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise Refusal("base64_length", field)
    if base64.b64encode(raw).decode("ascii") != value:
        raise Refusal("base64_not_canonical", field)
    return "sha256:" + raw.hex()


def _artifact_digest(files, path):
    """The `sha256:<64 hex>` digest of the one artifact entry of `path`, as is."""
    entries = [e for e in files if isinstance(e, dict) and e.get("path") == path]
    if not entries:
        raise Refusal("artifact_absent", path)
    if len(entries) > 1:
        raise Refusal("artifact_duplicate", path)
    digest = entries[0].get("digest")
    if not isinstance(digest, str):
        raise Refusal("field_type", "artifacts.files[%s].digest" % path)
    if not RECEIPT_DIGEST.fullmatch(digest):
        raise Refusal("digest_form", "artifacts.files[%s].digest" % path)
    return digest


def map_release(record, pin):
    """`pin` is {path: 64 lowercase hex} (the digests of the manifest of corpus/pins.yaml, without the `sha256:` prefix)."""
    for member in ("release.envelope", "provenance.envelope"):
        value = _get(record, member)
        if value is MISSING or type(value) is not int or value != 3:
            raise Refusal("envelope_not_3", member)

    out = {"primitive_build_hash": to_receipt_digest(_text(record, "release.build_hash"), "release.build_hash")}

    files = _get(record, "artifacts.files")
    if files is MISSING or files is None:
        raise Refusal("field_missing", "artifacts.files")
    if not isinstance(files, list):
        raise Refusal("field_type", "artifacts.files")

    for receipt_field, path, provenance_member in GRAMMAR_FIELDS:
        primary = _artifact_digest(files, path)
        where = "provenance." + provenance_member
        cross = to_receipt_digest(_text(record, where), where)
        if cross != primary:
            raise Refusal("provenance_artifacts_disagree", receipt_field)
        pinned = pin.get(path)
        if not isinstance(pinned, str):
            raise Refusal("field_missing", "pin." + path)
        if "sha256:" + pinned != primary:
            raise Refusal("record_pin_disagree", receipt_field)
        out[receipt_field] = primary
    return out
