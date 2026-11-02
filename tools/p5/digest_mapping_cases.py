#!/usr/bin/env python3
"""Writes testdata/p5/digest-mapping.json, the shared cases of the release record to receipt digest mapping (docs/P5-DIGEST-MAPPING-CONTRACT.md, section 6).

The file is language-neutral: a case is either a CONVERSION (a base64 string, the receipt digest or one reason) or a MAPPING (a small record, a pin, the three receipt digests
or one reason and the field it names). The Rust and the Go implementations read the file and have to give the same answers as tools/p5/digest_mapping.py.

The expected values are NOT computed by the reference. Synthetic ones are written from raw bytes with `bytes.hex()` and the standard base64 encoder; the real ones are the
values of the record of cic-primitives v0.2.1 (release commit 5e8c3219b52399e3e764b0b0bb27de710f5445a4, record SHA-256 e5d8268aede0d24f02e60a151099a2fdc7615172ed3105b0a05b6b5d19addf63)
with their hex written out literally here; the test recomputes those with `openssl base64 -d | od` and compares the two grammar digests with corpus/pins.yaml.

    python3 tools/p5/digest_mapping_cases.py --write     rewrite the file
    python3 tools/p5/digest_mapping_cases.py             check that the file is the one this script writes (exit 1 when not)
"""
import base64
import copy
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "testdata" / "p5" / "digest-mapping.json"

CHECK_GRAMMAR = "proposals/atom-grammar/check_grammar.py"
GRAMMAR_SCHEMA = "proposals/atom-grammar/instance-grammar.schema.yaml"

# the record of cic-primitives v0.2.1: base64 as the record writes it, hex as openssl and od read it
BUILD_B64 = "HVazYxdg737P2bMy2Enkj5ej1VfF+TQ4pDub86G3p2s="
BUILD_HEX = "1d56b3631760ef7ecfd9b332d849e48f97a3d557c5f93438a43b9bf3a1b7a76b"
GRAMMAR_B64 = "7y1qbg9pLA6KksuBMCEZOokEy0Gnq8Dqg4cwsQZyY3U="
GRAMMAR_HEX = "ef2d6a6e0f692c0e8a92cb813021193a8904cb41a7abc0ea838730b106726375"
SCHEMA_B64 = "OvJnzW5/C5MX5iv8uPDR0g73+bJ01wo8h1ON/2T9plc="
SCHEMA_HEX = "3af267cd6e7f0b9317e62bfcb8f0d1d20ef7f9b274d70a3c87538dff64fda657"
LICENSE_HEX = "4fffcdac095c0623fefb8515ab80b3e9629c0febc454456cd08b3cdf3c941720"      # LICENSE.md of the pin, an artifact that is not read by the mapping

REAL_RECORD = {
    "release": {"envelope": 3, "build_hash": BUILD_B64},
    "provenance": {"envelope": 3, "grammar_sha256": GRAMMAR_B64, "grammar_schema_sha256": SCHEMA_B64},
    "artifacts": {"files": [
        {"path": "LICENSE.md", "digest": "sha256:" + LICENSE_HEX},
        {"path": CHECK_GRAMMAR, "digest": "sha256:" + GRAMMAR_HEX},
        {"path": GRAMMAR_SCHEMA, "digest": "sha256:" + SCHEMA_HEX},
    ]},
}
REAL_PIN = {CHECK_GRAMMAR: GRAMMAR_HEX, GRAMMAR_SCHEMA: SCHEMA_HEX}
REAL_EXPECT = {"primitive_build_hash": "sha256:" + BUILD_HEX, "grammar_sha256": "sha256:" + GRAMMAR_HEX, "grammar_schema_sha256": "sha256:" + SCHEMA_HEX}


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


def flip_last_bit(hex_text):
    return hex_text[:-1] + ("1" if hex_text[-1] == "0" else "0")


# ---------------------------------------------------------------- conversion cases

