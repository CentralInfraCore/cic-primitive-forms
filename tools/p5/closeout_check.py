#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the closeout ledger of P5-1, P5-2 and P5-3 (docs/p5-closeout.json, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 22).

    closeout_check.py            check the ledger in the repository (exit 1 and the problems on stderr)

The ledger separates four statuses, and this checker holds it to them:

 * `proved` and `fixture_only` entries name their evidence and the checker reads it: every file exists, every named text is in its file, every count is the count of
   the file, every CI step is a step of the workflow, every doc section is a heading of the doc. An entry with no evidence is not accepted as proved.
 * `not_proved` and `deferred` entries carry a reason and no evidence.
 * P5-3 closes only as a flat, fixture-based receipt: an entry that touches a Forms receipt (`receipt: true`, or evidence that is a receipt or end-to-end file)
   cannot be `proved`, and a `proved` entry has to say `receipt: false`.
 * The items the owner keeps open (REQUIRED_OPEN, fixed in this file, not in the ledger) cannot be dropped, or moved to `proved`. The required phrases and the
   sections without a contract are fixed here too: the ledger cannot lower what it is held to.

It also checks that the ledger and the doc cannot drift apart: the pins equal the files they quote, every section of the doc from 10 on is covered by an entry (or is
named as one without a contract), every entry id appears in the doc's section 22 and the doc holds the required phrases.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "p5-closeout.json"
STATUSES = {"proved", "fixture_only", "not_proved", "deferred"}
WITH_EVIDENCE = {"proved", "fixture_only"}
UNITS = {"P5-1", "P5-2", "P5-3"}
# What the ledger is held to is fixed HERE, not read from the ledger: an input that could lower its own requirements would check nothing (an open item deleted from
# the entries and from a list of requirements in the same file would go unseen).
REQUIRED_OPEN = (
    "authentic-release-origin",
    "reference-resolution",
    "capability-source-authenticated",
    "shared-compiled-schema-instance",
    "engine-verifier-blind-to-schema-identity",
    "production-receipt-api",
    "non-flat-binding",
    "list-element-selector",
    "non-flat-materialization",
    "p5-4-decision-f",
    "engine-63-open",
    "rust-go-quoted-scalar-parser-difference",
)
REQUIRED_PHRASES = (
    "P5-3 end-to-end proof without reference resolution",
    "flat, fixture-based receipt",
)
# the sections from 10 on that state no contract: the pre-checks. A section cannot be added here from the ledger to escape the coverage check.
NO_CONTRACT_SECTIONS = ("11", "15")
CLOSEOUT_SECTION = "22"
FIRST_SECTION = 10
# a file of the receipt or of the end-to-end chain: an entry that rests on one touches a Forms receipt, whatever its own flag says
RECEIPT_FILE = re.compile(r"receipt|end[-_]to[-_]end", re.I)


def read(root, rel):
    return (root / rel).read_text(encoding="utf-8")


def headings(doc):
    """-> the top-level section numbers of the doc ('## 10. ...') from FIRST_SECTION on."""
    return [m.group(1) for m in re.finditer(r"^## (\d+)\.", doc, re.M) if int(m.group(1)) >= FIRST_SECTION]


def count_of(root, spec):
    """-> (the measured count, the label), or (None, the problem)."""
    path = root / spec["file"]
    kind = spec.get("kind")
    if not path.exists():
        return None, f"{spec['file']} does not exist"
    if kind == "json_len":
        return len(json.loads(path.read_text(encoding="utf-8"))[spec["key"]]), f"{spec['file']}[{spec['key']}]"
    if kind == "tsv_data_lines":
        return len([l for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]), f"data lines of {spec['file']}"
    if kind == "occurrences":
        return path.read_text(encoding="utf-8").count(spec["text"]), f"occurrences of {spec['text']!r} in {spec['file']}"
    if kind == "dir_entries":
        return len([x for x in path.iterdir() if x.is_dir()]), f"directories of {spec['file']}"
    return None, f"unknown count kind {kind!r}"


def check_evidence(root, eid, ev, steps, problems):
    if not isinstance(ev, dict) or not ev.get("files"):
        problems.append(f"{eid}: a proved or fixture-only entry has no evidence files")
        return
    for rel in ev["files"]:
        if not (root / rel).exists():
            problems.append(f"{eid}: the evidence file {rel} does not exist")
    for c in ev.get("contains", []):
        p = root / c["file"]
        if not p.exists():
            problems.append(f"{eid}: the file {c['file']} does not exist")
        elif c["text"] not in p.read_text(encoding="utf-8"):
            problems.append(f"{eid}: {c['file']} does not contain {c['text']!r}")
    for step in ev.get("ci_steps", []):
        if step not in steps:
            problems.append(f"{eid}: the workflow has no step named {step!r}")
    for spec in ev.get("counts", []):
        n, label = count_of(root, spec)
        if n is None:
            problems.append(f"{eid}: {label}")
        elif "equals" in spec and n != spec["equals"]:
            problems.append(f"{eid}: {label} is {n}, the ledger says {spec['equals']}")
        elif "at_least" in spec and n < spec["at_least"]:
            problems.append(f"{eid}: {label} is {n}, the ledger says at least {spec['at_least']}")
        elif "equals" not in spec and "at_least" not in spec:
            problems.append(f"{eid}: a count with no expectation")


