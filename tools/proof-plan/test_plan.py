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
