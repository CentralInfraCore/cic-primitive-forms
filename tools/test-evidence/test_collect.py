"""Probes of the run collector against a GitHub that can be made to misbehave."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import collect  # noqa: E402

REPO = "CentralInfraCore/cic-primitive-forms"
SHA = "1" * 40
OTHER = "2" * 40


class Clock:
    def __init__(self):
        self.t = 0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeGh(collect.Gh):
    """Workflows with runs that take `polls` polls to finish; a dispatch creates a run for the head of main."""

    def __init__(self, polls=2):
        self.runs = {}        # id -> run
        self.jobs = {}        # id -> [job]
        self.artifacts = {}   # id -> [artifact]
        self.polls = {}       # id -> polls left
        self.next_id = 100
        self.head = SHA       # head of main
        self.dispatched = []
        self.default_polls = polls
        self.downloads = []
        self.hidden = set()   # workflows whose dispatch starts nothing

    def add_run(self, workflow, sha=SHA, event="push", status="in_progress", conclusion=None, polls=None, created="2026-10-20T03:00:00Z"):
        self.next_id += 1
        rid = self.next_id
        self.runs[rid] = {"id": rid, "name": workflow, "workflow": workflow, "head_sha": sha, "event": event, "status": status,
                          "conclusion": conclusion, "created_at": created}
        self.jobs[rid] = [{"name": "job-%s" % workflow, "conclusion": "success", "html_url": "https://x/%d" % rid}]
        self.artifacts[rid] = []
        self.polls[rid] = self.default_polls if polls is None else polls
        return rid

    def api(self, path, paginate=False):
        if "/actions/workflows/" in path and "/runs" in path:
            wf = path.split("/actions/workflows/")[1].split("/runs")[0]
            q = dict(p.split("=") for p in path.split("?")[1].split("&"))
            runs = [r for r in self.runs.values() if r["workflow"] == wf and (("head_sha" not in q) or r["head_sha"] == q["head_sha"])
                    and (("event" not in q) or r["event"] == q["event"])]
            return {"workflow_runs": runs}
        if path.endswith("/jobs?per_page=100"):
            rid = int(path.split("/runs/")[1].split("/")[0])
            return {"jobs": self.jobs[rid]}
        if path.endswith("/artifacts?per_page=100"):
            rid = int(path.split("/runs/")[1].split("/")[0])
            return {"artifacts": self.artifacts[rid]}
        if "/actions/runs/" in path:
            rid = int(path.split("/runs/")[1])
            run = self.runs[rid]
            if run["status"] != "completed":
                self.polls[rid] -= 1
                if self.polls[rid] <= 0:
                    run["status"], run["conclusion"] = "completed", "success"
            return dict(run)
        raise AssertionError(path)

    def dispatch(self, repo, workflow, ref):
        self.dispatched.append((workflow, ref))
        if workflow not in self.hidden:
            self.add_run(workflow, sha=self.head, event="workflow_dispatch", created="2026-10-20T03:10:00Z")

    def download(self, repo, run_id, name, dest):
        self.downloads.append((run_id, name))
        with open(os.path.join(dest, "marker"), "w") as fh:
            fh.write(name)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGh()
        self.clock = Clock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def collect(self, trigger="push", **kw):
        return collect.collect(self.gh, REPO, SHA, trigger, self.tmp.name, sleep=self.clock.sleep, now=self.clock.now, **kw)

    def push_runs(self):
        p1 = self.gh.add_run("p1.yml")
        co = self.gh.add_run("corpus.yml")
        self.gh.artifacts[p1] = [{"name": "lines", "expired": False}, {"name": "go-ledger-0", "expired": False}]
        return p1, co

    def test_the_runs_of_a_push(self):
        p1, co = self.push_runs()
        jobs, arts, ids = self.collect()
        self.assertEqual(ids, {"p1.yml": p1, "corpus.yml": co})
        self.assertEqual(sorted(j["name"] for j in jobs), ["job-corpus.yml", "job-p1.yml"])
        self.assertEqual(arts, ["go-ledger-0", "lines"])
        self.assertEqual(sorted(self.gh.downloads), [(p1, "go-ledger-0"), (p1, "lines")])  # only the artifacts of p1
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "lines", "marker")))
        self.assertGreater(self.clock.t, 0)  # it waited for the runs
        self.assertEqual({j["run_id"] for j in jobs}, {p1, co})

    def test_a_run_that_has_not_started_yet_is_waited_for(self):
        orig = self.gh.api
        calls = {"n": 0}

        def late(path, paginate=False):
            if "/runs?head_sha" in path:
                calls["n"] += 1
                if calls["n"] == 3:
                    self.push_runs()
            return orig(path, paginate)
        self.gh.api = late
        jobs, _, _ = self.collect()
        self.assertTrue(jobs)

    def test_no_run_for_the_push(self):
        self.gh.add_run("p1.yml")  # but none for corpus
        with self.assertRaises(collect.CollectError) as cm:
            self.collect()
        self.assertEqual(cm.exception.kind, "NO_RUN")

    def test_runs_for_another_commit_are_not_used(self):
        self.gh.add_run("p1.yml", sha=OTHER)
        self.gh.add_run("corpus.yml", sha=OTHER)
        with self.assertRaises(collect.CollectError) as cm:
            self.collect()
        self.assertEqual(cm.exception.kind, "NO_RUN")

    def test_the_newest_run_of_the_commit_wins(self):
        old = self.gh.add_run("p1.yml", status="completed", conclusion="failure", created="2026-10-20T01:00:00Z")
        new = self.gh.add_run("p1.yml", created="2026-10-20T02:00:00Z")
        self.gh.add_run("corpus.yml")
        self.gh.artifacts[new] = []
        _, _, ids = self.collect()
        self.assertEqual(ids["p1.yml"], new)
        self.assertNotEqual(ids["p1.yml"], old)

    def test_a_run_that_does_not_finish(self):
        self.push_runs()
        for rid in self.gh.polls:
            self.gh.polls[rid] = 10**6
        with self.assertRaises(collect.CollectError) as cm:
            self.collect(wait_for=600)
        self.assertEqual(cm.exception.kind, "TIMEOUT")

    def test_a_run_whose_head_changed_under_it(self):
        p1, co = self.push_runs()
        orig = self.gh.api

        def moved(path, paginate=False):
            out = orig(path, paginate)
            if path.endswith("/runs/%d" % p1):
                out["head_sha"] = OTHER
            return out
        self.gh.api = moved
        with self.assertRaises(collect.CollectError) as cm:
            self.collect()
        self.assertEqual(cm.exception.kind, "RUN_FOR_ANOTHER_COMMIT")

    def test_expired_and_duplicated_artifacts(self):
        p1, _ = self.push_runs()
        self.gh.artifacts[p1][0]["expired"] = True
        with self.assertRaises(collect.CollectError) as cm:
            self.collect()
        self.assertEqual(cm.exception.kind, "ARTIFACT_EXPIRED")
        self.gh.artifacts[p1][0]["expired"] = False
        self.gh.artifacts[p1].append({"name": "lines", "expired": False})
        with self.assertRaises(collect.CollectError) as cm:
            self.collect()
        self.assertEqual(cm.exception.kind, "DUPLICATE_ARTIFACT")

    def test_the_daily_run_dispatches_and_finds_what_it_started(self):
        self.gh.add_run("p1.yml", event="workflow_dispatch", status="completed", conclusion="success", created="2026-10-19T03:00:00Z")  # yesterday's
        self.gh.artifacts[101] = []
        jobs, _, ids = self.collect("dispatch")
        self.assertEqual(self.gh.dispatched, [("p1.yml", "main"), ("corpus.yml", "main")])
        self.assertNotIn(101, ids.values())              # yesterday's run is not today's
        self.assertEqual({j["run_id"] for j in jobs}, set(ids.values()))

    def test_a_dispatch_that_starts_nothing(self):
        self.gh.hidden.add("corpus.yml")
        with self.assertRaises(collect.CollectError) as cm:
            self.collect("dispatch")
        self.assertEqual(cm.exception.kind, "NO_RUN")

    def test_main_moved_before_the_dispatch_took_effect(self):
        self.gh.head = OTHER
        with self.assertRaises(collect.CollectError) as cm:
            self.collect("dispatch")
        self.assertEqual(cm.exception.kind, "RUN_FOR_ANOTHER_COMMIT")

    def test_an_unknown_trigger(self):
        with self.assertRaises(collect.CollectError) as cm:
            self.collect("schedule")
        self.assertEqual(cm.exception.kind, "TRIGGER")

    def test_unfinished_jobs_are_reported_as_they_are(self):
        p1, co = self.push_runs()
        self.gh.jobs[p1][0]["conclusion"] = None
        jobs, _, _ = self.collect()
        self.assertIn(None, [j["conclusion"] for j in jobs])  # the builder refuses it (JOB_NOT_FINISHED); nothing is invented here


if __name__ == "__main__":
    unittest.main()
