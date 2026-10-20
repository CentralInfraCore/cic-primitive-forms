"""Publishing a record to the `ci-evidence` branch, and reading the branch back (docs/EVIDENCE-PUBLISHING.md).

Layout: `evidence/full/<hash>.json` (anchors), `evidence/audit/<hash>.json` (failed runs), `index/<date>.json` (what was
published on a UTC day, in canonical JSON). A record is named by its own hash, so a file that exists is the file or it is
corruption (`CONFLICT`). Publishing never forces anything: a push that is rejected because someone else published first is
retried on top of the new tip, any other refusal (a ruleset, a hook, a missing right) is an error. The credentials of the
publisher go in the environment of git, never in its arguments.
"""
import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

import canon

BRANCH = "ci-evidence"
INDEX_SCHEMA = "test-evidence-index/1"
HASH = re.compile(r"[0-9a-f]{64}\Z")
DAY = re.compile(r"\d{4}-\d\d-\d\d\Z")
KINDS = {"full": "evidence/full", "audit": "evidence/audit"}


class PublishError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


# --- the index -----------------------------------------------------------------------------------------------------

def index_bytes(date, records):
    """Canonical bytes of an index; `records` is a list of (hash, kind), kept sorted and without repeats."""
    entries = sorted({(h, k) for h, k in records}, key=lambda e: (e[1], e[0]))
    return canon.canonicalize({"schema": INDEX_SCHEMA, "date": date, "records": [{"hash": h, "kind": k} for h, k in entries]})


def read_index(data):
    """The (hash, kind) pairs and the date of an index; anything that is not exactly an index written by `index_bytes` is refused."""
    try:
        doc = canon.read_canonical(data)
    except canon.CanonError as e:
        raise PublishError("BAD_INDEX", e.kind)
    if not (isinstance(doc, dict) and set(doc) == {"schema", "date", "records"} and doc["schema"] == INDEX_SCHEMA
            and isinstance(doc["date"], str) and DAY.match(doc["date"]) and isinstance(doc["records"], list)):
        raise PublishError("BAD_INDEX", "not an index")
    out = []
    for r in doc["records"]:
        if not (isinstance(r, dict) and set(r) == {"hash", "kind"} and isinstance(r["hash"], str) and HASH.match(r["hash"]) and r["kind"] in KINDS):
            raise PublishError("BAD_INDEX", "a bad entry")
        out.append((r["hash"], r["kind"]))
    if index_bytes(doc["date"], out) != data:
        raise PublishError("BAD_INDEX", "entries out of order or repeated")
    return doc["date"], out


def entries_for(checkout, dates):
    """Every (hash, kind) the indexes of those UTC days list."""
    found = []
    for d in dates:
        path = os.path.join(checkout, "index", d + ".json")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            date, entries = read_index(fh.read())
        if date != d:
            raise PublishError("BAD_INDEX", "%s says %s" % (path, date))
        found += entries
    return found


def fetch_record(checkout, digest, kind):
    path = os.path.join(checkout, KINDS[kind], digest + ".json")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


# --- git -----------------------------------------------------------------------------------------------------------

def auth_env(token):
    """The environment that gives git a token without putting it in an argument."""
    if not token:
        return {}
    basic = base64.b64encode(("x-access-token:%s" % token).encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraheader", "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic %s" % basic}


class Git:
    """git, run in a directory, with a clean configuration: no hooks, no signing, no global or system file."""

    def __init__(self, cwd, env_extra=None, run=subprocess.run):
        self.cwd = cwd
        self.run = run
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0")
        self.env.update(env_extra or {})

    def __call__(self, *args, check=True):
        argv = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false"] + list(args)
        done = self.run(argv, cwd=self.cwd, env=self.env, capture_output=True, timeout=300)
        out, err = done.stdout.decode("utf-8", "replace").strip(), done.stderr.decode("utf-8", "replace").strip()
        if check and done.returncode != 0:
            raise PublishError("GIT", "%s: %s" % (" ".join(args[:2]), err[:300]))
        return done.returncode, out, err


def _rejected(err):
    return any(s in err for s in ("non-fast-forward", "fetch first", "failed to update ref", "cannot lock ref"))


def publish(origin, name, raw, kind, date, identity=("evidence", "evidence@users.noreply.github.com"), token=None,
            branch=BRANCH, retries=5, run=subprocess.run):
    """Add the record and the index entry to the branch and push, without force. Returns the commit that was pushed."""
    if kind not in KINDS or not DAY.match(date or ""):
        raise PublishError("ARGUMENT", "kind %r, date %r" % (kind, date))
    stem = name[:-5] if name.endswith(".json") else ""
    if not HASH.match(stem) or hashlib.sha256(raw).hexdigest() != stem:
        raise PublishError("NAME", "%s is not the hash of the record" % name)
    try:
        rec = canon.read_canonical(raw)
    except canon.CanonError as e:
        raise PublishError("NOT_CANONICAL", e.kind)
    if not isinstance(rec, dict) or (rec.get("outcome") == "pass") != (kind == "full"):
        raise PublishError("KIND", "a record of outcome %r is not a %s record" % (rec.get("outcome") if isinstance(rec, dict) else None, kind))
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "branch")
        os.makedirs(work)
        git = Git(work, auth_env(token), run=run)
        for attempt in range(retries):
            _checkout(git, origin, branch, work)
            _write(work, stem, raw, kind, date)
            git("add", "evidence", "index")
            if git("diff", "--cached", "--quiet", check=False)[0] == 0:   # the record and its index entry are there already
                return git("rev-parse", "HEAD")[1]
            git("-c", "user.name=%s" % identity[0], "-c", "user.email=%s" % identity[1], "commit", "-q", "-m",
                "evidence: %s %s (%s)" % (kind, stem[:12], date))
            rc, _, err = git("push", "origin", "HEAD:refs/heads/%s" % branch, check=False)
            if rc == 0:
                return git("rev-parse", "HEAD")[1]
            if not _rejected(err):
                raise PublishError("PUSH", err[:300])
            _clear(work)
        raise PublishError("RACE", "the branch moved %d times while publishing" % retries)


def _clear(work):
    for entry in os.listdir(work):
        path = os.path.join(work, entry)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)


def _checkout(git, origin, branch, work):
    rc, _, err = git("clone", "-q", "--no-tags", "--single-branch", "--branch", branch, origin, ".", check=False)
    if rc == 0:
        return
    if "not found" in err or "Remote branch" in err:
        git("init", "-q")
        git("checkout", "-q", "--orphan", branch)
        git("remote", "add", "origin", origin)
        return
    raise PublishError("CLONE", err[:300])


def _write(work, stem, raw, kind, date):
    path = os.path.join(work, KINDS[kind], stem + ".json")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            if fh.read() != raw:
                raise PublishError("CONFLICT", "%s exists with other content" % path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
    ipath = os.path.join(work, "index", date + ".json")
    entries = []
    if os.path.exists(ipath):
        with open(ipath, "rb") as fh:
            d, entries = read_index(fh.read())
        if d != date:
            raise PublishError("BAD_INDEX", "%s says %s" % (ipath, d))
    os.makedirs(os.path.dirname(ipath), exist_ok=True)
    with open(ipath, "wb") as fh:
        fh.write(index_bytes(date, entries + [(stem, kind)]))
