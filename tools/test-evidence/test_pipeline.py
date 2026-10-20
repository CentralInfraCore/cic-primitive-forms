"""Probes of the decisions in the evidence workflow: which slot a run stands for, what a reader of the branch trusts."""
import datetime
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pipeline  # noqa: E402
import publish  # noqa: E402
import test_evidence as te  # noqa: E402
import verify  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 20, 8, 0, 0, tzinfo=UTC)
ENV = {"GITHUB_SHA": te.C1, "GITHUB_REPOSITORY": "CentralInfraCore/cic-primitive-forms", "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ID": "77",
       "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_REF": "CentralInfraCore/cic-primitive-forms/.github/workflows/evidence.yml@refs/heads/main"}


class FakeGit:
    def tree_of(self, c):
        return te.TREE if c == te.C1 else None


class InputTests(unittest.TestCase):
    def inputs(self, event="push", slot=None, **over):
        return pipeline.make_inputs(dict(ENV, **over), event, slot, NOW, FakeGit())

    def refuses(self, kind, **kw):
        with self.assertRaises(pipeline.PipelineError) as cm:
            self.inputs(**kw)
        self.assertEqual(cm.exception.kind, kind)

    def test_a_push(self):
        target, run, slot, trigger = self.inputs()
        self.assertEqual(target, {"kind": "main", "commit": te.C1, "tree": te.TREE, "base_commit": None, "head_commit": None})
        self.assertEqual(run, {"repository": ENV["GITHUB_REPOSITORY"], "workflow_path": ".github/workflows/evidence.yml", "ref": "refs/heads/main",
                               "run_id": 77, "run_attempt": 1, "commit": te.C1})
        self.assertEqual((slot, trigger), (None, "push"))

    def test_the_daily_run_stands_for_today(self):
        _, _, slot, trigger = self.inputs("schedule")
        self.assertEqual((slot, trigger), ("2026-10-20", "dispatch"))
        late = pipeline.make_inputs(ENV, "schedule", None, datetime.datetime(2026, 10, 20, 23, 59, tzinfo=UTC), FakeGit())
        self.assertEqual(late[2], "2026-10-20")

    def test_a_manual_run_stands_for_no_slot_unless_told(self):
        self.assertEqual(self.inputs("workflow_dispatch")[2:], (None, "dispatch"))
        self.assertEqual(self.inputs("workflow_dispatch", slot="2026-10-19")[2], "2026-10-19")
        self.assertEqual(self.inputs("workflow_dispatch", slot="")[2], None)

    def test_a_slot_that_a_run_may_not_stand_in_for(self):
        for slot in ("2026-10-21", "2026-10-16", "2026-02-30", "20261019", "yesterday", "2026-10-19 "):
            with self.subTest(slot):
                self.refuses("SLOT", event="workflow_dispatch", slot=slot)
        self.refuses("SLOT", event="push", slot="2026-10-19")

    def test_only_main_and_only_this_repository(self):
        self.refuses("REF", GITHUB_REF="refs/heads/feature")
        self.refuses("REF", GITHUB_REF="refs/pull/5/merge")
        self.refuses("WORKFLOW_REF", GITHUB_WORKFLOW_REF="Other/repo/.github/workflows/evidence.yml@refs/heads/main")
        self.refuses("WORKFLOW_REF", GITHUB_WORKFLOW_REF="CentralInfraCore/cic-primitive-forms/scripts/x.yml@refs/heads/main")
        self.refuses("WORKFLOW_REF", GITHUB_WORKFLOW_REF="CentralInfraCore/cic-primitive-forms/.github/workflows/evidence.yml")
        self.refuses("EVENT", event="pull_request")

    def test_what_the_run_does_not_say(self):
        for k in ENV:
            env = {a: b for a, b in ENV.items() if a != k}
            with self.subTest(k), self.assertRaises(pipeline.PipelineError) as cm:
                pipeline.make_inputs(env, "push", None, NOW, FakeGit())
            self.assertEqual(cm.exception.kind, "ENVIRONMENT")
        self.refuses("ENVIRONMENT", GITHUB_RUN_ID="seven")

    def test_a_commit_that_is_not_in_the_checkout(self):
        self.refuses("TREE", GITHUB_SHA=te.C3)


