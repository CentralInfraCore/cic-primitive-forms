"""Collecting what a FULL run left (docs/EVIDENCE-PUBLISHING.md, "Collecting").

The measuring workflows (`p1`, `corpus`) run on their own when a commit lands on `main`; for the daily run they are
dispatched. This finds those runs for one commit, waits until they have finished, and gathers the conclusions of their jobs
and the artifacts the builder needs. It asks GitHub through a small interface so that every branch (a run that never starts,
one for another commit, one that does not finish, an artifact that has expired) can be exercised without GitHub.

Nothing found is guessed: a run that cannot be found, is for another commit, does not finish in time, or has lost its
artifacts is a `CollectError` and no record is made; the chain rule counts that as a missed run.
"""
import json
import os
import subprocess
import time

WORKFLOWS = ("p1.yml", "corpus.yml")


class CollectError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


def merge_pages(text):
    """`gh api --paginate` prints one JSON document per page, one after the other; the lists they hold are joined."""
    dec, i, merged = json.JSONDecoder(), 0, {}
    text = text.strip()
    while i < len(text):
        page, i = dec.raw_decode(text, i)
        while i < len(text) and text[i].isspace():
            i += 1
        for k, v in page.items():
            merged[k] = merged.get(k, []) + v if isinstance(v, list) else v
    return merged


class Gh:
    def api(self, path, paginate=False):
        raise NotImplementedError

    def dispatch(self, repo, workflow, ref):
        raise NotImplementedError

    def download(self, repo, run_id, name, dest):
        raise NotImplementedError


class GhCli(Gh):
    def __init__(self, gh="gh"):
        self.gh = gh

    def _run(self, argv):
        done = subprocess.run([self.gh] + argv, capture_output=True, timeout=600)
        if done.returncode != 0:
            raise CollectError("GITHUB", "%s: %s" % (" ".join(argv[:3]), done.stderr.decode("utf-8", "replace")[:200]))
        return done.stdout

    def api(self, path, paginate=False):
        out = self._run(["api"] + (["--paginate"] if paginate else []) + [path]).decode("utf-8")
        if not paginate:
            return json.loads(out)
        return merge_pages(out)

    def dispatch(self, repo, workflow, ref):
        self._run(["workflow", "run", workflow, "-R", repo, "--ref", ref])

    def download(self, repo, run_id, name, dest):
        self._run(["run", "download", str(run_id), "-R", repo, "-n", name, "-D", dest])


def latest_run(gh, repo, workflow, sha, event=None, exclude=()):
    """The newest run of the workflow for that commit (optionally of that event), not one of `exclude`; None if there is none."""
    q = "repos/%s/actions/workflows/%s/runs?head_sha=%s&per_page=50" % (repo, workflow, sha)
    if event:
        q += "&event=" + event
    runs = [r for r in gh.api(q).get("workflow_runs", []) if r["id"] not in exclude and r["head_sha"] == sha]
    return max(runs, key=lambda r: (r["created_at"], r["id"])) if runs else None


def find_pushed(gh, repo, workflow, sha, sleep, deadline, now):
    """The run that the push of `sha` started. It may not exist yet for a moment after the push."""
    while True:
        run = latest_run(gh, repo, workflow, sha, event="push")
        if run:
            return run
        if now() >= deadline:
            raise CollectError("NO_RUN", "%s has no run for the push of %s" % (workflow, sha))
        sleep(15)


def find_dispatched(gh, repo, workflow, sha, ref, sleep, now, wait=300):
    """Dispatch the workflow and find the run it started. A run that is for another commit (main moved) is no answer."""
    before = {r["id"] for r in gh.api("repos/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=50" % (repo, workflow)).get("workflow_runs", [])}
    gh.dispatch(repo, workflow, ref)
    deadline = now() + wait
    while True:
        runs = [r for r in gh.api("repos/%s/actions/workflows/%s/runs?event=workflow_dispatch&per_page=50" % (repo, workflow)).get("workflow_runs", [])
                if r["id"] not in before]
        if runs:
            run = max(runs, key=lambda r: (r["created_at"], r["id"]))
            if run["head_sha"] != sha:
                raise CollectError("RUN_FOR_ANOTHER_COMMIT", "%s ran for %s, the commit is %s" % (workflow, run["head_sha"], sha))
            return run
        if now() >= deadline:
            raise CollectError("NO_RUN", "%s was dispatched and no run appeared" % workflow)
        sleep(10)


def wait_finished(gh, repo, run, sleep, now, deadline, poll=60):
    while run["status"] != "completed":
        if now() >= deadline:
            raise CollectError("TIMEOUT", "run %s of %s did not finish" % (run["id"], run.get("name")))
        sleep(poll)
        run = gh.api("repos/%s/actions/runs/%s" % (repo, run["id"]))
    return run


def jobs_of(gh, repo, run):
    jobs = gh.api("repos/%s/actions/runs/%s/jobs?per_page=100" % (repo, run["id"]), paginate=True).get("jobs", [])
    return [{"name": j["name"], "conclusion": j["conclusion"], "html_url": j["html_url"], "run_id": run["id"]} for j in jobs]


def download_artifacts(gh, repo, run, dest):
    arts = gh.api("repos/%s/actions/runs/%s/artifacts?per_page=100" % (repo, run["id"]), paginate=True).get("artifacts", [])
    names = sorted(a["name"] for a in arts)
    if len(names) != len(set(names)):
        raise CollectError("DUPLICATE_ARTIFACT", ", ".join(names))
    for a in arts:
        if a.get("expired"):
            raise CollectError("ARTIFACT_EXPIRED", a["name"])
    for name in names:
        os.makedirs(os.path.join(dest, name), exist_ok=True)
        gh.download(repo, run["id"], name, os.path.join(dest, name))
    return names


def collect(gh, repo, sha, trigger, dest, workflows=WORKFLOWS, ref="main", sleep=time.sleep, now=time.time, wait_for=7200, artifacts_from="p1.yml"):
    """Find the runs of the measuring workflows for `sha` (`trigger` is "push" or "dispatch"), wait for them, and gather their jobs
    and the artifacts of `artifacts_from` into `dest`. Returns the normalised jobs of all the runs."""
    if trigger not in ("push", "dispatch"):
        raise CollectError("TRIGGER", trigger)
    deadline = now() + wait_for
    runs = {}
    for wf in workflows:
        runs[wf] = (find_pushed(gh, repo, wf, sha, sleep, now() + 600, now) if trigger == "push"
                    else find_dispatched(gh, repo, wf, sha, ref, sleep, now))
    jobs, artifacts = [], []
    for wf in workflows:
        run = wait_finished(gh, repo, runs[wf], sleep, now, deadline)
        if run["head_sha"] != sha:
            raise CollectError("RUN_FOR_ANOTHER_COMMIT", "%s: %s" % (wf, run["head_sha"]))
        jobs += jobs_of(gh, repo, run)
        if wf == artifacts_from:
            artifacts = download_artifacts(gh, repo, run, dest)
    return jobs, artifacts, {wf: r["id"] for wf, r in runs.items()}
