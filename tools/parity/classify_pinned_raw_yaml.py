#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The normative `allowed` classification of the pinned Rust/Go parser divergence inputs (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, sections 2 and 4.3).

    classify_pinned_raw_yaml.py [--write]       writes / checks tools/parity/pinned_classification_raw_yaml.json

Whether a pinned input is in P_allowed is decided by the documented subset (docs/P1-SUPPORTED-SUBSET.md), NEVER by whether the Rust and Go parsers agree on it. The divergence of the two parsers decides
only P_parity and P_effective. The rule per input, applied by the documented-subset reader tools/parity/raw_yaml_subset_ref.py:

  allowed false          a refusal the subset document names (a control character, a directive or document-end line, an anchor, a duplicate key, ...): rule "documented:<what>"
  allowed true           the text parses and none of the documented refusals applies: rule "in-table". A permitted text that two parsers read differently is exactly the case the
                         contract keeps apart from P_parity.
  allowed "undetermined" the reference reader's own library (PyYAML) finds the text invalid and no documented rule names it (rule "yaml-error"), or the refusal is a restriction of
                         the reference reader that the subset document does not name (rule "undocumented:<what>", e.g. a bare scalar as the whole document). The subset document itself lists such
                         spellings as places where the libraries disagree on what YAML 1.2 allows (flow plain scalars with an indicator character, a quoted scalar followed by `#`, ...),
                         so one library's verdict is not normative.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import raw_yaml_subset_ref as ref  # noqa: E402

KNOWN = os.path.join(ROOT, "testdata", "p1", "parity-known.txt")
OUT = os.path.join(HERE, "pinned_classification_raw_yaml.json")


def classify():
    with open(KNOWN, encoding="utf-8") as f:
        lines = f.read().splitlines()
    rows = {}
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        hexs = line.split()[0]
        res, _what, rule = ref.explain(bytes.fromhex(hexs))
        if res == "accept":
            rows[hexs] = {"allowed": True, "rule": "in-table"}
        elif rule == "yaml-error" or rule.startswith("undocumented:"):
            rows[hexs] = {"allowed": "undetermined", "rule": rule}
        else:
            rows[hexs] = {"allowed": False, "rule": rule}
    return rows


def main(argv):
    rows = classify()
    text = json.dumps(rows, indent=1, sort_keys=True) + "\n"
    if "--write" in argv:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        with open(OUT, encoding="utf-8") as f:
            if f.read() != text:
                print("the committed classification differs from the reading of the documented table")
                return 1
    by = {}
    for v in rows.values():
        k = (str(v["allowed"]), v["rule"])
        by[k] = by.get(k, 0) + 1
    for k, n in sorted(by.items()):
        print(k[0].ljust(14), k[1].ljust(44), n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
