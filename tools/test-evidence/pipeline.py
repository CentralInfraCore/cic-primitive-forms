"""The steps of the evidence workflow that are decisions, not plumbing (docs/EVIDENCE-PUBLISHING.md).

`make_inputs` turns the environment of a workflow run into the target, the run and the slot of a record; `verify_published` reads
the branch back and checks every record of the last days with the verifier of step A, as a reader would; `watch` says whether the
latest due slot has a record. The workflow calls them through evidence.py; the tests call them directly.
"""
import datetime
import re

import publish
import verify

DAY = re.compile(r"\d{4}-\d\d-\d\d\Z")
REF = re.compile(r"refs/(heads|tags)/[^\s]+\Z")


class PipelineError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


def utc_today(now):
    return now.astimezone(datetime.timezone.utc).date()


def check_slot(slot, now, max_age_days=3):
    """A slot a run may stand in for: a real UTC day, not in the future, not older than the baseline window."""
    if not isinstance(slot, str) or not DAY.match(slot):
        raise PipelineError("SLOT", "%r is not a day" % (slot,))
    try:
        day = datetime.date.fromisoformat(slot)
    except ValueError:
        raise PipelineError("SLOT", "%r is not a day" % slot)
    today = utc_today(now)
    if day > today:
        raise PipelineError("SLOT", "%s is in the future" % slot)
    if (today - day).days > max_age_days:
        raise PipelineError("SLOT", "%s is older than %d days" % (slot, max_age_days))
    return slot


def workflow_path(workflow_ref, repository):
    """`owner/repo/.github/workflows/evidence.yml@refs/heads/main` -> `.github/workflows/evidence.yml`, for this repository only."""
    head, at, _ = workflow_ref.partition("@")
    prefix = repository + "/"
    if not at or not head.startswith(prefix) or not head[len(prefix):].startswith(".github/workflows/"):
        raise PipelineError("WORKFLOW_REF", workflow_ref)
    return head[len(prefix):]


def make_inputs(env, event, slot_input, now, git):
    """The target, the run, the slot and the trigger for the record of this workflow run. `env` is the environment of the run.

    A push to main and a manual run stand for no slot; the daily run stands for today's; a manual run may be given the slot it
    stands in for (the watchdog does that). The run is on main only: the signer identity the verifier expects is the workflow
    on `refs/heads/main`."""
    try:
        sha, repo, ref = env["GITHUB_SHA"], env["GITHUB_REPOSITORY"], env["GITHUB_REF"]
        run_id, attempt, wref = int(env["GITHUB_RUN_ID"]), int(env["GITHUB_RUN_ATTEMPT"]), env["GITHUB_WORKFLOW_REF"]
    except (KeyError, ValueError) as e:
        raise PipelineError("ENVIRONMENT", "the workflow run does not say %s" % e)
    if ref != "refs/heads/main":
        raise PipelineError("REF", "evidence is made on refs/heads/main, not %s" % ref)
    if event not in ("push", "schedule", "workflow_dispatch"):
        raise PipelineError("EVENT", event)
    tree = git.tree_of(sha)
    if tree is None:
        raise PipelineError("TREE", "%s is not a commit of the checkout" % sha)
    if event == "schedule":
        slot = utc_today(now).isoformat()
    elif event == "workflow_dispatch" and slot_input:
        slot = check_slot(slot_input, now)
    else:
        slot = None
    if event == "push" and slot_input:
        raise PipelineError("SLOT", "a push stands for no slot")
    target = {"kind": "main", "commit": sha, "tree": tree, "base_commit": None, "head_commit": None}
    run = {"repository": repo, "workflow_path": workflow_path(wref, repo), "ref": ref, "run_id": run_id, "run_attempt": attempt, "commit": sha}
    return target, run, slot, ("push" if event == "push" else "dispatch")


def recent_days(now, days=4):
    today = utc_today(now)
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(days)]


def verify_published(checkout, now, ctx, must_contain=None, days=4):
    """Read the branch as a verifier would. Every record the indexes of the last days list has to be there and genuine
    (canonical, named by its hash, in the schema, bound to its commit, attested by the evidence workflow); `must_contain`
    (the name of the record just published) has to be among them. Returns the verified records, newest last."""
    entries = publish.entries_for(checkout, recent_days(now, days))
    verified = []
    for digest, kind in entries:
        raw = publish.fetch_record(checkout, digest, kind)
        if raw is None:
            raise PipelineError("MISSING_RECORD", "%s/%s is listed and not there" % (kind, digest))
        try:
            v = verify.verify_record(raw, digest + ".json", ctx)
        except verify.VerifyError as e:
            raise PipelineError("UNVERIFIED", "%s/%s: %s %s" % (kind, digest, e.code, e.detail))
        if (v.rec["outcome"] == "pass") != (kind == "full"):
            raise PipelineError("KIND", "%s is filed as %s" % (digest, kind))
        verified.append(v)
    if must_contain and must_contain not in {v.digest + ".json" for v in verified}:
        raise PipelineError("NOT_PUBLISHED", "%s is not in the last days' indexes" % must_contain)
    return sorted(verified, key=lambda v: (v.measured_at, v.digest))


def watch(checkout, now, ctx, days=4):
    """(slot, present): the latest due slot and whether a verified record stands for it."""
    slot = verify.latest_due_slot(now)
    records = verify_published(checkout, now, ctx, days=days)
    return slot, any(v.rec["slot"] == slot and v.rec["target"]["kind"] == "main" for v in records)
