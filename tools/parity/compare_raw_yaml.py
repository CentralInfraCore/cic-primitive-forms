#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare of the raw YAML differential (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3b).

    compare_raw_yaml.py INPUT --python DUMP --go DUMP --rust DUMP [--known FILE] [--floors FILE] [--json OUT]

What the three readers answer for the same RAW BYTES, and what the system may therefore admit. Five questions per case, asked separately; none stands in for another:

  parity        P_parity = SEMANTIC parity (decision D10): Rust and Go both accept, with the same tree (structure, scalar kinds, values, order). MEASURED from the two answers, never taken
                from a label. POSITION parity (the same source positions, the same locations of refusals) is measured apart, as diagnostic and audit evidence; it is not part of admission.
  construction  the outcome fixed by construction in the input (`parse_expect`): an accepted tree, a refusal class, nothing (undetermined), or "a pinned divergence".
  allowed       P_effective = P_parity and the case's normative `allowed` label is true. An analysis result, NOT a production admission (D9 is not decided).
  mapping       where both parsers accepted, their mapping answers equal `mapping_expect`.
  python        the oracle's reading against the admitted tree on the members the mapping reads; PyYAML never decides anything.

Everything fail-closed makes the run red (exit 1): unknown / missing / duplicate / reordered ids, a line outside the vocabulary, an unfinished last line, CRASH and TIMEOUT, a thin class,
a pinned divergence that is in P_parity (and a pinned divergence that no longer diverges is named as its own event), a forbidden construct in P_parity, a blocking Python difference that
is not a known difference, a known difference that no longer occurs. A Rust/Go difference where nothing was promised is a FINDING: listed by id, never normalized, not red by itself.
"""
import argparse
import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_digest_mapping as cdm  # noqa: E402

IMPLS = ("python", "go", "rust")
CLASSES = {"syntax", "duplicate_key", "invalid_utf8", "other", "TIMEOUT", "CRASH", "python_error"}
READ_MEMBERS = (("release", "envelope"), ("release", "build_hash"), ("provenance", "envelope"), ("provenance", "grammar_sha256"), ("provenance", "grammar_schema_sha256"), ("artifacts", "files"))


def sha256_file(path):
    return cdm.sha256_file(path)


def tree_sha(tree):
    return hashlib.sha256(json.dumps(tree, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def member(tree, path):
    cur = tree
    for key in path:
        if not isinstance(cur, dict) or cur.get("k") != "map":
            return None
        nxt = [v for k, v in cur["v"] if k == key]
        if not nxt:
            return None
        cur = nxt[-1]
    return cur


def read_dump(path, errors, impl):
    with open(path, encoding="utf-8", newline="") as f:
        lines = cdm.physical_lines(f.read(), errors, impl)
    rows = []
    for n, line in enumerate(lines, 1):
        try:
            rows.append(json.loads(line))
        except ValueError:
            errors.append("%s: line %d is not JSON" % (impl, n))
            rows.append(None)
    return rows


def valid_row(row, impl, errors):
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("ok"), bool):
        errors.append("%s: a line lacks id/ok" % impl)
        return False
    if row["ok"]:
        if "tree" not in row or "mapping" not in row:
            errors.append("%s: %s: an accepted line lacks tree/mapping" % (impl, row["id"]))
            return False
    elif row.get("class") not in CLASSES:
        errors.append("%s: %s: class %r is outside the vocabulary" % (impl, row["id"], row.get("class")))
        return False
    return True


def accepted(row):
    return row["ok"]


def refusal_class(row):
    return row.get("class")


def mapping_matches(got, expect):
    if got is None:
        return False
    if expect["refuse"] is None:
        return "ok" in got and got["ok"] == expect["expect"]
    return got.get("refuse") == expect["refuse"] and got.get("field") == expect["field"]


def compare(input_path, dumps, known=None, floors=None, subtypes=None):
    errors = []
    cases, ids = [], set()
    with open(input_path, encoding="utf-8", newline="") as f:
        text = f.read()
    for n, line in enumerate(cdm.physical_lines(text, errors, "input"), 1):
        try:
            c = json.loads(line)
        except ValueError:
            errors.append("input: line %d is not JSON" % n)
            continue
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or "bytes_hex" not in c or "parse_expect" not in c or "allowed" not in c:
            errors.append("input: line %d is not a case" % n)
            continue
        if c["id"] in ids:
            errors.append("input: duplicate id %s" % c["id"])
        ids.add(c["id"])
        cases.append(c)
    by_id = {c["id"]: c for c in cases}
    answers = {}
    for impl in IMPLS:
        rows = read_dump(dumps[impl], errors, impl)
        got = [r["id"] if isinstance(r, dict) and isinstance(r.get("id"), str) else None for r in rows]
        if got != [c["id"] for c in cases]:
            seen = collections.Counter(g for g in got if g is not None)
            before = len(errors)
            for g in seen:
                if g not in by_id:
                    errors.append("%s: unknown id %s" % (impl, g))
                if seen[g] > 1:
                    errors.append("%s: duplicate id %s" % (impl, g))
            for c in cases:
                if c["id"] not in seen:
                    errors.append("%s: missing id %s" % (impl, c["id"]))
            if len(errors) == before:
                errors.append("%s: the ids are not in the order of the input" % impl)
        answers[impl] = {}
        for r in rows:
            if isinstance(r, dict) and r.get("id") in by_id and r["id"] not in answers[impl] and valid_row(r, impl, errors):
                answers[impl][r["id"]] = r

    classes = collections.defaultdict(collections.Counter)
    red, findings = [], []
    events = collections.defaultdict(list)
    known_entries = list((known or {}).get("entries", []))
    known_by_id = {}
    for k in known_entries:
        if k.get("id") in known_by_id:
            errors.append("known difference: id %s is listed twice" % k.get("id"))
        known_by_id[k.get("id")] = k
        if k.get("id") not in by_id:
            errors.append("known difference: id %s is not in the input" % k.get("id"))
    known_used = collections.Counter()
    subtypes = subtypes or {}
    position_findings = []
    pinned_subtype_measured = collections.Counter()
    pinned_in_parity = 0
    pinned_ids = set()
    pinned_hex = set()
    python_more_permissive = collections.Counter()
    python_diff_other = []
    python_diff_not_admitted = []
    admitted_ids = []

    def add_red(c, why, **extra):
        red.append(dict({"id": c["id"], "class": c["class"], "why": why}, **extra))

    for c in cases:
        cid, cls = c["id"], c["class"].split(":")[0]
        a = {i: answers[i].get(cid) for i in IMPLS}
        classes[cls]["cases"] += 1
        if any(v is None for v in a.values()):
            classes[cls]["red"] += 1
            add_red(c, "an answer is missing or outside the vocabulary")
            continue
        rust, go, py = a["rust"], a["go"], a["python"]
        if (not py["ok"]) and py["class"] in ("CRASH", "TIMEOUT"):
            events["python_unavailable"].append(cid)       # the oracle is not under proof: listed, not red
            py = {"ok": False, "class": "python_error", "code": py["class"]}
        bad = [i for i in ("rust", "go") if (not a[i]["ok"]) and a[i]["class"] in ("CRASH", "TIMEOUT")]
        if bad:
            classes[cls]["red"] += 1
            add_red(c, "%s: %s" % ("+".join(bad), "/".join(a[i]["class"] for i in bad)))
            continue
        # --- parity: measured. Semantic parity is P_parity; position parity is apart (D10)
        both_accept = accepted(rust) and accepted(go)
        parity = both_accept and rust["tree"] == go["tree"]
        both_refuse = (not accepted(rust)) and (not accepted(go))
        same_refusal = both_refuse and refusal_class(rust) == refusal_class(go) and rust.get("code") == go.get("code")
        if both_accept:
            pos_equal = rust.get("positions") == go.get("positions")
        elif both_refuse:
            pos_equal = rust.get("locations") == go.get("locations")
        else:
            pos_equal = False
        differ = (accepted(rust) != accepted(go)) or (both_accept and rust["tree"] != go["tree"]) or (both_refuse and refusal_class(rust) != refusal_class(go))
        if differ:
            findings.append({"id": cid, "class": c["class"], "rust": "accept" if accepted(rust) else rust["class"], "go": "accept" if accepted(go) else go["class"],
                             "kind": "accept_vs_refuse" if accepted(rust) != accepted(go) else ("tree_differs" if both_accept else "refusal_class_differs")})
        elif (parity or same_refusal) and not pos_equal:
            position_findings.append({"id": cid, "class": c["class"], "kind": "positions_differ" if parity else "refusal_locations_differ"})
        if parity:
            classes[cls]["position_parity" if pos_equal else "position_differs"] += 1
        # the measured subtype of a divergence (used for the pinned inputs)
        if parity and not pos_equal:
            measured = "positions_only"
        elif same_refusal and not pos_equal:
            measured = "refusal_position_only"
        elif parity or same_refusal:
            measured = "agrees"
        else:
            measured = "semantic"
        pe, allowed = c["parse_expect"], c["allowed"]
        effective = parity and allowed is True
        if parity:
            classes[cls]["parity"] += 1
        if effective:
            classes[cls]["effective"] += 1
            admitted_ids.append(cid)
        if parity and allowed is False:
            events["forbidden_construct_in_parity"].append(cid)
        if parity and allowed == "undetermined":
            events["undetermined_in_parity"].append(cid)
        failed = None
        # --- construction
        if pe["result"] == "accept":
            if not parity:
                failed = "expected both parsers to accept the tree; rust %s, go %s" % ("accept" if accepted(rust) else rust["class"], "accept" if accepted(go) else go["class"])
            elif rust["tree"] != pe["tree"]:
                failed = "both parsers accept a tree that is not the constructed one"
        elif pe["result"] == "refuse":
            if parity:
                failed = "both parsers accept a text that is forbidden (allowed false): a forbidden construct in P_parity"
            elif accepted(rust) or accepted(go):
                failed = "one parser accepts a text that is forbidden"
            elif pe["class"] != "any" and not (refusal_class(rust) == pe["class"] == refusal_class(go)):
                failed = "refusal class: rust %s, go %s, constructed %s" % (refusal_class(rust), refusal_class(go), pe["class"])
        elif pe["result"] == "pinned_divergence":
            pinned_ids.add(cid)
            pinned_hex.add(c["bytes_hex"])
            declared = subtypes.get(c["bytes_hex"], {"subtype": "semantic"})
            pinned_subtype_measured[measured] += 1
            if declared["subtype"] != measured:
                if measured == "agrees":
                    events["pinned_no_longer_diverges"].append(cid)
                    failed = "a pinned divergence no longer diverges at all (declared %s): remove it from the pinned list on purpose" % declared["subtype"]
                else:
                    events["pinned_subtype_changed"].append({"id": cid, "declared": declared["subtype"], "measured": measured})
                    failed = "a pinned divergence changed subtype: declared %s, measured %s (a change of the classification is a contract change)" % (declared["subtype"], measured)
                if measured == "semantic" or declared["subtype"] == "semantic":
                    pass
            elif declared["subtype"] == "semantic":
                pinned_in_parity += 1 if parity else 0
                classes[cls]["still_diverges"] += 1
            else:
                classes[cls]["declared_" + declared["subtype"]] += 1
                orig = declared.get("original")
                got_orig = {"rust": rust.get("positions", rust.get("locations")), "go": go.get("positions", go.get("locations"))}
                if orig is not None and orig != got_orig:
                    failed = "the original answers of the declared %s divergence changed" % declared["subtype"]
        # --- mapping
        if not failed and c.get("mapping_expect") is not None and parity:
            for impl in ("rust", "go"):
                if not mapping_matches(a[impl]["mapping"], c["mapping_expect"]):
                    failed = "mapping: %s answered %s, expected %s/%s" % (impl, json.dumps(a[impl]["mapping"], sort_keys=True)[:120], c["mapping_expect"]["refuse"], c["mapping_expect"]["field"])
                    break
        # --- python (oracle): never decides
        if parity:
            if not py["ok"]:
                events["python_refuses_a_parity_input"].append(cid)
            else:
                admitted = rust["tree"]
                if py["tree"] != admitted:
                    diff_read = [".".join(m) for m in READ_MEMBERS if member(py["tree"], m) != member(admitted, m)]
                    if diff_read and effective:
                        k = known_by_id.get(cid)
                        if k and k.get("kind") == "read_member_difference" and k.get("members") == diff_read and k.get("python_tree_sha256") == tree_sha(py["tree"]):
                            known_used[cid] += 1
                        else:
                            events["python_differs_on_read_members"].append({"id": cid, "members": diff_read})
                            failed = failed or "PyYAML reads the admitted input differently on %s" % diff_read
                    elif diff_read:
                        python_diff_not_admitted.append({"id": cid, "members": diff_read})
                    else:
                        python_diff_other.append(cid)
        elif py["ok"] and not accepted(rust) and not accepted(go):
            # Python accepts what both Forms parsers refuse: the safe direction, counted per class; a known difference is the stated expectation
            python_more_permissive[cls] += 1
            k = known_by_id.get(cid)
            if k and k.get("kind") == "python_accepts_forms_refuses":
                exact = (refusal_class(rust) == k.get("rust_class") and refusal_class(go) == k.get("go_class") and k.get("python_tree_sha256") == tree_sha(py["tree"]))
                if exact:
                    known_used[cid] += 1
                else:
                    failed = failed or "a known difference with another answer"
        elif not py["ok"] and pe["result"] == "refuse":
            classes[cls]["python_also_refuses"] += 1
        if failed:
            classes[cls]["red"] += 1
            add_red(c, failed)
        else:
            classes[cls]["green"] += 1

    for k in known_entries:
        if k.get("id") in by_id and known_used[k["id"]] == 0:
            errors.append("known difference %s (%s) no longer occurs with its exact answers" % (k.get("id"), k.get("kind")))
        if not k.get("acceptance"):
            errors.append("known difference %s has no recorded acceptance" % k.get("id"))
    for name in ("forbidden_construct_in_parity", "pinned_no_longer_diverges", "pinned_subtype_changed", "python_differs_on_read_members"):
        if events.get(name):
            errors.append("%s: %d case(s)" % (name, len(events[name])))
    for cls, floor in (floors or {}).items():
        if classes[cls]["cases"] < floor:
            errors.append("thin run: class %s has %d cases, the floor is %d" % (cls, classes[cls]["cases"], floor))
    pinned_cases = [c for c in cases if c["parse_expect"].get("result") == "pinned_divergence"]
    return {
        "cases": len(cases),
        "green": sum(v["green"] for v in classes.values()),
        "red_cases": len(red),
        "position_findings": position_findings,
        "pinned": {"occurrences": len(pinned_cases), "distinct_inputs": len({c["bytes_hex"] for c in pinned_cases}), "measured_subtypes": dict(pinned_subtype_measured),
                   "declared_non_semantic": {c["bytes_hex"][:16]: subtypes[c["bytes_hex"]]["subtype"] for c in pinned_cases if c["bytes_hex"] in subtypes},
                   "still_diverge": sum(v["still_diverges"] for v in classes.values()), "in_parity": pinned_in_parity},
        "p_effective": {"count": len(admitted_ids), "note": "analysis result, not a production admission (D9 undecided)"},
        "findings_rust_go": findings,
        "events": {k: v for k, v in events.items()},
        "python": {"more_permissive_by_class": dict(python_more_permissive), "differs_outside_read_members": len(python_diff_other), "differs_outside_read_members_ids": python_diff_other[:50],
                   "differs_on_read_members_but_not_admitted": python_diff_not_admitted},
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
    ap.add_argument("--subtypes")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    subtypes = json.load(open(a.subtypes, encoding="utf-8")) if a.subtypes else None
    known = json.load(open(a.known, encoding="utf-8")) if a.known else None
    floors = json.load(open(a.floors, encoding="utf-8")) if a.floors else None
    rep = compare(a.input, {i: getattr(a, i) for i in IMPLS}, known, floors, subtypes)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, sort_keys=True)
            f.write("\n")
    print("cases %d, green %d, red %d, P_effective %d, rust/go findings %d, errors %d" % (rep["cases"], rep["green"], rep["red_cases"], rep["p_effective"]["count"], len(rep["findings_rust_go"]), len(rep["errors"])))
    for e in rep["errors"][:20]:
        print("ERROR", e)
    for r in rep["red"][:20]:
        print("RED", r["id"], r["why"])
    return 1 if rep["is_red"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
