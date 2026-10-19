"""Building an evidence record from the results of a FULL run (docs/EVIDENCE-PRODUCTION.md, "From a run to a record").

The inputs are what the run left: the conclusions of its jobs, and the artifacts the measuring jobs uploaded (the raw reports of
the quality measurements, the branch coverage reports, the parts of the environment). The numbers are worked out again from
the raw reports with the functions of the gate itself, not read off a log; the verdict is not taken from the record's author
but from the numbers, and the conclusion of the gate's own jobs has to agree with it. Anything that cannot be had, an
artifact that is missing, shards that are incomplete, an environment that conflicts, a job that did not finish, a gate that
disagrees with its numbers, is a `BuildError` and no record: a run that cannot be described leaves nothing, and a missing
record is what the chain rule of the verifier counts as a missed run.

Nothing here writes to a branch or signs: that is step B2.
"""
import datetime
import glob
import hashlib
import json
import os
import sys

import canon
import envcap
import key as keymod
import record

TOOLS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, TOOLS)
import quality_gate  # noqa: E402

BRANCH_THRESHOLD = 90  # coverage_gate.THRESHOLD (a test holds them equal; that module needs PyYAML)


class BuildError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


def _read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as e:
        raise BuildError("MISSING_ARTIFACT", "%s: %s" % (path, e))


def _files(base, pattern):
    found = sorted(glob.glob(os.path.join(base, pattern)))
    if not found:
        raise BuildError("MISSING_ARTIFACT", "%s under %s" % (pattern, base))
    return found


def required_checks(jobs, expected):
    """One entry for each expected check, from the job of that name. A check that did not run, ran twice or did not finish is an error."""
    out = []
    for name in expected:
        found = [j for j in jobs if j.get("name") == name]
        if not found:
            raise BuildError("MISSING_JOB", name)
        if len(found) > 1:
            raise BuildError("AMBIGUOUS_JOB", name)
        j = found[0]
        if j.get("conclusion") not in record.CONCLUSIONS:
            raise BuildError("JOB_NOT_FINISHED", "%s: %r" % (name, j.get("conclusion")))
        out.append({"name": name, "conclusion": j["conclusion"], "job_url": j["html_url"], "run_id": j["run_id"], "artifact_digest": None})
    return out


def _count(measure, *a):
    try:
        return measure(*a)
    except quality_gate.Refused as r:
        kind = "INCOMPLETE_MEASUREMENT" if r.reason == "INCOMPLETE_SHARDS" else "UNMEASURED"
        raise BuildError(kind, "%s: %s" % (r.reason, r.detail))


def quality_numbers(art):
    """The quality measurements from the raw reports, and the paths they were read from."""
    used = []
    rl = os.path.join(art, "lines", "rust-lines.json")
    gl = os.path.join(art, "lines", "go-lines.out")
    rm = _files(art, "rust-mutants-*/outcomes.json")
    listed = os.path.join(art, "quality-listed", "rust-listed.json")
    ledgers = _files(art, "go-ledger-*/ledger")
    for p in [rl, gl, listed] + rm + ledgers:
        _read(p)
        used.append(p)
    rc, rt = _count(quality_gate.rust_lines, rl)
    gc, gt = _count(quality_gate.go_lines, gl)
    rk, rtot = _count(quality_gate.rust_mutants, rm, listed)
    gk, gtot = _count(quality_gate.go_ledgers, ledgers)
    unviable = 0
    for p in rm:
        unviable += json.loads(_read(p))["unviable"]
    uncompilable = 0
    go_shards = set()
    for p in ledgers:
        for line in _read(p).decode("utf-8").splitlines():
            f = line.split("\t")
            go_shards.add(int(f[2]))
            uncompilable += f[3] == "uncompilable"
    if len(go_shards) != 1:
        raise BuildError("INCOMPLETE_MEASUREMENT", "the go ledgers disagree on the number of shards")
    return {
        "rust_lines": {"covered": rc, "total": rt}, "go_statements": {"covered": gc, "total": gt},
        "rust_mutation": {"killed": rk, "survived": rtot - rk, "unviable": unviable},
        "go_mutation": {"killed": gk, "survived": gtot - gk, "uncompilable": uncompilable},
        "shards": {"rust": len(rm), "go": go_shards.pop()},
    }, used


