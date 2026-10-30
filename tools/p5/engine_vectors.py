#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The canonical-bytes vectors of P5-1 (testdata/p5/canonical-vectors.tsv) against the pinned cic-primitive-engine checkout.

    engine_vectors.py --engine DIR            check the table against the engine's conformance/canonicalize
    engine_vectors.py --engine DIR --write    write the engine lines of the table from the engine (own lines are kept)
    engine_vectors.py --own                   check the own lines against the independent reference below (no engine needed)

The table is read by rust/tests/canonical_corpus.rs and go/canonical_corpus_test.go. Columns (tab separated, JSON string literals for the text so that a
tab, a quote or a backslash cannot be misread):  source (engine|own) / name / status (in_range|out_of_range) / input (YAML text) / canonical bytes (or - when
out of range) / digest (or -) / why.

The check, in the engine mode: the checkout is at the pinned commit; every engine vector is classified in the table (a new vector there fails here until it is
classified, so the range of this library is never widened or narrowed unseen); every in-range line equals the engine's input and expected bytes; an
out-of-range line names a vector that exists. The own lines are checked against a reference written apart here (json.dumps and the five Go escapes), which is a
third reader of A1-A6 and shares no code with either implementation.
Exit status 1 on any failure.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
TABLE = ROOT / "testdata/p5/canonical-vectors.tsv"
PIN = ROOT / "testdata/p5/engine-pin.txt"

HEADER = [
    "# The canonical bytes of P5-1: one line per vector. Source engine = a vector of cic-primitive-engine conformance/canonicalize, checked against the pinned commit",
    "# (testdata/p5/engine-pin.txt) by tools/p5/engine_vectors.py; source own = a vector of this library, checked against a reference written apart in that tool.",
    "# out_of_range = the engine vector holds a float or an integer beyond 64 bits, which this library's value cannot (docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md, decision G).",
    "# Read by rust/tests/canonical_corpus.rs and go/canonical_corpus_test.go. Columns: source, name, status, input, canonical, digest, why (text as JSON string literals).",
]

IN_RANGE = [
    "control_characters",
    "digest_of_empty_mapping",
    "html_unsafe_characters",
    "line_and_paragraph_separators",
    "nested_structures",
    "object_key_ordering",
    "quoted_numeric_string",
]
OUT_OF_RANGE = {
    "explicit_int_tag_exactness": "an integer beyond 64 bits",
    "float_exact_decimal_tie": "floats",
    "float_extreme_magnitude": "a float",
    "large_integer_beyond_i64": "an integer beyond 64 bits",
    "negative_zero": "a float (-0.0) in the same vector",
    "non_finite_float_rejected": "a float (.inf)",
}

# Own vectors: (name, YAML input, why). The expected bytes come from reference(), never from either implementation.
OWN = [
    ("own_minus_zero_integer", "v: -0\n", "-0 is the integer 0"),
    ("own_i64_extremes", "a: 9223372036854775807\nb: -9223372036854775808\n", "the two ends of the signed 64-bit range, as digits"),
    ("own_scalars", "a: true\nb: false\nc: null\nd: ~\n", "bool and null tokens"),
    ("own_empty_containers", "a: []\nb: {}\nc: ''\n", "empty sequence, empty map, empty string"),
    ("own_sequence_order_kept", "v: [3, 1, 2]\n", "A6: a sequence is written as given"),
    ("own_key_order_is_bytes", "z: 1\nB: 2\na: 3\n\u00e9: 4\n\u00ff: 5\n", "A2: raw UTF-8 byte order, uppercase before lowercase, multibyte after ASCII"),
    ("own_key_order_is_bytes_not_utf16", '"\\U0001f600": 1\n"\\ue000": 2\n', "A2: a character beyond the BMP sorts after a private-use one by UTF-8 bytes, before it by UTF-16 code units"),
    ("own_no_unicode_normalization", "a: \u00e9\nb: e\u0301\n", "A4: precomposed and decomposed stay different bytes"),
    ("own_literal_non_ascii", "v: \u00fc\u20ac\U0001f600\n", "A4: everything else is written literally"),
    ("own_del_and_slash_literal", 'v: "\\x7f/"\n', "DEL and the slash are not escaped"),
    ("own_quote_backslash_backspace_formfeed", 'v: "a\\"b\\\\c\\bd\\fe\\rf\\n"\n', "A4: the short escapes"),
    ("own_nul_and_unit_separator", 'v: "\\0\\x1f"\n', "A4: other control characters as lowercase hex"),
    ("own_key_is_escaped", '"a<b": 1\n', "A4 applies to a key as to a value"),
    ("own_deep_mixture", "a:\n  - b: 1\n    a: [x, {k: v}]\n  - null\n", "nesting of maps in sequences"),
]


def reference(v):
    """Canonical bytes of a plain Python value, written apart from the implementations."""
    s = json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # json.dumps escapes control characters as \u00XX lowercase already, \b \f \n \r \t short; add the five Go escapes.
    s = s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return s


