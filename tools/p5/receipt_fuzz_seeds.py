#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Writes the shared inputs of the receipt fuzz targets (P5-3, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 19), all of them DERIVED from the files the receipt already has:

    testdata/p5/receipt-fuzz/schema.yaml   the schema text of the first accepted case of testdata/p5/materialize.json (the one `receipt_flat` is proved on)
    testdata/p5/receipt-fuzz/fixture.txt   tab-separated lines: the grammar and the access keys of testdata/p1/defaults.json, the fixture release and the engine version of
                                           testdata/p5/receipt.json (the data tools/p5/receipt_cases.py writes), the capability map of that first case
    testdata/p5/receipt-fuzz/seeds.json    the seeds: {"name", "flags", "kind", "doc"}
    rust/fuzz/corpus/receipt/<name>        the same seeds as bytes: flags, kind, the document (the Go fuzz target reads seeds.json; one list, two formats)

An input of the fuzz targets is: byte 0 the FLAGS, byte 1 the KIND, the rest the document (YAML text). FLAGS (bit set = the fault is made):
    1  the schema binding: the claimed digest is not that of the bytes (the KIND says how)
    2  the schema has no `version`
    4  the build hash of the fixture release is not a digest
    8  the engine version is empty
    16 the capability map is not the complete one (the KIND says how)
    32 the parsed document repeats its first key (a node a caller built; the parser never produces it)
    64 the field `label` of the schema is named `la.bel` (and so in the capability map): a name that is not one plain path segment
