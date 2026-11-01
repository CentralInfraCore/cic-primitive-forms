"""shadow_check must report a REUSE_ELIGIBLE decision that the observed outcomes contradict.

    python3 -m unittest lab/measure/test_shadow_check.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from shadow_check import classify  # noqa: E402

PARENT = {"c1": "c0"}


def verdicts(parent, child):
    return {"c0": {"go": {"TestX": parent}}, "c1": {"go": {"TestX": child}}}


def run(status, parent, child):
    tally, false_reuse, flaky = classify(["c1 go:go.TestX %s ALL_CONDITIONS_HOLD" % status], verdicts(parent, child), PARENT)
    return dict(tally[("go", status)]), false_reuse, flaky


class Sensitivity(unittest.TestCase):
    def test_a_deliberately_wrong_reuse_is_reported(self):
        tally, false_reuse, _ = run("REUSE_ELIGIBLE", ["PASS", "PASS"], ["FAIL", "FAIL"])
        self.assertEqual(tally, {"FALSE_REUSE": 1})
        self.assertEqual(len(false_reuse), 1)

    def test_a_right_reuse_is_consistent(self):
        self.assertEqual(run("REUSE_ELIGIBLE", ["PASS", "PASS"], ["PASS", "PASS"])[0], {"CONSISTENT": 1})

    def test_an_unstable_child_is_not_a_false_reuse(self):
        tally, false_reuse, flaky = run("REUSE_ELIGIBLE", ["PASS", "PASS"], ["PASS", "FAIL"])
        self.assertEqual((tally, false_reuse, flaky), ({"NONDETERMINISTIC": 1}, [], {"TestX"}))

    def test_a_rerun_is_not_judged_a_false_reuse_but_its_value_is_counted(self):
        self.assertEqual(run("RERUN_REQUIRED", ["PASS", "PASS"], ["FAIL", "FAIL"])[0], {"OUTCOME_CHANGED": 1})
        self.assertEqual(run("UNVERIFIABLE", ["PASS", "PASS"], ["PASS", "PASS"])[0], {"SAME_OUTCOME": 1})


if __name__ == "__main__":
    unittest.main()
