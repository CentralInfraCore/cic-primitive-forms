#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""tools/go_list_count.py: the count of Go mutants in the diff is believed only from a finished list run; a crash is not "no mutants"."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import go_list_count  # noqa: E402


def output(passed=0, skipped=0, unknown=0, failed=0, summary=True, claim=None):
    kinds = ["PASS"] * passed + ["SKIP"] * skipped + ["UNKOWN exit code for"] * unknown + ["FAIL"] * failed
    rows = ['%s "/tmp/go-mutesting-1/a.go.%d" with checksum %032x' % (k, i, i) for i, k in enumerate(kinds)]
    s = dict(passed=passed, failed=failed, skipped=skipped, total=len(rows))
    s.update(claim or {})
    tail = ("The mutation score is 1.000000 (%d passed, %d failed, 0 duplicated, %d skipped, total is %d)\n" % (s["passed"], s["failed"], s["skipped"], s["total"])) if summary else ""
    return "\n".join(rows) + "\n" + tail


class Count(unittest.TestCase):
    def test_a_finished_run_is_counted(self):
        self.assertEqual(go_list_count.count(output(passed=7, skipped=109)), 7)

    def test_zero_is_believed_from_a_finished_run_only(self):
        self.assertEqual(go_list_count.count(output(skipped=40)), 0)
        self.assertEqual(go_list_count.count(output()), 0)          # no Go file in the diff mutates nothing, and says so
        with self.assertRaises(ValueError):
            go_list_count.count("")                                  # a crash that printed nothing
        with self.assertRaises(ValueError):
            go_list_count.count(output(skipped=40, summary=False))   # a run that stopped before its summary

    def test_a_mutant_the_executor_could_not_place_is_not_a_skipped_one(self):
        with self.assertRaises(ValueError):
            go_list_count.count(output(passed=3, skipped=5, unknown=1, claim={"skipped": 5, "total": 9}))

    def test_a_failure_in_a_mode_that_runs_no_test_is_refused(self):
        with self.assertRaises(ValueError):
            go_list_count.count(output(passed=3, failed=1))

    def test_a_summary_that_disagrees_with_the_result_lines_is_refused(self):
        with self.assertRaises(ValueError):
            go_list_count.count(output(passed=3, skipped=5, claim={"passed": 4}))
        with self.assertRaises(ValueError):
            go_list_count.count(output(passed=3, skipped=5, claim={"total": 9}))

    def test_the_colour_codes_of_go_mutesting_do_not_matter(self):
        self.assertEqual(go_list_count.count(output(passed=2, skipped=1).replace('PASS "', "\x1b[97;42mPASS\x1b[0m \"")), 2)

    def test_the_command_prints_the_number_or_fails_with_nothing_on_stdout(self):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "go_list_count.py")
        with tempfile.TemporaryDirectory() as d:
            good, bad = os.path.join(d, "good"), os.path.join(d, "bad")
            with open(good, "w") as fh:
                fh.write(output(passed=4, skipped=2))
            with open(bad, "w") as fh:
                fh.write(output(passed=4, summary=False))
            r = subprocess.run([sys.executable, script, good], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "4"))
            r = subprocess.run([sys.executable, script, bad], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (1, ""))
            r = subprocess.run([sys.executable, script, os.path.join(d, "absent")], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (1, ""))


if __name__ == "__main__":
    unittest.main()