KIND: byte % 3 is the binding fault (0 the last hex character of the claim is changed, 1 the bytes get a comment line after the claim was taken, 2 the claim is empty),
(byte // 3) % 3 the capability fault (0 `mtu` removed, 1 the value of `label` misspelled, 2 an undeclared key added),
(byte // 9) % 3 the fault of the build hash (0 `sha256:ABC`, 1 a digest whose LAST hex character is `g`, 2 a digest whose FIRST hex character is `G`).

    python3 tools/p5/receipt_fuzz_seeds.py --write     rewrite the files
    python3 tools/p5/receipt_fuzz_seeds.py             check that they are what this script writes (exit 1 when not)
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import receipt_cases as rc  # noqa: E402

BIND, NO_VERSION, BAD_HASH, NO_ENGINE_VERSION, CAPS, DUPLICATE, DOTTED = 1, 2, 4, 8, 16, 32, 64

# name, flags, kind, document. The names sort in the order of the list: the ids of the engine replay are the positions.
SEEDS = [
    # accepted: four applied defaults, none, one, two
    ("s00_defaults_4", 0, 0, "type: t\n"),
    ("s01_defaults_0", 0, 0, 'type: t\nenabled: false\nmtu: 0\nlabel: ""\nmaybe: ~\n'),
    ("s02_defaults_1", 0, 0, "type: t\nenabled: false\nmtu: 5\nlabel: x\n"),
    ("s03_defaults_2", 0, 0, "type: t\nenabled: false\nmtu: 5\n"),
    ("s04_escaping", 0, 0, 'type: "a<b>&c\\u2028\\td"\nlabel: "é/ü"\nmtu: 9223372036854775807\n'),
    # the document is refused by the materialization (the receipt is not made)
    ("s10_unknown_field", 0, 0, "type: t\nzzz: 1\n"),
    ("s11_mandatory_missing", 0, 0, "mtu: 1\n"),
    ("s12_wrong_scalar_type", 0, 0, "type: 1\n"),
    ("s13_nested_value", 0, 0, "type: t\nmtu: [1]\n"),
    ("s14_empty_mapping", 0, 0, "{}\n"),
    # the schema binding: the claim is not the digest of the bytes
    ("s20_claim_changed", BIND, 0, "type: t\n"),
    ("s21_bytes_edited_after_the_claim", BIND, 1, "type: t\n"),
    ("s22_claim_empty", BIND, 2, "type: t\n"),
    ("s23_binding_before_a_bad_document", BIND, 0, "zzz: 1\n"),
    # the receipt's own refusals, in the order of the checks
    ("s30_no_version", NO_VERSION, 0, "type: t\n"),
    ("s31_bad_build_hash", BAD_HASH, 0, "type: t\n"),
    ("s31b_build_hash_last_character", BAD_HASH, 9, "type: t\n"),
    ("s31c_build_hash_first_character", BAD_HASH, 18, "type: t\n"),
    ("s32_empty_engine_version", NO_ENGINE_VERSION, 0, "type: t\n"),
    ("s33_binding_wins_over_the_version", BIND | NO_VERSION, 0, "type: t\n"),
    ("s34_version_wins_over_the_hash", NO_VERSION | BAD_HASH, 0, "type: t\n"),
    ("s35_hash_wins_over_the_engine", BAD_HASH | NO_ENGINE_VERSION, 0, "type: t\n"),
    ("s36_engine_wins_over_the_document", NO_ENGINE_VERSION, 0, "zzz: 1\n"),
    # the capability map
    ("s40_capability_missing", CAPS, 0, "type: t\n"),
    ("s41_capability_unknown", CAPS, 3, "type: t\n"),
    ("s42_capability_undeclared", CAPS, 6, "type: t\n"),
    # a field name that is not one plain path segment: the receipt refuses it when the materialization has accepted the document
    ("s45_dotted_name", DOTTED, 0, "type: t\n"),
    ("s46_dotted_name_after_a_refused_document", DOTTED, 0, "zzz: 1\n"),
    # a repeated key in a node a caller built
    ("s50_repeated_key", DUPLICATE, 0, "type: t\nmtu: 1\n"),
    ("s51_repeated_key_in_a_refused_document", DUPLICATE, 0, "zzz: 1\n"),
    ("s52_nothing_to_repeat", DUPLICATE, 0, "{}\n"),
]


def build():
    m = json.loads((ROOT / "testdata" / "p5" / "materialize.json").read_text())
    c = m["cases"][0]
    defaults = json.loads((ROOT / "testdata" / "p1" / "defaults.json").read_text())
    fixture = [("grammar", defaults["grammar"]), ("access_keys", ",".join(defaults["access_keys"]))]
    fixture += [(k, rc.RELEASE[k]) for k in ("primitive_release", "primitive_source_commit", "primitive_build_hash", "grammar_sha256", "grammar_schema_sha256")]
    fixture.append(("engine_version", rc.ENGINE_VERSION))
    fixture += [("cap:" + k, v) for k, v in c["capabilities"].items()]
    files = {
        "testdata/p5/receipt-fuzz/schema.yaml": m["schemas"][c["schema"]],
        "testdata/p5/receipt-fuzz/fixture.txt": "".join("%s\t%s\n" % kv for kv in fixture),
        "testdata/p5/receipt-fuzz/seeds.json": json.dumps([{"name": n, "flags": f, "kind": k, "doc": d} for n, f, k, d in SEEDS], indent=1, ensure_ascii=False) + "\n",
    }
    raw = {"rust/fuzz/corpus/receipt/" + n: bytes([f, k]) + d.encode() for n, f, k, d in SEEDS}
    return files, raw


def main():
    files, raw = build()
    assert [s[0] for s in SEEDS] == sorted(s[0] for s in SEEDS), "the names must sort in the order of the list"
    if "--write" in sys.argv:
        for p, t in files.items():
            (ROOT / p).write_text(t, encoding="utf-8")
        for p, b in raw.items():
            (ROOT / p).write_bytes(b)
        return 0
    bad = [p for p, t in files.items() if not (ROOT / p).exists() or (ROOT / p).read_text(encoding="utf-8") != t]
    bad += [p for p, b in raw.items() if not (ROOT / p).exists() or (ROOT / p).read_bytes() != b]
    corpus = {p.name for p in (ROOT / "rust/fuzz/corpus/receipt").glob("*")}
    bad += ["rust/fuzz/corpus/receipt/" + x for x in sorted(corpus - {p.split("/")[-1] for p in raw})]
    for p in bad:
        print("%s is not what tools/p5/receipt_fuzz_seeds.py writes" % p, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