def branch_numbers(art, commit):
    """The branch coverage of both languages from the reports the branch-coverage job made: for this commit, and branch metric."""
    out, used = {}, []
    for lang, key in (("rust", "rust_branches"), ("go", "go_branches")):
        path = os.path.join(art, "branch-reports", "%s-report.json" % lang)
        used.append(path)
        try:
            rep = json.loads(_read(path))
            totals = rep["totals"]
            ok = (rep["language"] == lang and rep["metric"] == "branch" and rep["source"]["commit"] == commit
                  and all(isinstance(totals[k], int) and not isinstance(totals[k], bool) for k in ("covered", "total")))
        except (ValueError, KeyError, TypeError):
            raise BuildError("BAD_BRANCH_REPORT", path)
        if not ok:
            raise BuildError("BAD_BRANCH_REPORT", "%s is not a branch report of %s for %s" % (path, lang, commit))
        out[key] = {"covered": totals["covered"], "total": totals["total"]}
    return out, used


def environment(art, root):
    parts = []
    for p in _files(art, "env-*/env.json"):
        try:
            parts.append(json.loads(_read(p)))
        except ValueError:
            raise BuildError("BAD_ENVIRONMENT", p)
    try:
        return envcap.merge(parts, root), [p for p in _files(art, "env-*/env.json")]
    except envcap.EnvError as e:
        raise BuildError("ENVIRONMENT_" + e.kind, str(e))


def _artifact_entry(art, path):
    data = _read(path)
    return {"name": os.path.relpath(path, art).replace(os.sep, "/"), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def build(art, jobs, root, target, run, slot, measured_at, expected_checks, gate_jobs=("quality", "branch-coverage")):
    """The record of a FULL run, its canonical bytes and its file name; (record, raw, filename, kind)."""
    checks = required_checks(jobs, expected_checks)
    qn, used_q = quality_numbers(art)
    bn, used_b = branch_numbers(art, run["commit"])
    env, used_e = environment(art, root)
    q = dict(qn, **bn)
    q["thresholds"] = {"line": quality_gate.THRESHOLD, "mutation": quality_gate.THRESHOLD, "branch": BRANCH_THRESHOLD}
    q["completeness"] = "complete"
    quality_ok, branch_ok = record.quality_pass(q), record.branch_pass(q)
    conclusion = {c["name"]: c["conclusion"] for c in checks}
    for name, ok in (("quality", quality_ok), ("branch-coverage", branch_ok)):
        if name in expected_checks and (conclusion[name] == "success") != ok:
            raise BuildError("GATE_DISAGREES", "the job %s concluded %s, its numbers say %s" % (name, conclusion[name], "pass" if ok else "fail"))
    green = record.numbers_pass(q)
    q["gate_verdict"] = "PASS" if green else "FAIL"
    outcome = "pass" if green and all(c["conclusion"] == "success" for c in checks) else "fail"
    try:
        validity_key, named = keymod.validity_key(root, env)
    except keymod.KeyError_ as e:
        raise BuildError("KEY_" + e.kind, str(e))
    rec = {
        "schema": record.SCHEMA, "kind": "full", "outcome": outcome, "slot": slot, "target": target,
        "validity_key": validity_key, "validity_inputs": named, "environment": env, "run": run,
        "measured_at": measured_at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quality_full": q, "required_checks": checks, "anchor": outcome == "pass",
        "mutants": None, "coverage_map": None,
        "artifacts": sorted((_artifact_entry(art, p) for p in used_q + used_b + used_e), key=lambda a: a["name"]),
    }
    try:
        record.validate(rec, expected_checks)
    except record.RecordError as e:
        raise BuildError("INVALID_RECORD", "%s %s" % (e.kind, e.detail))
    raw = canon.canonicalize(rec)
    return rec, raw, hashlib.sha256(raw).hexdigest() + ".json", "full" if outcome == "pass" else "audit"
