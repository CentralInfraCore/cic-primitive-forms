#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the dumps of the end-to-end chain (rust/tests/receipt_end_to_end.rs, go/receipt_end_to_end_test.go) against the Python reference.

    check_receipt_e2e.py DIR

DIR holds rust.e2e.jsonl and go.e2e.jsonl, one line per case: {"id", "base", "alt"}, each run holding every artefact of ONE execution of the chain (the schema
identity, the authored input, the standalone materialization, the receipt with the object the builder materialized inside, and `chain`, a digest over the schema,
the input, the output, the receipt, the path binding and the capability map of that run). The reference is testdata/p5/end-to-end.json, computed in Python by
tools/p5/end_to_end_cases.py from the fields each case lists. Exit status 1 when

 * a language lacks a case the reference has, repeats one, or has one the reference does not (completeness is checked per language: a case missing from BOTH is red);
 * a run lacks an artefact of the chain (a step was skipped) or has one the chain does not make;
 * the schema identity, the authored input, the standalone object, its output digest, the receipt, the binding or the default evidence differ from the reference;
 * the object the receipt builder materialized inside is not the standalone one (the chain states TWO materializations; they have to be one object);
 * the chain digest is not the one of the artefacts the record holds (an artefact of ANOTHER run was put in: the schema, the input, the output, the receipt, the path
   binding and the capability map have to be of one run);
 * a capability change does not move the object, the output digest and the receipt, or moves the input digest, the schema identity or the binding, or more of the
   receipt than its output digest;
 * the two languages differ in anything but the engine identity (and what depends on it: the receipt digest and the chain digest);
 * the case with two defaulted fields of EQUAL value has lost its point (two evidence entries with one value digest): the swapped binding the engine cannot see
   is then no longer tested by it.

