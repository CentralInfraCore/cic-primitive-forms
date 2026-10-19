"""The evidence record `test-evidence/1` (docs/TEST-EVIDENCE.md, "The record"): its schema and its hash.

`validate` accepts a decoded record and refuses, with a class, everything that is not exactly the record: a missing
or an extra member at any level, a wrong type, a value outside its set, a consistency rule broken. It checks the
shape and the rules inside the record; whether the record is the right one for a target (its key, its ancestry, its
age, its attestation) is verify.py.
"""
import datetime
import re

import canon

SCHEMA = "test-evidence/1"
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
DAY = re.compile(r"\d{4}-\d\d-\d\d\Z")
VERSION = re.compile(r"\d+\.\d+(\.\d+)?([-+.][0-9A-Za-z.+-]+)?\Z")
CONCLUSIONS = {"success", "failure", "cancelled", "skipped", "neutral", "timed_out", "action_required", "stale"}


class RecordError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


def _fail(kind, detail):
    raise RecordError(kind, detail)


def _obj(v, name, fields):
    """`fields` maps each member to a checker; the members must be exactly these."""
    if not isinstance(v, dict):
        _fail("TYPE", "%s is not an object" % name)
    missing, extra = sorted(set(fields) - set(v)), sorted(set(v) - set(fields))
    if missing:
        _fail("MISSING", "%s: %s" % (name, ", ".join(missing)))
    if extra:
        _fail("EXTRA", "%s: %s" % (name, ", ".join(extra)))
    for k, check in fields.items():
        check(v[k], "%s.%s" % (name, k))
    return v


def _int(v, name, lo=0):
    if not isinstance(v, int) or isinstance(v, bool) or v < lo:
        _fail("TYPE", "%s is not an integer >= %d" % (name, lo))


def _str(v, name):
    if not isinstance(v, str) or not v:
        _fail("TYPE", "%s is not a non-empty string" % name)


def _re(rx):
    def check(v, name):
        if not isinstance(v, str) or not rx.match(v):
            _fail("FORMAT", "%s does not match %s" % (name, rx.pattern))
    return check


def _enum(*allowed):
    def check(v, name):
        if v not in allowed or isinstance(v, bool) != any(isinstance(a, bool) for a in allowed if a == v):
            _fail("VALUE", "%s is not one of %s" % (name, list(allowed)))
    return check


def _nullable(check):
    return lambda v, name: None if v is None else check(v, name)


def _day(v, name):
    _re(DAY)(v, name)
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        _fail("FORMAT", "%s is not a date" % name)


def _ts(v, name):
    _re(TS)(v, name)
    try:
        datetime.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("FORMAT", "%s is not a time" % name)


def parse_ts(v):
    return datetime.datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def _counts(*names):
    """{covered, total} or {killed, survived, ...}: non-negative integers; coverage never covers more than there is."""
    def check(v, name):
        _obj(v, name, {n: _int for n in names})
        if names[0] == "covered" and v["covered"] > v["total"]:
            _fail("CONSISTENCY", "%s covers more than there is" % name)
    return check


def _map_of(value_check, nonempty=True):
    def check(v, name):
        if not isinstance(v, dict) or (nonempty and not v):
            _fail("TYPE", "%s is not a non-empty object" % name)
        for k, x in v.items():
            if not isinstance(k, str) or not k:
                _fail("TYPE", "%s has a member name that is not a string" % name)
            value_check(x, "%s[%s]" % (name, k))
    return check


def _artifact_ref(v, name):
    _obj(v, name, {"digest": _re(SHA256), "size": _int, "artifact": _str})


def _target(v, name):
    _obj(v, name, {"kind": _enum("main", "merge_candidate"), "commit": _re(SHA1), "tree": _re(SHA1),
                   "base_commit": _nullable(_re(SHA1)), "head_commit": _nullable(_re(SHA1))})
    mc = v["kind"] == "merge_candidate"
    if mc != (v["base_commit"] is not None and v["head_commit"] is not None) or (not mc and (v["base_commit"] or v["head_commit"])):
        _fail("CONSISTENCY", "%s: a merge candidate names its base and head, a main record names neither" % name)


def _environment(v, name):
    """The resolved environment: versions that were actually resolved, never a label asked for."""
    version = _re(VERSION)
    _obj(v, name, {
        "rustc": version, "cargo": version, "go": _re(re.compile(r"go\d+\.\d+(\.\d+)?\Z")), "python": version,
        "tools": lambda x, n: _obj(x, n, {t: version for t in ("cargo-llvm-cov", "cargo-mutants", "go-mutesting", "cargo-fuzz")}),
        "runner": lambda x, n: _obj(x, n, {"image_os": _str, "image_version": _str, "os": _str, "arch": _str}),
        # every action by the full commit it resolves to: a tag such as v4 is a label, not an identity
        "actions": _map_of(_re(SHA1)),
    })