def sorted_by_bytes(v):
    # json.dumps sort_keys sorts by code point; UTF-8 byte order equals code point order, so this agrees with A2.
    return v


def lit(s):
    return json.dumps(s, ensure_ascii=True)


def line(src, name, status, inp, canon, digest, why):
    return "\t".join([src, name, status, lit(inp), "-" if canon is None else lit(canon), digest, lit(why)])


def own_lines():
    out = []
    for name, inp, why in OWN:
        canon = reference(yaml.safe_load(inp))
        out.append(line("own", name, "in_range", inp, canon, "sha256:" + hashlib.sha256(canon.encode()).hexdigest(), why))
    return out


def read_expected(text):
    """expected.yaml of the engine: flat `key: value` lines. Its `why` holds a colon unquoted, which a strict YAML reader refuses (the engine reads it with its own)."""
    out = {}
    for ln in text.splitlines():
        key, _, val = ln.partition(": ")
        if val.startswith("'") and val.endswith("'"):
            val = val[1:-1].replace("''", "'")
        out[key] = val
    return out


def engine_dir_vectors(d):
    res = {}
    for p in sorted((d / "conformance/canonicalize").iterdir()):
        exp = read_expected((p / "expected.yaml").read_text(encoding="utf-8"))
        res[p.name] = ((p / "input.yaml").read_text(encoding="utf-8"), exp)
    return res


def engine_lines(vecs):
    out = []
    for name in IN_RANGE:
        inp, exp = vecs[name]
        canon = exp["canonical"]
        digest = exp.get("digest") or "sha256:" + hashlib.sha256(canon.encode()).hexdigest()
        out.append(line("engine", name, "in_range", inp, canon, digest, exp["why"]))
    for name, reason in OUT_OF_RANGE.items():
        inp, exp = vecs[name]
        out.append(line("engine", name, "out_of_range", inp, None, "-", "out of range: " + reason))
    return out


def read_table():
    rows = []
    for ln in TABLE.read_text(encoding="utf-8").splitlines():
        if ln.startswith("#") or not ln:
            continue
        f = ln.split("\t")
        assert len(f) == 7, ln
        rows.append(dict(src=f[0], name=f[1], status=f[2], inp=json.loads(f[3]), canon=None if f[4] == "-" else json.loads(f[4]), digest=f[5]))
    return rows


def check_own(rows):
    bad = []
    seen = {r["name"] for r in rows if r["src"] == "own"}
    if seen != {n for n, _, _ in OWN}:
        bad.append(f"own lines differ from OWN in this tool: {sorted(seen ^ {n for n, _, _ in OWN})}")
    for r in rows:
        if r["src"] != "own":
            continue
        want = reference(yaml.safe_load(r["inp"]))
        if r["canon"] != want:
            bad.append(f"{r['name']}: table {r['canon']!r} != reference {want!r}")
        if r["digest"] != "sha256:" + hashlib.sha256(want.encode()).hexdigest():
            bad.append(f"{r['name']}: digest")
    return bad


def check_engine(d, rows):
    bad = []
    pin = dict(l.split() for l in PIN.read_text().splitlines() if l and not l.startswith("#"))
    head = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if not head.startswith(pin["commit"]):
        bad.append(f"engine checkout is at {head}, the pin is {pin['commit']}")
    vecs = engine_dir_vectors(d)
    table = {r["name"]: r for r in rows if r["src"] == "engine"}
    if set(table) != set(vecs):
        bad.append(f"engine vectors not classified or gone: {sorted(set(table) ^ set(vecs))}")
    for name, r in table.items():
        if name not in vecs:
            continue
        inp, exp = vecs[name]
        if r["inp"] != inp:
            bad.append(f"{name}: input differs from the engine's")
        if r["status"] == "in_range":
            if exp.get("outcome") != "accepted" or r["canon"] != exp["canonical"]:
                bad.append(f"{name}: canonical bytes differ from the engine's")
            if "digest" in exp and r["digest"] != exp["digest"]:
                bad.append(f"{name}: digest differs from the engine's")
        elif r["status"] != "out_of_range" or r["canon"] is not None:
            bad.append(f"{name}: bad status")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--own", action="store_true")
    a = ap.parse_args()
    if a.write:
        vecs = engine_dir_vectors(pathlib.Path(a.engine))
        TABLE.write_text("\n".join(HEADER + engine_lines(vecs) + own_lines()) + "\n", encoding="utf-8")
        return 0
    rows = read_table()
    bad = check_own(rows)
    if a.engine:
        bad += check_engine(pathlib.Path(a.engine), rows)
    if not any(r["status"] == "in_range" for r in rows):
        bad.append("no in-range vector")
    for b in bad:
        print("FAIL", b)
    print(f"{len(rows)} vectors, {len(bad)} failures")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