The engine verifier is the other reader of the same records (tools/p5/engine-harness-*, mode `end-to-end`).
"""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "p5"))
import receipt_cases as rc  # noqa: E402

FIXTURE = ROOT / "testdata" / "p5" / "end-to-end.json"
IDENTITY = {"rust": "cic-primitive-forms-rust", "go": "cic-primitive-forms-go"}
RUN_KEYS = {"capabilities", "schema_name", "schema_version", "schema_digest", "authored_input", "standalone", "receipt", "chain"}
STANDALONE_KEYS = {"projection", "expected_keys", "output_digest"}
RECEIPT_KEYS = {"document", "digest", "binding", "authored_input", "projection", "output_digest"}


def lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def chain_digest(run):
    """The digest a run has to carry: of ITS schema, input, output, receipt, path binding (the engine takes it apart from the receipt) and capability map."""
    caps = json.dumps(run["capabilities"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    input_digest = "sha256:" + sha(run["authored_input"])
    binding = json.dumps(run["receipt"]["binding"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha("\n".join([run["schema_digest"], input_digest, run["standalone"]["output_digest"], run["receipt"]["digest"], caps, sha(binding)]))


def check_run(lang, cid, which, run, case, want, fixture):
    """-> the problems of ONE run of ONE language."""
    at = f"{lang} {cid} {which}"
    if not isinstance(run, dict) or set(run) != RUN_KEYS:
        return [f"{at}: the record has {sorted(run) if isinstance(run, dict) else run}, a chain has {sorted(RUN_KEYS)} (a step was skipped or invented)"]
    if set(run["standalone"]) != STANDALONE_KEYS or set(run["receipt"]) != RECEIPT_KEYS:
        return [f"{at}: the standalone materialization or the receipt lacks an artefact of the chain"]
    out = []
    name, version = rc.identity(case["schema"])
    if (run["schema_name"], run["schema_version"], run["schema_digest"]) != (name, version, "sha256:" + case["sha256"]):
        out.append(f"{at}: the schema identity is not the one of the schema source")
    if run["capabilities"] != want["capabilities"]:
        out.append(f"{at}: the capability map is not the one of the run")
    if run["authored_input"] != want["authored_input"]:
        out.append(f"{at}: the authored input")
    s, r = run["standalone"], run["receipt"]
    if (s["projection"], s["output_digest"], s["expected_keys"]) != (want["projection"], want["output_digest"], want["expected_keys"]):
        out.append(f"{at}: the standalone object or its output digest differs from the reference")
    if (r["projection"], r["output_digest"]) != (s["projection"], s["output_digest"]) or r["authored_input"] != run["authored_input"]:
        out.append(f"{at}: the object the receipt builder materialized is not the standalone one (two executions, two objects)")
    if r["document"] != want["document"].replace(fixture["engine_identity_token"], IDENTITY[lang]):
        out.append(f"{at}: the receipt differs from the reference")
    if r["digest"] != "sha256:" + sha(r["document"]):
        out.append(f"{at}: the receipt digest is not the digest of the receipt")
    if r["binding"] != want["binding"]:
        out.append(f"{at}: the path binding {r['binding']} is not the reference's {want['binding']}")
    if run["chain"] != chain_digest(run):
        out.append(f"{at}: the chain digest is not the one of the artefacts of the record (an artefact of another run, or of another capability map)")
    return out


def relation(lang, cid, base, alt):
    """-> the problems of the relation between the base run and the alt run of one case."""
    at = f"{lang} {cid}"
    out = []
    if not isinstance(base, dict) or not isinstance(alt, dict) or set(base) != RUN_KEYS or set(alt) != RUN_KEYS:
        return out
    b, a = base["receipt"], alt["receipt"]
    if base["standalone"]["projection"] == alt["standalone"]["projection"] or base["standalone"]["output_digest"] == alt["standalone"]["output_digest"] or b["document"] == a["document"]:
        out.append(f"{at}: a capability change did not move the object, the output digest and the receipt")
    if base["authored_input"] != alt["authored_input"] or (base["schema_name"], base["schema_version"], base["schema_digest"]) != (alt["schema_name"], alt["schema_version"], alt["schema_digest"]) or b["binding"] != a["binding"]:
        out.append(f"{at}: a capability change moved the authored input, the schema identity or the binding")
    try:
        bb, aa = json.loads(b["document"])["materialization"], json.loads(a["document"])["materialization"]
        bb.pop("output_digest", None)
        aa.pop("output_digest", None)
        if bb != aa:
            out.append(f"{at}: a capability change moved more of the receipt than its output digest")
        if json.loads(b["document"])["materialization"]["output_digest"] == alt["standalone"]["output_digest"]:
            out.append(f"{at}: the receipt of the base run names the output of the alt run")
    except (KeyError, ValueError):
        out.append(f"{at}: a receipt is not a receipt")
    return out


def twin_point(fixture):
    """-> problems when the equal-value case has lost its point: two evidence entries with one value digest."""
    out = []
    twin = [c for c in fixture["cases"] if c["id"] == "twin-defaults"]
    if len(twin) != 1:
        return ["the reference has no case twin-defaults"]
    digests = [d["value_digest"] for d in json.loads(twin[0]["base"]["document"])["materialization"]["applied_defaults"]]
    if len(digests) < 2 or len(set(digests)) == len(digests):
        out.append("twin-defaults has no two defaulted fields of one value: the swapped binding is not tested by it")
    return out


def strip_identity(lang, run):
    """A run with what depends on the engine identity taken out, for the comparison of the two languages."""
    r = dict(run["receipt"])
    r["document"] = r["document"].replace(IDENTITY[lang], "IDENTITY")
    del r["digest"]
    out = {k: v for k, v in run.items() if k not in ("chain", "receipt")}
    out["receipt"] = r
    return out


def check(fixture, dumps):
    """dumps: {"rust": [records], "go": [records]} -> a list of problems (empty when the chain is proved)."""
    problems = twin_point(fixture)
    ids = [c["id"] for c in fixture["cases"]]
    if len(ids) != len(set(ids)):
        problems.append("the reference repeats a case")
    by_lang = {}
    for lang in ("rust", "go"):
        records = dumps.get(lang, [])
        got = [r.get("id") for r in records]
        for cid in ids:
            n = got.count(cid)
            if n == 0:
                problems.append(f"{lang}: the case {cid} is missing")
            elif n > 1:
                problems.append(f"{lang}: the case {cid} is repeated")
        for cid in got:
            if cid not in ids:
                problems.append(f"{lang}: the case {cid} is not one the reference has")
        by_lang[lang] = {r["id"]: r for r in records if r.get("id") in ids}
        for case in fixture["cases"]:
            rec = by_lang[lang].get(case["id"])
            if rec is None:
                continue
            for which in ("base", "alt"):
                problems += check_run(lang, case["id"], which, rec.get(which), case, case[which], fixture)
            problems += relation(lang, case["id"], rec.get("base"), rec.get("alt"))
    for cid in ids:
        a, b = by_lang["rust"].get(cid), by_lang["go"].get(cid)
        if a is None or b is None:
            continue
        for which in ("base", "alt"):
            ra, rb = a.get(which), b.get(which)
            if isinstance(ra, dict) and isinstance(rb, dict) and set(ra) == set(rb) == RUN_KEYS and set(ra["receipt"]) == set(rb["receipt"]) == RECEIPT_KEYS:
                if strip_identity("rust", ra) != strip_identity("go", rb):
                    problems.append(f"{cid} {which}: Rust and Go differ in more than the engine identity")
    return problems


def main():
    d = pathlib.Path(sys.argv[1])
    fixture = json.loads(FIXTURE.read_text())
    problems = check(fixture, {l: lines(d / f"{l}.e2e.jsonl") for l in ("rust", "go")})
    for p in problems:
        print("E2E:", p, file=sys.stderr)
    if problems:
        return 1
    print(f"end-to-end: {len(fixture['cases'])} cases x 2 runs x 2 languages agree with the Python reference; the two executions of each chain are one object")
    return 0


if __name__ == "__main__":
    sys.exit(main())