class ReadBackTests(unittest.TestCase):
    """The branch as a reader sees it: a real git repository, the verifier of step A, an attestation that can be made to fail."""

    def setUp(self):
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no git")
        self.fx = te.Fixture(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.origin = os.path.join(self.tmp.name, "o.git")
        subprocess.run(["git", "init", "-q", "--bare", self.origin], check=True, capture_output=True)

    def put(self, **kw):
        measured = kw.pop("measured", NOW - datetime.timedelta(hours=3))
        rec = self.fx.record(measured=measured, **kw)
        raw, name = te.raw_of(rec)
        publish.publish(self.origin, name, raw, "full" if rec["outcome"] == "pass" else "audit", measured.date().isoformat())
        return name

    def checkout(self):
        out = os.path.join(self.tmp.name, "co")
        subprocess.run(["rm", "-rf", out], check=True)
        subprocess.run(["git", "clone", "-q", "--branch", "ci-evidence", self.origin, out], check=True, capture_output=True)
        return out

    def ctx(self, **kw):
        kw.setdefault("now", NOW)
        return te.context(self.fx, **kw)

    def test_a_published_record_reads_back(self):
        name = self.put()
        got = pipeline.verify_published(self.checkout(), NOW, self.ctx(), must_contain=name)
        self.assertEqual([v.digest + ".json" for v in got], [name])

    def test_what_was_not_published_is_not_there(self):
        self.put()
        with self.assertRaises(pipeline.PipelineError) as cm:
            pipeline.verify_published(self.checkout(), NOW, self.ctx(), must_contain="0" * 64 + ".json")
        self.assertEqual(cm.exception.kind, "NOT_PUBLISHED")

    def test_a_record_that_is_not_attested_stops_the_reader(self):
        self.put()
        with self.assertRaises(pipeline.PipelineError) as cm:
            pipeline.verify_published(self.checkout(), NOW, self.ctx(att=te.Attested(ok=False)))
        self.assertEqual(cm.exception.kind, "UNVERIFIED")
        self.assertIn("ATTESTATION", str(cm.exception))

    def test_a_record_bound_to_another_tree_stops_the_reader(self):
        self.put()
        with self.assertRaises(pipeline.PipelineError) as cm:
            pipeline.verify_published(self.checkout(), NOW, self.ctx(tree_of=lambda c: "9" * 40))
        self.assertIn("TREE_MISMATCH", str(cm.exception))

    def test_a_listed_record_that_is_gone_stops_the_reader(self):
        name = self.put()
        co = self.checkout()
        os.unlink(os.path.join(co, "evidence", "full", name))
        with self.assertRaises(pipeline.PipelineError) as cm:
            pipeline.verify_published(co, NOW, self.ctx())
        self.assertEqual(cm.exception.kind, "MISSING_RECORD")

    def test_a_record_filed_under_the_wrong_kind(self):
        name = self.put()
        co = self.checkout()
        os.makedirs(os.path.join(co, "evidence", "audit"))
        os.rename(os.path.join(co, "evidence", "full", name), os.path.join(co, "evidence", "audit", name))
        idx = os.path.join(co, "index", "2026-10-20.json")
        with open(idx, "wb") as fh:
            fh.write(publish.index_bytes("2026-10-20", [(name[:-5], "audit")]))
        with self.assertRaises(pipeline.PipelineError) as cm:
            pipeline.verify_published(co, NOW, self.ctx())
        self.assertEqual(cm.exception.kind, "KIND")

    def test_only_the_last_days_are_read(self):
        old = self.put(measured=NOW - datetime.timedelta(days=9))
        co = self.checkout()
        self.assertEqual(pipeline.verify_published(co, NOW, self.ctx(att=te.Attested(ok=False))), [])  # too old to be looked at
        self.assertIsNotNone(old)

    def test_the_watchdog(self):
        self.put(slot="2026-10-19", measured=NOW - datetime.timedelta(days=1, hours=5))
        co = self.checkout()
        self.assertEqual(pipeline.watch(co, NOW, self.ctx()), ("2026-10-20", False))   # today's slot has no record
        self.put(slot="2026-10-20")
        self.assertEqual(pipeline.watch(self.checkout(), NOW, self.ctx()), ("2026-10-20", True))
        early = datetime.datetime(2026, 10, 20, 5, 0, tzinfo=UTC)   # before the slot is due the latest due one is yesterday's
        self.assertEqual(pipeline.watch(self.checkout(), early, self.ctx(now=early)), ("2026-10-19", True))
        self.assertEqual(pipeline.recent_days(NOW, 3), ["2026-10-20", "2026-10-19", "2026-10-18"])

    def test_a_fail_record_does_not_stand_for_a_slot_it_is_still_a_record(self):
        self.put(outcome="fail", slot="2026-10-20")
        self.assertEqual(pipeline.watch(self.checkout(), NOW, self.ctx()), ("2026-10-20", True))   # the run happened; the chain rule judges it


if __name__ == "__main__":
    unittest.main()