def check(root=ROOT, ledger=None):
    """-> a list of problems (empty when the ledger holds)."""
    problems = []
    ledger = ledger if ledger is not None else json.loads(read(root, "docs/p5-closeout.json"))
    doc = read(root, ledger["doc"])
    workflow = read(root, ledger["workflow"])
    steps = {m.group(1).strip() for m in re.finditer(r"^\s*- name: (.+)$", workflow, re.M)}
    entries = ledger["entries"]

    ids = [e.get("id") for e in entries]
    for eid in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{eid}: the id is repeated")
    by_id = {e["id"]: e for e in entries if "id" in e}

    pin = re.search(r"^commit (\w+)$", read(root, "testdata/p5/engine-pin.txt"), re.M)
    if not pin or pin.group(1) != ledger["engine_pin"]:
        problems.append("the engine pin of the ledger is not the commit of testdata/p5/engine-pin.txt")
    if ledger["grammar_pin_commit"] not in read(root, "corpus/pins.yaml"):
        problems.append("the grammar pin commit of the ledger is not in corpus/pins.yaml")

    sections = set(headings(doc))
    covered = set()
    for e in entries:
        eid = e.get("id", "?")
        status = e.get("status")
        if status not in STATUSES:
            problems.append(f"{eid}: the status {status!r} is not one of {sorted(STATUSES)}")
            continue
        if e.get("unit") not in UNITS:
            problems.append(f"{eid}: the unit {e.get('unit')!r} is not one of {sorted(UNITS)}")
        if not str(e.get("claim", "")).strip():
            problems.append(f"{eid}: no claim")
        for s in e.get("doc", []):
            covered.add(s)
            if s not in sections and s not in NO_CONTRACT_SECTIONS:
                problems.append(f"{eid}: the doc has no section {s}")
        if not e.get("doc"):
            problems.append(f"{eid}: no doc section")
        if f"`{eid}`" not in doc:
            problems.append(f"{eid}: the id is not in the doc (section {CLOSEOUT_SECTION} has to hold every entry)")
        if status in WITH_EVIDENCE:
            check_evidence(root, eid, e.get("evidence"), steps, problems)
            if not str(e.get("limit", "")).strip():
                problems.append(f"{eid}: a proved or fixture-only entry states no limit")
            if e.get("receipt") not in (True, False):
                problems.append(f"{eid}: 'receipt' has to say whether the entry touches a Forms receipt")
            elif e["receipt"] and status == "proved":
                problems.append(f"{eid}: an entry that touches a Forms receipt cannot be proved: P5-3 closes only as a flat fixture receipt")
            elif e.get("unit") == "P5-3" and status == "proved" and e["receipt"] is not False:
                problems.append(f"{eid}: a proved entry of P5-3 has to say receipt: false")
            elif status == "proved" and any(RECEIPT_FILE.search(f) for f in (e.get("evidence") or {}).get("files", []) + [c["file"] for c in (e.get("evidence") or {}).get("contains", [])]):
                problems.append(f"{eid}: a proved entry rests on a receipt file; the flag 'receipt: false' is not believed against its own evidence (P5-3 closes only as a flat fixture receipt)")
        else:
            if "evidence" in e:
                problems.append(f"{eid}: a {status} entry carries evidence; evidence belongs to a proved claim")
            if not str(e.get("reason", "")).strip():
                problems.append(f"{eid}: a {status} entry gives no reason")

    for rid in REQUIRED_OPEN:
        got = by_id.get(rid)
        if got is None:
            problems.append(f"{rid}: a required open item is missing from the ledger")
        elif got.get("status") not in ("not_proved", "deferred"):
            problems.append(f"{rid}: a required open item is {got.get('status')}, it has to stay not_proved or deferred")

    for s in sorted(sections - {CLOSEOUT_SECTION}, key=int):
        if s not in covered and s not in NO_CONTRACT_SECTIONS:
            problems.append(f"section {s} of the doc is covered by no entry of the ledger")
    for p in REQUIRED_PHRASES:
        if p not in doc:
            problems.append(f"the doc lacks the phrase {p!r}")
    if CLOSEOUT_SECTION not in set(re.findall(r"^## (\d+)\.", doc, re.M)):
        problems.append(f"the doc has no section {CLOSEOUT_SECTION}")
    return problems


def main():
    problems = check()
    for p in problems:
        print("CLOSEOUT:", p, file=sys.stderr)
    if problems:
        return 1
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    tally = {}
    for e in ledger["entries"]:
        tally[e["status"]] = tally.get(e["status"], 0) + 1
    print("closeout: " + ", ".join(f"{k} {tally.get(k, 0)}" for k in ("proved", "fixture_only", "not_proved", "deferred")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