def _run(v, name):
    _obj(v, name, {"repository": _re(re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")), "workflow_path": _str,
                   "ref": _re(re.compile(r"refs/.+\Z")), "run_id": lambda x, n: _int(x, n, 1),
                   "run_attempt": lambda x, n: _int(x, n, 1), "commit": _re(SHA1)})


def _quality(v, name):
    _obj(v, name, {
        "rust_lines": _counts("covered", "total"), "go_statements": _counts("covered", "total"),
        "rust_branches": _counts("covered", "total"), "go_branches": _counts("covered", "total"),
        "rust_mutation": _counts("killed", "survived", "unviable"), "go_mutation": _counts("killed", "survived", "uncompilable"),
        "thresholds": lambda x, n: _obj(x, n, {k: lambda y, m: _int(y, m, 0) for k in ("line", "mutation", "branch")}),
        "gate_verdict": _enum("PASS", "FAIL"),
        "shards": lambda x, n: _obj(x, n, {"rust": lambda y, m: _int(y, m, 1), "go": lambda y, m: _int(y, m, 1)}),
        "completeness": _enum("complete", "incomplete"),
    })


def _required_check(v, name):
    _obj(v, name, {"name": _str, "conclusion": _enum(*sorted(CONCLUSIONS)), "job_url": _str,
                   "run_id": lambda x, n: _int(x, n, 1), "artifact_digest": _nullable(_re(SHA256))})


def _artifacts(v, name):
    if not isinstance(v, list):
        _fail("TYPE", "%s is not a list" % name)
    seen = set()
    for i, a in enumerate(v):
        _obj(a, "%s[%d]" % (name, i), {"name": _str, "sha256": _re(SHA256), "size": _int})
        if a["name"] in seen:
            _fail("CONSISTENCY", "%s: the artifact %s is listed twice" % (name, a["name"]))
        seen.add(a["name"])


def _required_checks(v, name):
    if not isinstance(v, list) or not v:
        _fail("TYPE", "%s is not a non-empty list" % name)
    names = set()
    for i, c in enumerate(v):
        _required_check(c, "%s[%d]" % (name, i))
        if c["name"] in names:
            _fail("CONSISTENCY", "%s: the check %s is listed twice" % (name, c["name"]))
        names.add(c["name"])


FIELDS = {
    "schema": _enum(SCHEMA), "kind": _enum("full"), "outcome": _enum("pass", "fail"), "slot": _nullable(_day),
    "target": _target, "validity_key": _re(SHA256), "validity_inputs": _map_of(_re(SHA256)),
    "environment": _environment, "run": _run, "measured_at": _ts, "quality_full": _quality,
    "required_checks": _required_checks, "anchor": _enum(True, False),
    "mutants": _nullable(_artifact_ref), "coverage_map": _nullable(_artifact_ref), "artifacts": _artifacts,
}


def validate(rec, expected_checks=None):
    """The record is exactly the schema, and consistent in itself. `expected_checks` is the list of required checks
    the record has to answer for (required-checks.json at the commit): each once, no more, no fewer."""
    _obj(rec, "record", FIELDS)
    q, checks = rec["quality_full"], rec["required_checks"]
    green = q["gate_verdict"] == "PASS" and q["completeness"] == "complete" and all(c["conclusion"] == "success" for c in checks)
    if rec["outcome"] == "pass" and not green:
        _fail("CONSISTENCY", "outcome is pass but the gate or a required check did not pass")
    if rec["outcome"] == "fail" and green:
        _fail("CONSISTENCY", "outcome is fail but everything passed")
    if rec["anchor"] != (rec["outcome"] == "pass"):
        _fail("CONSISTENCY", "anchor is true exactly when the outcome is pass")
    if rec["slot"] is not None and rec["target"]["kind"] != "main":
        _fail("CONSISTENCY", "only a record of main has a slot")
    if rec["target"]["commit"] != rec["run"]["commit"] and rec["target"]["kind"] == "main":
        _fail("CONSISTENCY", "a record of main is made by the run on that commit")
    if "environment" not in rec["validity_inputs"]:
        _fail("CONSISTENCY", "validity_inputs lacks the environment digest")
    if rec["outcome"] == "pass" and min(q["thresholds"].values()) < 90:
        _fail("CONSISTENCY", "a pass measured against a threshold below 90")
    if expected_checks is not None and sorted(c["name"] for c in checks) != sorted(expected_checks):
        _fail("CHECKS", "the required checks of the record are not the expected ones")
    return rec


def record_hash(rec):
    return canon.content_hash(rec)
