#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare of the digest mapping differential (docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md, sections 4 to 6).

    compare_digest_mapping.py INPUT --python DUMP --go DUMP --rust DUMP [--known FILE] [--floors FILE] [--json OUT]

Two verdicts per case: the three implementations AGREE, and the common answer is the one fixed BY CONSTRUCTION in the input (`expect`, or `refuse` and `field`). Green needs both.
Everything the contract lists as fail-closed makes the run red (exit 1): unknown / missing / duplicate / reordered ids, a line that is not in the vocabulary, a thin class, a known
difference that no longer occurs or occurs with another answer. Nothing is a warning. The input file is the reference for "missing": a case absent from all three dumps is red.
"""
import argparse
import collections
import hashlib
import json
import re
import sys

IMPLS = ("python", "go", "rust")
REASONS = {"envelope_not_3", "field_missing", "field_type", "base64_shape", "base64_not_canonical", "base64_length", "digest_form", "artifact_absent", "artifact_duplicate",
           "provenance_artifacts_disagree", "record_pin_disagree"}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAP_KEYS = ("primitive_build_hash", "grammar_sha256", "grammar_schema_sha256")


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_lines(path, errors, what):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f.read().split("\n")[:-1], 1):
            try:
                rows.append(json.loads(line))
            except ValueError:
                errors.append("%s: line %d is not JSON" % (what, n))
                rows.append(None)
    return rows


def normalize(row, case, errors, what):
    """-> a hashable answer, or None after recording why the line is outside the contract."""
    if not isinstance(row, dict) or "id" not in row or "ok" not in row or not isinstance(row["ok"], bool):
        errors.append("%s: a line lacks id/ok (%r)" % (what, row))
        return None
    if row["ok"]:
        v = row.get("value")
        if case["kind"] == "convert":
            if not (isinstance(v, str) and DIGEST.fullmatch(v)):
                errors.append("%s: %s: the value is not a receipt digest" % (what, row["id"]))
                return None
            return ("ok", v)
        if not (isinstance(v, dict) and set(v) == set(MAP_KEYS) and all(isinstance(x, str) and DIGEST.fullmatch(x) for x in v.values())):
            errors.append("%s: %s: the value is not the three receipt digests" % (what, row["id"]))
            return None
        return ("ok", tuple(sorted(v.items())))
    reason = row.get("reason")
    if reason == "CRASH":
        return ("crash", row.get("detail"))
    if reason not in REASONS:
        errors.append("%s: %s: reason %r is not in the contract" % (what, row["id"], reason))
        return None
    field = row.get("field")
    if field is not None and not isinstance(field, str):
        errors.append("%s: %s: field is not a string or null" % (what, row["id"]))
        return None
    return ("refuse", reason, field or None)


def construction_ok(case, ans):
    if case["refuse"] is None:
        want = case["expect"]
        want = ("ok", want) if case["kind"] == "convert" else ("ok", tuple(sorted(want.items())))
        return ans == want
    if ans[0] != "refuse" or ans[1] != case["refuse"]:
        return False
    return case.get("field") is None or ans[2] == case["field"]


def describe(ans):
    return None if ans is None else list(ans)


def compare(input_path, dumps, known=None, floors=None):
    """-> a report dict; report["red"] is False only when every rule of the contract holds."""
    errors = []
    cases = []
    ids = set()
    with open(input_path, encoding="utf-8") as f:
        text = f.read()
    if text and not text.endswith("\n"):
        errors.append("input: the last line has no newline")
    for n, line in enumerate(text.split("\n")[:-1], 1):
        try:
            c = json.loads(line)
        except ValueError:
            errors.append("input: line %d is not JSON" % n)
            continue
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or c.get("kind") not in ("convert", "map") or "refuse" not in c:
            errors.append("input: line %d is not a case" % n)
            continue
        if c["id"] in ids:
            errors.append("input: duplicate id %s" % c["id"])
        ids.add(c["id"])
        cases.append(c)
    by_id = {c["id"]: c for c in cases}
    answers = {}
    for impl in IMPLS:
        rows = read_lines(dumps[impl], errors, impl)
        got = [r["id"] if isinstance(r, dict) and "id" in r else None for r in rows]
        if got != [c["id"] for c in cases]:
            seen = collections.Counter(g for g in got if g is not None)
            for g in seen:
                if g not in by_id:
                    errors.append("%s: unknown id %s" % (impl, g))
                if seen[g] > 1:
                    errors.append("%s: duplicate id %s" % (impl, g))
            for c in cases:
                if c["id"] not in seen:
                    errors.append("%s: missing id %s" % (impl, c["id"]))
            if not any(e.startswith(impl + ": ") and ("unknown" in e or "duplicate" in e or "missing" in e) for e in errors):
                errors.append("%s: the ids are not in the order of the input" % impl)
            if len(got) != len(cases):
                errors.append("%s: %d lines for %d cases" % (impl, len(got), len(cases)))
        answers[impl] = {}
        for r in rows:
            if isinstance(r, dict) and r.get("id") in by_id and r["id"] not in answers[impl]:
                answers[impl][r["id"]] = normalize(r, by_id[r["id"]], errors, impl)
    known = {k["id"]: k for k in (known or {}).get("entries", [])}
    used = set()
    classes = collections.defaultdict(lambda: collections.Counter())
    red = []
    for c in cases:
        a = {impl: answers[impl].get(c["id"]) for impl in IMPLS}
        cls = c.get("class", "shared").split(":")[0]
        classes[cls]["cases"] += 1
        if any(v is None for v in a.values()):
            classes[cls]["red"] += 1
            red.append({"id": c["id"], "why": "an answer is missing or outside the contract", "answers": {k: describe(v) for k, v in a.items()}})
            continue
        agree = len(set(a.values())) == 1
        constructed = {impl: construction_ok(c, a[impl]) for impl in IMPLS}
        if c["id"] in known:
            entry = known[c["id"]]
            same = {impl: describe(a[impl]) == entry["answers"].get(impl) for impl in IMPLS}
            if all(same.values()):
                used.add(c["id"])
                classes[cls]["known_difference"] += 1
                continue
            red.append({"id": c["id"], "why": "a known difference with another answer", "answers": {k: describe(v) for k, v in a.items()}})
            classes[cls]["red"] += 1
            used.add(c["id"])
            continue
        if agree and all(constructed.values()):
            classes[cls]["green"] += 1
            if c["refuse"] is not None and c.get("field") is None:
                classes[cls]["reason_only"] += 1
            continue
        classes[cls]["red"] += 1
        red.append({"id": c["id"], "why": ("the implementations disagree" if not agree else "") + ("; " if not agree and not all(constructed.values()) else "") +
                    ("" if all(constructed.values()) else "disagree with the construction: " + ",".join(i for i in IMPLS if not constructed[i])),
                    "answers": {k: describe(v) for k, v in a.items()}})
    for kid in known:
        if kid not in used:
            errors.append("known difference %s no longer occurs" % kid)
    for cls, floor in (floors or {}).items():
        if classes[cls]["cases"] < floor:
            errors.append("thin run: class %s has %d cases, the floor is %d" % (cls, classes[cls]["cases"], floor))
    return {
        "cases": len(cases),
        "green": sum(v["green"] for v in classes.values()),
        "red_cases": len(red),
        "known_differences": sum(v["known_difference"] for v in classes.values()),
        "classes": {k: dict(v) for k, v in sorted(classes.items())},
        "red": red,
        "errors": errors,
        "is_red": bool(errors or red),
    }


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    for impl in IMPLS:
        ap.add_argument("--" + impl, required=True)
    ap.add_argument("--known")
    ap.add_argument("--floors")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    known = json.load(open(a.known, encoding="utf-8")) if a.known else None
    floors = json.load(open(a.floors, encoding="utf-8")) if a.floors else None
    rep = compare(a.input, {i: getattr(a, i) for i in IMPLS}, known, floors)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, sort_keys=True)
            f.write("\n")
    print("cases %d, green %d, red %d, known differences %d, errors %d" % (rep["cases"], rep["green"], rep["red_cases"], rep["known_differences"], len(rep["errors"])))
    for e in rep["errors"][:20]:
        print("ERROR", e)
    for r in rep["red"][:20]:
        print("RED", r["id"], r["why"], r["answers"])
    return 1 if rep["is_red"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
