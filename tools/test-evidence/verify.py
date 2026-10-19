"""Verifying an evidence record, and choosing the baseline (docs/TEST-EVIDENCE.md, "Verification" and "Which record may serve").

Every step is a trust boundary and every step has a probe in test_evidence.py that breaks it. A failure is a
`VerifyError` with a code; there is no partial success, and nothing here falls back to another record.

`verify_record` takes the raw bytes and the file name, and a `Context` that says what the record has to be right for:
the time, the target, the tree to compute the key from, the environment of the run, how to ask whether a commit is
an ancestor, how to check the attestation, how to find an artifact. `choose_baseline` applies the rule that a failed,
missed or unverifiable run breaks the chain.
"""
import datetime
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field

import canon
import key as keymod
import record

MAX_AGE = datetime.timedelta(days=3)
DUE_HOUR_UTC = 6


class VerifyError(Exception):
    def __init__(self, code, detail=""):
        super().__init__("%s %s" % (code, detail))
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Trust:
    """What an attestation has to say: who built the record. B creates the workflow named here."""
    repository: str = "CentralInfraCore/cic-primitive-forms"
    signer_workflow: str = ".github/workflows/evidence.yml"
    source_ref: str = "refs/heads/main"


class Attestation:
    def verify(self, record_path, digest, trust, source_commit):
        raise NotImplementedError


GH_FLAGS = ("--repo", "--signer-workflow", "--source-ref", "--source-digest")


