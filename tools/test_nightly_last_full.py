#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tests of tools/nightly_last_full.py: which commit the last nightly FULL measured."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nightly_last_full as n  # noqa: E402


def run(i, sha, status="completed"):
    return {"id": i, "head_sha": sha, "status": status}


class LastFull(unittest.TestCase):
    def jobs(self, table):
        return lambda i: [{"name": name, "conclusion": c} for name, c in table.get(i, [])]

    def test_the_newest_run_whose_p1_ran_decides(self):
        runs = [run(5, "e"), run(4, "d"), run(3, "c")]
        jobs = self.jobs({5: [("changed", "success"), ("p1 / scope", "skipped")],      # no development: p1 skipped
                          4: [("changed", "success"), ("p1 / quality", "failure")],     # a failed FULL counts: its incident is open
                          3: [("p1 / quality", "success")]})
        self.assertEqual(n.last_full(runs, jobs, current=99), "d")

    def test_the_current_run_a_running_one_and_a_cancelled_p1_do_not_count(self):
        runs = [run(9, "x"), run(8, "y", status="in_progress"), run(7, "z"), run(6, "w")]
        jobs = self.jobs({9: [("p1 / quality", "success")], 8: [("p1 / quality", "success")],
                          7: [("p1 / quality", "cancelled")], 6: [("p1 / quality", "success")]})
        self.assertEqual(n.last_full(runs, jobs, current=9), "w")

    def test_no_full_at_all_is_none(self):
        self.assertIsNone(n.last_full([run(1, "a")], self.jobs({1: [("changed", "success")]}), current=2))
        self.assertIsNone(n.last_full([], self.jobs({}), current=2))

    def test_one_green_job_of_p1_is_not_a_measurement(self):
        # the review of #108: `scope` is green seconds in; a run cancelled after it, or whose runner was shut down, measured nothing
        runs = [run(3, "c"), run(2, "b"), run(1, "a")]
        jobs = self.jobs({3: [("p1 / scope", "success"), ("p1 / go", "success"), ("p1 / quality", "cancelled")],
                          2: [("p1 / scope", "success"), ("p1 / fast-mutation-rust (3)", "failure"), ("p1 / quality", "skipped")],
                          1: [("p1 / scope", "success"), ("p1 / quality", "success")]})
        self.assertEqual(n.last_full(runs, jobs, current=99), "a")
        self.assertIsNone(n.last_full(runs[:2], jobs, current=99))

    def test_a_failed_quality_job_is_a_verdict_and_not_repeated(self):
        self.assertEqual(n.last_full([run(1, "a")], self.jobs({1: [("p1 / quality", "failure")]}), current=2), "a")


if __name__ == "__main__":
    unittest.main()
