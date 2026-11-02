"""A test the plan skipped is carried, not observed; one it did not skip but nobody observed is dropped.

    python3 -m unittest test_record_carry
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import record as rec  # noqa: E402

CUR = {"go:TestA": {"code_closure_digest": "c", "environment_digest": "e", "data_dependencies_digest": "d"},
       "go:TestB": {"code_closure_digest": "c", "environment_digest": "e", "data_dependencies_digest": "d"}}
PREV = {"go:TestA": dict(CUR["go:TestA"], verdict="pass", provenance={"commit": "old"}), "go:TestB": dict(CUR["go:TestB"], verdict="pass", provenance={"commit": "old"})}
DEC = [{"key": "go:TestA", "status": "REUSE_ELIGIBLE"}, {"key": "go:TestB", "status": "REUSE_ELIGIBLE"}]


class Carried(unittest.TestCase):
    def test_a_skipped_test_is_carried(self):
        rows, summary = rec.classify(DEC, PREV, {"go:TestB": {"outcome": "PASS"}}, {"go:TestA"})
        self.assertEqual([r["kind"] for r in rows], ["CARRIED", "CONSISTENT"])
        self.assertEqual(summary["REUSE_ELIGIBLE"], {"CARRIED": 1, "CONSISTENT": 1})

    def test_an_unobserved_test_the_plan_did_not_skip_is_not_carried(self):
        rows, _ = rec.classify(DEC, PREV, {}, set())
        self.assertEqual({r["kind"] for r in rows}, {"NOT_OBSERVED"})

    def test_only_a_reuse_eligible_test_with_an_earlier_pass_can_be_carried(self):
        rows, _ = rec.classify([{"key": "go:TestA", "status": "RERUN_REQUIRED"}], PREV, {}, {"go:TestA"})
        self.assertEqual(rows[0]["kind"], "NOT_OBSERVED")
        rows, _ = rec.classify(DEC[:1], {"go:TestA": dict(PREV["go:TestA"], verdict="fail")}, {}, {"go:TestA"})
        self.assertEqual(rows[0]["kind"], "NOT_OBSERVED")

    def test_the_evidence_keeps_the_earlier_verdict_and_where_it_was_obtained(self):
        ev = rec.new_evidence(CUR, PREV, {"go:TestB": {"outcome": "PASS"}}, "newsha1234567", skipped={"go:TestA"})
        self.assertEqual(ev["go:TestA"]["verdict"], "pass")
        self.assertEqual(ev["go:TestA"]["provenance"], {"commit": "old"})
        self.assertTrue(ev["go:TestA"]["carried"])
        self.assertEqual(ev["go:TestB"]["provenance"]["commit"], "newsha1234567")
        self.assertNotIn("carried", ev["go:TestB"])

    def test_a_skipped_test_without_an_earlier_pass_leaves_no_evidence(self):
        ev = rec.new_evidence(CUR, {}, {}, "newsha1234567", skipped={"go:TestA"})
        self.assertEqual(ev, {})


if __name__ == "__main__":
    unittest.main()