class GhAttestation(Attestation):
    """`gh attestation verify` with the exact expectations. Any other outcome than a clean exit is a refusal,
    including a missing `gh`, a timeout and a `gh` that does not know the command."""

    def __init__(self, gh="gh", timeout=120):
        self.gh = gh
        self.timeout = timeout

    def argv(self, record_path, trust, source_commit):
        return [self.gh, "attestation", "verify", record_path,
                "--repo", trust.repository,
                "--signer-workflow", "%s/%s" % (trust.repository, trust.signer_workflow),
                "--source-ref", trust.source_ref,
                "--source-digest", source_commit]

    def verify(self, record_path, digest, trust, source_commit):
        try:
            done = subprocess.run(self.argv(record_path, trust, source_commit), capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0

    def missing_flags(self):
        """The flags used above that `gh attestation verify --help` does not list (all of them, when it cannot be asked)."""
        try:
            done = subprocess.run([self.gh, "attestation", "verify", "--help"], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return list(GH_FLAGS)
        text = (done.stdout + done.stderr).decode("utf-8", "replace")
        return [f for f in GH_FLAGS if f not in text] if done.returncode == 0 else list(GH_FLAGS)


@dataclass
class Context:
    now: datetime.datetime
    target_commit: str
    root: str                     # the checkout of the target: the key is computed from it
    environment: dict             # the environment of the verifying run, as resolved now
    is_ancestor: object           # (ancestor, descendant) -> bool
    attestation: Attestation
    expected_checks: list
    trust: Trust = field(default_factory=Trust)
    artifacts: object = None      # name -> bytes or None
    max_age: datetime.timedelta = MAX_AGE


@dataclass
class Verified:
    rec: dict
    digest: str

    @property
    def measured_at(self):
        return record.parse_ts(self.rec["measured_at"])


def _check_envelope(raw, filename, ctx):
    """Steps 1 to 3: the bytes are canonical, the name is their hash, the attestation holds, the schema is the schema."""
    try:
        rec = canon.read_canonical(raw)
    except canon.CanonError as e:
        raise VerifyError("NOT_CANONICAL" if e.kind == "NOT_CANONICAL" else "NOT_JSON", e.kind)
    digest = hashlib.sha256(raw).hexdigest()
    if filename != digest + ".json":
        raise VerifyError("NAME_MISMATCH", "%s is not the hash %s" % (filename, digest))
    try:
        record.validate(rec, ctx.expected_checks)
    except record.RecordError as e:
        raise VerifyError("SCHEMA", "%s %s" % (e.kind, e.detail))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, filename)
        with open(path, "wb") as fh:
            fh.write(raw)
        if ctx.attestation.verify(path, digest, ctx.trust, rec["run"]["commit"]) is not True:
            raise VerifyError("ATTESTATION", "no valid attestation for %s from %s" % (digest, ctx.trust.signer_workflow))
    return Verified(rec, digest)


def verify_record(raw, filename, ctx):
    """A record that is genuine: canonical, named by its hash, attested by the evidence workflow, in the schema."""
    return _check_envelope(raw, filename, ctx)


def verify_baseline(raw, filename, ctx):
    """The same, and fit to be the baseline of a DELTA for the target of ctx (steps 3 to 6 and 8)."""
    v = _check_envelope(raw, filename, ctx)
    rec = v.rec
    if rec["outcome"] != "pass" or rec["anchor"] is not True:
        raise VerifyError("NOT_A_BASELINE", "outcome %s, anchor %s" % (rec["outcome"], rec["anchor"]))
    if rec["target"]["kind"] != "main":
        raise VerifyError("NOT_A_BASELINE", "a baseline is a record of main")
    try:
        want, named = keymod.validity_key(ctx.root, ctx.environment)
    except keymod.KeyError_ as e:
        raise VerifyError("KEY_UNAVAILABLE", "%s %s" % (e.kind, e))
    if keymod.key_of(rec["validity_inputs"]) != rec["validity_key"]:
        raise VerifyError("KEY_INCONSISTENT", "validity_key is not the digest of validity_inputs")
    if rec["validity_key"] != want:
        changed = sorted(k for k in set(named) | set(rec["validity_inputs"]) if named.get(k) != rec["validity_inputs"].get(k))
        raise VerifyError("KEY_MISMATCH", "changed: %s" % ", ".join(changed[:5]))
    commit = rec["target"]["commit"]
    if commit != ctx.target_commit and not ctx.is_ancestor(commit, ctx.target_commit):
        raise VerifyError("NOT_ANCESTOR", "%s is not an ancestor of %s" % (commit, ctx.target_commit))
    age = ctx.now - v.measured_at
    if age > ctx.max_age or age < datetime.timedelta(0):
        raise VerifyError("EXPIRED", "measured %s, now %s" % (rec["measured_at"], ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ")))
    for ref in (rec["mutants"], rec["coverage_map"]):
        if ref is None:
            continue
        data = ctx.artifacts(ref["artifact"]) if ctx.artifacts else None
        if data is None:
            raise VerifyError("ARTIFACT_MISSING", ref["artifact"])
        if hashlib.sha256(data).hexdigest() != ref["digest"] or len(data) != ref["size"]:
            raise VerifyError("ARTIFACT_DIGEST", ref["artifact"])
    return v


# --- the chain -----------------------------------------------------------------------------------------------------

def latest_due_slot(now):
    """The UTC day whose due time (DUE_HOUR_UTC o'clock) is not after now."""
    day = now.astimezone(datetime.timezone.utc).date()
    due = datetime.datetime(day.year, day.month, day.day, DUE_HOUR_UTC, tzinfo=datetime.timezone.utc)
    return (day if now >= due else day - datetime.timedelta(days=1)).isoformat()


@dataclass
class Choice:
    baseline: object  # Verified or None
    reason: str       # "OK" or why nothing is usable
    detail: str = ""


def choose_baseline(records, now, target_commit, is_ancestor, max_age=MAX_AGE):
    """`records`: every verified record of main (genuine ones, any outcome). The rule of the plan, section 5.7:

    1. there is a record for the latest due slot (a run that did not happen leaves none: that is the refusal);
    2. the newest record, whatever its outcome and whether it came from the schedule or from a push, is a passing anchor;
    3. the baseline is a passing anchor that no failed record follows;
    4. it is not older than max_age, and is an ancestor of the target (or the target itself).
    When the state is uncertain nothing is usable: the answer is a FULL run, never a DELTA from what there is."""
    mains = [v for v in records if v.rec["target"]["kind"] == "main"]
    if not mains:
        return Choice(None, "NO_RECORDS")
    mains.sort(key=lambda v: (v.measured_at, v.digest))
    newest = mains[-1]
    ties = [v for v in mains if v.measured_at == newest.measured_at]
    if len({v.rec["outcome"] for v in ties}) > 1:
        return Choice(None, "UNCERTAIN", "records of the same second disagree")
    slot = latest_due_slot(now)
    if not any(v.rec["slot"] == slot for v in mains):
        return Choice(None, "SLOT_MISSED", "no record for %s" % slot)
    if newest.rec["outcome"] != "pass" or newest.rec["anchor"] is not True:
        return Choice(None, "NEWEST_NOT_PASS", "the newest record is %s" % newest.rec["outcome"])
    last_fail = max((v.measured_at for v in mains if v.rec["outcome"] != "pass"), default=None)
    candidates = [v for v in mains if v.rec["outcome"] == "pass" and v.rec["anchor"] is True
                  and (last_fail is None or v.measured_at > last_fail)]
    fresh = [v for v in candidates if datetime.timedelta(0) <= now - v.measured_at <= max_age]
    if not fresh:
        return Choice(None, "TOO_OLD", "no passing anchor after the last failure is within %s" % max_age)
    related = [v for v in fresh if v.rec["target"]["commit"] == target_commit or is_ancestor(v.rec["target"]["commit"], target_commit)]
    if not related:
        return Choice(None, "NO_ANCESTOR", "no usable record is an ancestor of %s" % target_commit)
    return Choice(related[-1], "OK")


def load_and_choose(index_entries, fetch, ctx):
    """`index_entries`: the (hash, kind) pairs the indexes list for main; `fetch(hash, kind)` returns the bytes or None.
    Every listed record has to be there and genuine: one that is missing or does not verify makes the state uncertain."""
    verified = []
    for digest, kind in index_entries:
        raw = fetch(digest, kind)
        if raw is None:
            return Choice(None, "UNCERTAIN", "listed record %s is missing" % digest)
        try:
            verified.append(verify_record(raw, digest + ".json", ctx))
        except VerifyError as e:
            return Choice(None, "UNCERTAIN", "listed record %s does not verify: %s" % (digest, e.code))
    seen = [v.digest for v in verified]
    if len(seen) != len(set(seen)):
        return Choice(None, "UNCERTAIN", "a record is listed twice")
    choice = choose_baseline(verified, ctx.now, ctx.target_commit, ctx.is_ancestor, ctx.max_age)
    if choice.baseline is None:
        return choice
    # the chosen record has to be fit for this target as well
    raw = fetch(choice.baseline.digest, "full")
    try:
        verify_baseline(raw, choice.baseline.digest + ".json", ctx)
    except VerifyError as e:
        return Choice(None, "BASELINE_REFUSED", e.code)
    return choice