def conversion_cases():
    zeros, ones, pluses = bytes(32), b"\xff" * 32, bytes([0xFB, 0xFF, 0xBF]) * 10 + b"\xfb\xff"
    ok = [("P1-build-hash-of-v0.2.1", BUILD_B64, "sha256:" + BUILD_HEX),
          ("P2-grammar-sha256-of-v0.2.1", GRAMMAR_B64, "sha256:" + GRAMMAR_HEX),
          ("P3-grammar-schema-sha256-of-v0.2.1", SCHEMA_B64, "sha256:" + SCHEMA_HEX),
          ("P4-32-zero-bytes", b64(zeros), "sha256:" + zeros.hex()),
          ("P5-32-bytes-ff", b64(ones), "sha256:" + ones.hex()),
          ("P6-plus-and-slash", b64(pluses), "sha256:" + pluses.hex())]
    cases = [{"id": i, "kind": "convert", "input": v, "expect": e, "refuse": None} for i, v, e in ok]

    def no(id, value, reason):
        cases.append({"id": id, "kind": "convert", "input": value, "expect": None, "refuse": reason})

    last_char_index = len(BUILD_B64) - 2                      # the character in front of the padding
    no("N1-non-canonical-spelling-of-the-same-32-bytes", BUILD_B64[:last_char_index] + "t=", "base64_not_canonical")
    no("N1b-non-canonical-spelling-of-zeros", "A" * 42 + "B=", "base64_not_canonical")
    no("N2-trailing-newline", BUILD_B64 + "\n", "base64_shape")
    no("N2b-trailing-carriage-return-and-newline", BUILD_B64 + "\r\n", "base64_shape")
    no("N3a-leading-space", " " + BUILD_B64, "base64_shape")
    no("N3b-embedded-newline", BUILD_B64[:10] + "\n" + BUILD_B64[10:], "base64_shape")
    no("N3c-trailing-space", BUILD_B64 + " ", "base64_shape")
    no("N4a-url-safe-minus", BUILD_B64.replace("+", "-"), "base64_shape")
    no("N4b-url-safe-underscore", b64(ones).replace("/", "_"), "base64_shape")
    no("N5a-no-padding", BUILD_B64[:-1], "base64_shape")
    no("N5b-double-padding", BUILD_B64[:-1] + "=" + "=", "base64_shape")
    no("N5c-padding-in-the-middle", BUILD_B64[:20] + "=" + BUILD_B64[21:], "base64_shape")
    no("N6a-31-bytes", b64(bytes(31)), "base64_shape")
    no("N6b-33-bytes", b64(bytes(33)), "base64_shape")
    no("N6c-64-bytes", b64(bytes(64)), "base64_shape")
    no("N6d-empty-string", "", "base64_shape")
    no("N6e-a-hex-string-of-64-digits", BUILD_HEX, "base64_shape")
    no("N6f-already-in-receipt-form", "sha256:" + BUILD_HEX, "base64_shape")
    no("N7a-a-non-ascii-character", BUILD_B64[:5] + chr(0xE9) + BUILD_B64[6:], "base64_shape")
    no("N7b-a-nul-character", BUILD_B64[:5] + chr(0) + BUILD_B64[6:], "base64_shape")
    no("N8a-an-integer", 5, "field_type")
    no("N8b-null", None, "field_type")
    no("N8c-a-list", [BUILD_B64], "field_type")
    no("N8d-a-boolean", True, "field_type")
    return cases


# ---------------------------------------------------------------- mapping cases

