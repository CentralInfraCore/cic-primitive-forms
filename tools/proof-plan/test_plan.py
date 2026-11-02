"""The pure parts of the planner and of the recorder.

    python3 -m unittest tools/proof-plan/test_plan.py
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence as ev  # noqa: E402
import find_prev  # noqa: E402
import plan as pl  # noqa: E402
import record as rec  # noqa: E402

BASE = {
    "contract_version": ev.CONTRACT_V2, "rule_version": "cic-lab/reuse-rules/v1", "subject": {"id": "go:TestX", "kind": "test"},
    "verdict": "pass", "code_closure_digest": "code-1", "data_dependencies_digest": "d", "environment_digest": "e",
    "graph": {"extractor": "typeddeps:x", "subject_in_graph": True, "constructs": ["go.func"], "opaque": [],
              "analysis": {"status": "COMPLETE", "unresolved": []}},
    "opaque_reads": [], "provenance": {"evidence_id": "a", "commit": "a"},
}
A = ev.Assurance.of([ev.CONTRACT_V2], ["cic-lab/reuse-rules/v1"], {"typeddeps:x": ["go.func"]})


def cur(**kw):
    e = copy.deepcopy(BASE)
    e.pop("verdict")
    e.update(kw)
    return e


class Decide(unittest.TestCase):
    def test_no_previous_evidence_is_no_evidence_not_a_kernel_decision(self):
        d = pl.decide({}, {"go:TestX": cur()}, A)
        self.assertEqual((d[0]["status"], d[0]["reasons"]), ("NO_EVIDENCE", ["NO_PREVIOUS_EVIDENCE"]))

    def test_the_kernel_decides_where_there_is_evidence(self):
        self.assertEqual(pl.decide({"go:TestX": BASE}, {"go:TestX": cur()}, A)[0]["status"], "REUSE_ELIGIBLE")
        self.assertEqual(pl.decide({"go:TestX": BASE}, {"go:TestX": cur(code_closure_digest="code-2")}, A)[0]["status"], "RERUN_REQUIRED")
        self.assertEqual(pl.decide({"go:TestX": dict(BASE, stability="unstable")}, {"go:TestX": cur()}, A)[0]["status"], "UNVERIFIABLE")

    def test_the_runner_key_of_a_rust_test(self):
        self.assertEqual(pl.runner_key("tests/probe::t_macro()."), "probe::t_macro")
        self.assertEqual(pl.runner_key("diff/tests/gone()."), "cic_primitive_forms::diff::tests::gone")

    def test_a_command_or_data_change_changes_the_digest(self):
        self.assertNotEqual(pl.sha("go", "a"), pl.sha("go", "b"))
        self.assertTrue(pl.is_data("testdata/x.yaml") and pl.is_data("corpus/v.yaml"))
        self.assertFalse(pl.is_data("go/a.go") or pl.is_data("rust/Cargo.lock") or pl.is_data("docs/x.md"))


class Trust(unittest.TestCase):
    def test_trust_comes_from_the_run_not_from_the_artifact(self):
        self.assertEqual(find_prev.trust_of({"headBranch": "main", "event": "push"}), "main")
        for r in ({"headBranch": "main", "event": "workflow_dispatch"}, {"headBranch": "feat/x", "event": "push"},
                  {"headBranch": "feat/x", "event": "pull_request"}, {}):
            self.assertEqual(find_prev.trust_of(r), "branch")

    def test_skipping_needs_both_the_evidence_and_the_planner_from_main(self):
        main = {"trust": "main", "branch": "main"}
        self.assertEqual(pl.authority(main, "main"), (True, ""))
        ok, why = pl.authority(main, "branch")
        self.assertFalse(ok)
        self.assertIn("planner", why)
        ok, why = pl.authority({"trust": "branch", "branch": "feat/x"}, "main")
        self.assertFalse(ok)
        self.assertIn("feat/x", why)
        self.assertFalse(pl.authority({"trust": "none"}, "main")[0])
        self.assertFalse(pl.authority({}, "main")[0])  # nothing known is the least trust, not the most

    def test_missing_or_broken_source_is_no_trust(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pl.load_source(d), {"trust": "none"})
            open(os.path.join(d, "source.json"), "w").write("not json")
            self.assertEqual(pl.load_source(d), {"trust": "none"})


class TreeStateTests(unittest.TestCase):
    def repo(self, d):
        import subprocess
        def git(*args):
            subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), check=True, capture_output=True)
        git("init", "-q")
        os.makedirs(os.path.join(d, "corpus"))
        open(os.path.join(d, "corpus", "v.yaml"), "w").write("a\n")
        open(os.path.join(d, "corpus", "w.yaml"), "w").write("w\n")
        git("add", "-A")
        git("commit", "-q", "-m", "x")
        return git

    def test_a_file_changed_after_the_last_add_is_seen_as_changed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.repo(d)
            reads = {"corpus/v.yaml": "path", "corpus": "dir"}
            before = pl.reads_digest(pl.TreeState(d), reads)
            open(os.path.join(d, "corpus", "v.yaml"), "a").write("more\n")  # not git-added
            after = pl.reads_digest(pl.TreeState(d), reads)
            self.assertNotEqual(before, after)
            self.assertEqual(pl.reads_digest(pl.TreeState(d), {"corpus/w.yaml": "path"}), pl.reads_digest(pl.TreeState(d), {"corpus/w.yaml": "path"}))

    def test_what_a_test_did_not_read_does_not_move_its_digest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.repo(d)
            reads = {"corpus/w.yaml": "path"}
            before = pl.reads_digest(pl.TreeState(d), reads)
            open(os.path.join(d, "corpus", "v.yaml"), "a").write("more\n")
            self.assertEqual(before, pl.reads_digest(pl.TreeState(d), reads))

    def test_a_deleted_file_and_a_new_tracked_file_in_a_listed_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            git = self.repo(d)
            listing = {"corpus": "dir"}
            before = pl.reads_digest(pl.TreeState(d), listing)
            os.remove(os.path.join(d, "corpus", "w.yaml"))
            self.assertNotEqual(before, pl.reads_digest(pl.TreeState(d), {"corpus/w.yaml": "path"} | {"corpus": "dir"}) )
            open(os.path.join(d, "corpus", "n.yaml"), "w").write("n\n")
            git("add", "corpus/n.yaml")
            self.assertNotEqual(before, pl.reads_digest(pl.TreeState(d), listing))


class QualifyCache(unittest.TestCase):
    def test_the_probe_digest_follows_the_probes_and_nothing_else(self):
        self.assertEqual(pl.probe_digest(), pl.probe_digest())
        self.assertEqual(len(pl.probe_digest()), 64)

    def test_a_cache_for_other_builds_is_ignored_and_one_for_these_is_used(self):
        import argparse, tempfile, json as _json
        calls = []
        orig = (pl.qgo.qualify, pl.qrs.qualify)
        pl.qgo.qualify = lambda *a, **k: calls.append("go") or frozenset({"go.func"})
        pl.qrs.qualify = lambda *a, **k: calls.append("rust") or frozenset({"rs.fn"})
        try:
            a = argparse.Namespace(typeddeps="t", scipdeps="s", rust_toolchain="1.99.1")
            tc = {"rustc": "rustc x", "rust-analyzer": "ra x"}
            ids = {"go": ("G1", {}), "rust": ("R1", {})}
            with tempfile.TemporaryDirectory() as d:
                f = os.path.join(d, "q.json")
                q, _, hit = pl.qualify_cached(a, ids, tc, f)
                self.assertEqual((hit, calls), (False, ["go", "rust"]))
                q, _, hit = pl.qualify_cached(a, ids, tc, f)
                self.assertEqual((hit, calls, q["G1"]), (True, ["go", "rust"], frozenset({"go.func"})))  # nothing was measured again
                q, _, hit = pl.qualify_cached(a, {"go": ("G2", {}), "rust": ("R1", {})}, tc, f)  # another go binary: not this file's
                self.assertEqual((hit, calls), (False, ["go", "rust", "go", "rust"]))
                q, _, hit = pl.qualify_cached(a, ids, {"rustc": "rustc y", "rust-analyzer": "ra x"}, f)  # another compiler
                self.assertFalse(hit)
                open(f, "w").write("not json")
                self.assertFalse(pl.qualify_cached(a, ids, tc, f)[2])
        finally:
            pl.qgo.qualify, pl.qrs.qualify = orig


class Record(unittest.TestCase):
    def rows(self, status, prev_verdict, obs):
        dec = [{"key": "go:TestX", "status": status, "reasons": []}]
        prev = {"go:TestX": dict(BASE, verdict=prev_verdict)} if prev_verdict else {}
        return rec.classify(dec, prev, {"go:TestX": {"outcome": obs}})

    def test_a_wrong_reuse_is_reported(self):
        rows, summary = self.rows("REUSE_ELIGIBLE", "pass", "FAIL")
        self.assertEqual(rows[0]["kind"], "FALSE_REUSE")
        self.assertEqual(self.rows("REUSE_ELIGIBLE", "pass", "PASS")[0][0]["kind"], "CONSISTENT")

    def test_what_a_rerun_bought(self):
        self.assertEqual(self.rows("RERUN_REQUIRED", "pass", "PASS")[0][0]["kind"], "SAME_OUTCOME")
        self.assertEqual(self.rows("RERUN_REQUIRED", "pass", "FAIL")[0][0]["kind"], "OUTCOME_CHANGED")
        self.assertEqual(self.rows("NO_EVIDENCE", None, "PASS")[0][0]["kind"], "NO_PREVIOUS")

    def test_a_flip_with_the_same_key_marks_the_test_unstable_and_it_stays_so(self):
        c = {"go:TestX": cur()}
        e = rec.new_evidence(c, {"go:TestX": BASE}, {"go:TestX": {"outcome": "FAIL"}}, "sha")
        self.assertEqual((e["go:TestX"]["verdict"], e["go:TestX"].get("stability")), ("fail", "unstable"))
        e2 = rec.new_evidence(c, e, {"go:TestX": {"outcome": "PASS"}}, "sha2")
        self.assertEqual(e2["go:TestX"].get("stability"), "unstable")
        e3 = rec.new_evidence({"go:TestX": cur(code_closure_digest="code-2")}, {"go:TestX": BASE}, {"go:TestX": {"outcome": "FAIL"}}, "sha")
        self.assertNotIn("stability", e3["go:TestX"])  # the code moved: a new outcome is no flip

    def test_a_test_that_was_not_observed_leaves_no_evidence(self):
        self.assertEqual(rec.new_evidence({"go:TestX": cur()}, {}, {}, "sha"), {})


class FindPrev(unittest.TestCase):
    def test_the_newest_ancestor_with_a_success_is_taken(self):
        runs = [{"databaseId": 3, "headSha": "c", "conclusion": "success"}, {"databaseId": 2, "headSha": "b", "conclusion": "success"},
                {"databaseId": 1, "headSha": "a", "conclusion": "success"}]
        self.assertEqual(find_prev.pick(runs, lambda s: s in ("a", "b"))["databaseId"], 2)
        self.assertIsNone(find_prev.pick(runs, lambda s: False))


class Locate(unittest.TestCase):
    def test_evidence_json_is_found_where_the_artifact_put_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(find_prev.locate(d))
            os.makedirs(os.path.join(d, "out"))
            open(os.path.join(d, "out", "evidence.json"), "w").write("{}")
            self.assertTrue(find_prev.locate(d))
            self.assertTrue(os.path.exists(os.path.join(d, "evidence.json")))
        with tempfile.TemporaryDirectory() as d:  # at the root already
            open(os.path.join(d, "evidence.json"), "w").write("{}")
            self.assertTrue(find_prev.locate(d))


if __name__ == "__main__":
    unittest.main()
