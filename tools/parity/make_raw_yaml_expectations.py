#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Writes the two committed expectation files of the raw YAML differential FROM A MEASURED RUN, so that a reviewer can see exactly what was measured and decide (docs/P5-RAW-YAML-UNIT3-CONTRACT.md).

    make_raw_yaml_expectations.py RUN_DIR [--write]

  pinned_subtypes_raw_yaml.json     the pinned Rust/Go divergences whose divergence is in source POSITIONS only (decision D10): `positions_only` (same tree, other positions) and
                                    `refusal_position_only` (the same refusal, another location), each with the ORIGINAL answers of both parsers. Every other pinned input is `semantic` by default.
  known_differences_raw_yaml.json   one entry per CASE ID, with the exact answers of Rust, Go and PyYAML (a hash of PyYAML's tree) and a recorded acceptance. A known difference is never a class.

A changed classification is a change of the contract, not a test exception: this tool is the only way these files are meant to be produced, and the compare goes red when a run no longer matches.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_raw_yaml as cmp_  # noqa: E402

ACCEPTANCE_KNOWN = ("docs/P5-RAW-YAML-UNIT3-CONTRACT.md section 5 (the first known difference: PyYAML takes the last of two equal keys) and decision D2, GO in the review of PR #158 on 2026-11-04")
ACCEPTANCE_D10 = "decision D10, review of PR #160 on 2026-11-05: source positions are not part of P_parity; positions are diagnostic and audit evidence"


def rows(path):
    with open(path, encoding="utf-8") as f:
        return {json.loads(l)["id"]: json.loads(l) for l in f.read().splitlines()}


def main(argv):
    run = argv[0]
    inp = rows(os.path.join(run, "input.jsonl"))
    d = {i: rows(os.path.join(run, "dump-%s.jsonl" % i)) for i in cmp_.IMPLS}
    subtypes, entries = {}, []
    for cid, c in inp.items():
        r, g, p = d["rust"][cid], d["go"][cid], d["python"][cid]
        if c["parse_expect"].get("result") == "pinned_divergence":
            if r["ok"] and g["ok"] and r["tree"] == g["tree"] and r.get("positions") != g.get("positions"):
                subtypes[c["bytes_hex"]] = {"subtype": "positions_only", "decision": ACCEPTANCE_D10, "original": {"rust": r["positions"], "go": g["positions"]}}
            elif (not r["ok"]) and (not g["ok"]) and r["class"] == g["class"] and r.get("code") == g.get("code") and r.get("locations") != g.get("locations"):
                subtypes[c["bytes_hex"]] = {"subtype": "refusal_position_only", "decision": ACCEPTANCE_D10, "original": {"rust": r["locations"], "go": g["locations"]}}
        if c["class"].startswith("dup") and (not r["ok"]) and (not g["ok"]) and p["ok"] and r["class"] == g["class"] == "duplicate_key":
            entries.append({"id": cid, "kind": "python_accepts_forms_refuses", "rust_class": r["class"], "go_class": g["class"], "python_tree_sha256": cmp_.tree_sha(p["tree"]), "acceptance": ACCEPTANCE_KNOWN})
    texts = {"pinned_subtypes_raw_yaml.json": json.dumps(subtypes, indent=1, sort_keys=True) + "\n", "known_differences_raw_yaml.json": json.dumps({"entries": entries}, indent=1, sort_keys=True) + "\n"}
    for name, text in texts.items():
        path = os.path.join(HERE, name)
        if "--write" in argv:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            with open(path, encoding="utf-8") as f:
                if f.read() != text:
                    print("%s differs from what this run measured" % name)
                    return 1
    print("pinned subtypes: %d (%s); known differences: %d" % (len(subtypes), ", ".join(sorted({v["subtype"] for v in subtypes.values()})), len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