def mutate(record, *ops):
    out = copy.deepcopy(record)
    for op in ops:
        kind, path = op[0], op[1].split(".")
        parent = out
        for part in path[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        last = int(path[-1]) if isinstance(parent, list) else path[-1]
        if kind == "set":
            parent[last] = op[2]
        elif kind == "delete":
            del parent[last]
        elif kind == "append":
            parent[last].append(op[2])
        else:
            raise ValueError(kind)
    return out


def mapping_cases():
    cases = []

    def case(id, record, pin=None, expect=None, refuse=None, field=None):
        cases.append({"id": id, "kind": "map", "record": record, "pin": REAL_PIN if pin is None else pin, "expect": expect, "refuse": refuse, "field": field})

    case("R1-the-record-of-v0.2.1", REAL_RECORD, expect=REAL_EXPECT)

    zeros, ones = bytes(32), b"\xff" * 32
    synthetic = {"release": {"envelope": 3, "build_hash": b64(zeros)},
                 "provenance": {"envelope": 3, "grammar_sha256": b64(ones), "grammar_schema_sha256": b64(bytes(range(32)))},
                 "artifacts": {"files": [{"path": CHECK_GRAMMAR, "digest": "sha256:" + ones.hex()},
                                         {"path": GRAMMAR_SCHEMA, "digest": "sha256:" + bytes(range(32)).hex()}]}}
    case("R2-a-synthetic-record", synthetic, pin={CHECK_GRAMMAR: ones.hex(), GRAMMAR_SCHEMA: bytes(range(32)).hex()},
         expect={"primitive_build_hash": "sha256:" + zeros.hex(), "grammar_sha256": "sha256:" + ones.hex(), "grammar_schema_sha256": "sha256:" + bytes(range(32)).hex()})
    case("R3-more-artifacts-and-members-are-ignored", mutate(REAL_RECORD, ("append", "artifacts.files", {"path": "schemas/atomic/role.yaml", "digest": "sha256:" + "0" * 64}),
                                                             ("set", "kind", "PrimitiveRelease"), ("set", "release.sign", "vault:v1:x")), expect=REAL_EXPECT)
    case("R4-a-forged-signature-is-not-this-functions-business", mutate(REAL_RECORD, ("set", "release.sign", "vault:v1:forged"),
                                                                        ("set", "cic_countersign", {"sign": "vault:v1:forged", "signed_payload": "build_hash"})), expect=REAL_EXPECT)

    # envelope
    for id, ops in (("E1-release-envelope-2", [("set", "release.envelope", 2)]), ("E2-release-envelope-1", [("set", "release.envelope", 1)]),
                    ("E3-release-envelope-as-a-string", [("set", "release.envelope", "3")]), ("E4-release-envelope-as-a-float", [("set", "release.envelope", 3.0)]),
                    ("E5-release-envelope-true", [("set", "release.envelope", True)]), ("E6-release-envelope-absent", [("delete", "release.envelope")]),
                    ("E7-release-envelope-4", [("set", "release.envelope", 4)]), ("E8-provenance-envelope-2", [("set", "provenance.envelope", 2)]),
                    ("E9-provenance-envelope-absent", [("delete", "provenance.envelope")]), ("E10-provenance-absent", [("delete", "provenance")]),
                    ("E11-release-absent", [("delete", "release")])):
        field = "provenance.envelope" if id.startswith(("E8", "E9", "E10")) else "release.envelope"
        case(id, mutate(REAL_RECORD, *ops), refuse="envelope_not_3", field=field)
    case("E12-the-envelope-is-judged-before-anything-else", mutate(REAL_RECORD, ("set", "release.envelope", 2), ("delete", "release.build_hash")), refuse="envelope_not_3", field="release.envelope")

    # primitive_build_hash
    case("B1-build-hash-absent", mutate(REAL_RECORD, ("delete", "release.build_hash")), refuse="field_missing", field="release.build_hash")
    case("B2-build-hash-null", mutate(REAL_RECORD, ("set", "release.build_hash", None)), refuse="field_missing", field="release.build_hash")
    case("B3-build-hash-an-integer", mutate(REAL_RECORD, ("set", "release.build_hash", 7)), refuse="field_type", field="release.build_hash")
    case("B4-build-hash-non-canonical", mutate(REAL_RECORD, ("set", "release.build_hash", BUILD_B64[:-2] + "t=")), refuse="base64_not_canonical", field="release.build_hash")
    case("B5-build-hash-with-a-trailing-newline", mutate(REAL_RECORD, ("set", "release.build_hash", BUILD_B64 + "\n")), refuse="base64_shape", field="release.build_hash")
    case("B6-build-hash-already-hex", mutate(REAL_RECORD, ("set", "release.build_hash", BUILD_HEX)), refuse="base64_shape", field="release.build_hash")

    # artifacts
    case("A1-artifacts-absent", mutate(REAL_RECORD, ("delete", "artifacts")), refuse="field_missing", field="artifacts.files")
    case("A2-artifacts-files-absent", mutate(REAL_RECORD, ("delete", "artifacts.files")), refuse="field_missing", field="artifacts.files")
    case("A3-artifacts-files-not-a-list", mutate(REAL_RECORD, ("set", "artifacts.files", {"a": 1})), refuse="field_type", field="artifacts.files")
    case("A4-check-grammar-absent", mutate(REAL_RECORD, ("delete", "artifacts.files.1")), refuse="artifact_absent", field=CHECK_GRAMMAR)
    case("A5-schema-absent", mutate(REAL_RECORD, ("delete", "artifacts.files.2")), refuse="artifact_absent", field=GRAMMAR_SCHEMA)
    case("A6-check-grammar-listed-twice-both-right", mutate(REAL_RECORD, ("append", "artifacts.files", {"path": CHECK_GRAMMAR, "digest": "sha256:" + GRAMMAR_HEX})),
         refuse="artifact_duplicate", field=CHECK_GRAMMAR)
    case("A7-a-path-spelled-differently-is-not-the-path", mutate(REAL_RECORD, ("set", "artifacts.files.1.path", "./" + CHECK_GRAMMAR)), refuse="artifact_absent", field=CHECK_GRAMMAR)
    for id, value in (("D1-digest-uppercase", "sha256:" + GRAMMAR_HEX.upper()), ("D2-digest-sha512", "sha512:" + GRAMMAR_HEX), ("D3-digest-63-digits", "sha256:" + GRAMMAR_HEX[:-1]),
                      ("D4-digest-65-digits", "sha256:" + GRAMMAR_HEX + "0"), ("D5-digest-no-prefix", GRAMMAR_HEX), ("D6-digest-trailing-newline", "sha256:" + GRAMMAR_HEX + "\n"),
                      ("D7-digest-in-base64", GRAMMAR_B64), ("D8-digest-empty", "")):
        case(id, mutate(REAL_RECORD, ("set", "artifacts.files.1.digest", value)), refuse="digest_form", field="artifacts.files[%s].digest" % CHECK_GRAMMAR)
    case("D9-digest-an-integer", mutate(REAL_RECORD, ("set", "artifacts.files.1.digest", 5)), refuse="field_type", field="artifacts.files[%s].digest" % CHECK_GRAMMAR)
    case("D10-digest-absent", mutate(REAL_RECORD, ("delete", "artifacts.files.1.digest")), refuse="field_type", field="artifacts.files[%s].digest" % CHECK_GRAMMAR)

    # provenance, the mandatory cross-check
    case("V1-provenance-grammar-absent", mutate(REAL_RECORD, ("delete", "provenance.grammar_sha256")), refuse="field_missing", field="provenance.grammar_sha256")
    case("V2-provenance-schema-absent", mutate(REAL_RECORD, ("delete", "provenance.grammar_schema_sha256")), refuse="field_missing", field="provenance.grammar_schema_sha256")
    case("V3-provenance-grammar-an-integer", mutate(REAL_RECORD, ("set", "provenance.grammar_sha256", 1)), refuse="field_type", field="provenance.grammar_sha256")
    case("V4-provenance-grammar-non-canonical", mutate(REAL_RECORD, ("set", "provenance.grammar_sha256", GRAMMAR_B64[:-2] + "V=")), refuse="base64_not_canonical", field="provenance.grammar_sha256")
    case("V5-provenance-grammar-with-a-trailing-newline", mutate(REAL_RECORD, ("set", "provenance.grammar_sha256", GRAMMAR_B64 + "\n")), refuse="base64_shape", field="provenance.grammar_sha256")
    flipped = bytes.fromhex(GRAMMAR_HEX)
    flipped = flipped[:-1] + bytes([flipped[-1] ^ 1])
    case("V6-provenance-differs-from-the-artifact-by-one-bit", mutate(REAL_RECORD, ("set", "provenance.grammar_sha256", b64(flipped))),
         refuse="provenance_artifacts_disagree", field="grammar_sha256")
    flipped_schema = bytes.fromhex(SCHEMA_HEX)
    flipped_schema = flipped_schema[:-1] + bytes([flipped_schema[-1] ^ 1])
    case("V7-provenance-schema-differs-by-one-bit", mutate(REAL_RECORD, ("set", "provenance.grammar_schema_sha256", b64(flipped_schema))),
         refuse="provenance_artifacts_disagree", field="grammar_schema_sha256")

    # the pin
    case("K1-artifact-differs-from-the-pin-by-one-bit", REAL_RECORD, pin={CHECK_GRAMMAR: flip_last_bit(GRAMMAR_HEX), GRAMMAR_SCHEMA: SCHEMA_HEX}, refuse="record_pin_disagree", field="grammar_sha256")
    case("K2-schema-differs-from-the-pin-by-one-bit", REAL_RECORD, pin={CHECK_GRAMMAR: GRAMMAR_HEX, GRAMMAR_SCHEMA: flip_last_bit(SCHEMA_HEX)}, refuse="record_pin_disagree", field="grammar_schema_sha256")
    case("K3-the-pin-has-no-entry-for-the-path", REAL_RECORD, pin={GRAMMAR_SCHEMA: SCHEMA_HEX}, refuse="field_missing", field="pin." + CHECK_GRAMMAR)
    case("K4-the-pin-digest-with-the-prefix-is-not-the-pin-form", REAL_RECORD, pin={CHECK_GRAMMAR: "sha256:" + GRAMMAR_HEX, GRAMMAR_SCHEMA: SCHEMA_HEX}, refuse="record_pin_disagree", field="grammar_sha256")

    # the order of the checks: the record is judged top to bottom, one reason each
    both = mutate(REAL_RECORD, ("set", "provenance.grammar_sha256", b64(flipped)))
    case("O1-provenance-is-judged-before-the-pin", both, pin={CHECK_GRAMMAR: flip_last_bit(GRAMMAR_HEX), GRAMMAR_SCHEMA: SCHEMA_HEX}, refuse="provenance_artifacts_disagree", field="grammar_sha256")
    case("O2-grammar-sha256-is-judged-before-the-schema", mutate(REAL_RECORD, ("set", "provenance.grammar_sha256", b64(flipped)), ("set", "provenance.grammar_schema_sha256", b64(flipped_schema))),
         refuse="provenance_artifacts_disagree", field="grammar_sha256")
    case("O3-the-build-hash-is-judged-before-the-artifacts", mutate(REAL_RECORD, ("set", "release.build_hash", 7), ("delete", "artifacts")), refuse="field_type", field="release.build_hash")
    return cases


def document():
    return {
        "about": "docs/P5-DIGEST-MAPPING-CONTRACT.md, section 6. A conversion case: the base64 value of a record member and the receipt digest, or one reason. A map case: a record "
                 "(only the members the mapping reads), a pin ({path: 64 lowercase hex}) and the three receipt digests, or one reason and the field it names. Written by "
                 "tools/p5/digest_mapping_cases.py; read by the reference tools/p5/digest_mapping.py and by the Rust and Go implementations.",
        "reasons": ["envelope_not_3", "field_missing", "field_type", "base64_shape", "base64_not_canonical", "base64_length", "digest_form",
                    "artifact_absent", "artifact_duplicate", "provenance_artifacts_disagree", "record_pin_disagree"],
        "cases": conversion_cases() + mapping_cases(),
    }


def render():
    return json.dumps(document(), indent=1, sort_keys=False) + "\n"


def main(argv):
    text = render()
    if "--write" in argv:
        OUT.write_text(text, encoding="utf-8")
        print("wrote %s (%d cases)" % (OUT.relative_to(ROOT), len(document()["cases"])))
        return 0
    if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
        print("%s is not the file this script writes; run it with --write" % OUT.relative_to(ROOT))
        return 1
    print("%s is the file this script writes (%d cases)" % (OUT.relative_to(ROOT), len(document()["cases"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
