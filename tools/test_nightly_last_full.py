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
                          7: [("p1 / quality", "cancelled")], 6: [("p1 / go", "success")]})
        self.assertEqual(n.last_full(runs, jobs, current=9), "w")

    def test_no_full_at_all_is_none(self):
        self.assertIsNone(n.last_full([run(1, "a")], self.jobs({1: [("changed", "success")]}), current=2))
        self.assertIsNone(n.last_full([], self.jobs({}), current=2))


if __name__ == "__main__":
    unittest.main()
