"""The job-by-job comparison of p1-proof with p1.

    python3 -m unittest test_compare_runs
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_runs as cr  # noqa: E402


def j(name, c):
    return {"name": name, "conclusion": c}


class Compare(unittest.TestCase):
    def test_equal(self):
        rows, bad = cr.compare([j("rust", "success"), j("fuzz", "skipped")], [j("proof / rust", "success"), j("proof / fuzz", "skipped")])
        self.assertEqual(bad, [])
        self.assertEqual(len(rows), 2)

    def test_a_different_conclusion_diverges(self):
        _, bad = cr.compare([j("rust", "success")], [j("proof / rust", "failure")])
        self.assertEqual(bad, [("rust", "success", "failure", False)])

    def test_skipped_is_not_success(self):
        _, bad = cr.compare([j("rust", "success")], [j("proof / rust", "skipped")])
        self.assertEqual(len(bad), 1)

    def test_a_job_one_side_lacks_diverges(self):
        _, bad = cr.compare([j("rust", "success"), j("go", "success")], [j("proof / rust", "success")])
        self.assertEqual([r[0] for r in bad], ["go"])
        _, bad = cr.compare([j("rust", "success")], [j("proof / rust", "success"), j("proof / extra", "success")])
        self.assertEqual([r[0] for r in bad], ["extra"])

    def test_the_jobs_only_p1_proof_has_are_not_compared(self):
        _, bad = cr.compare([j("rust", "success")], [j("proof / rust", "success"), j("proof / proof-plan / plan", "success"),
                                                      j("proof / proof-plan", "success"), j("proof / proof-compare", None)])
        self.assertEqual(bad, [])

    def test_an_unfinished_job_diverges(self):
        _, bad = cr.compare([j("rust", None)], [j("proof / rust", None)])
        self.assertEqual(len(bad), 1)   # no conclusion on either side is no evidence of equality

    def test_matrix_names_are_kept(self):
        _, bad = cr.compare([j("fuzz-target (go A)", "success")], [j("proof / fuzz-target (go A)", "success")])
        self.assertEqual(bad, [])


class Waiting(unittest.TestCase):
    def test_no_p1_run_after_the_grace_is_an_answer_not_a_wait(self):
        self.assertIsNone(cr.gives_up(None, 60, 180, 1500))
        self.assertIn("appeared", cr.gives_up(None, 180, 180, 1500))

    def test_a_run_that_exists_is_waited_for_up_to_the_timeout(self):
        run = {"status": "in_progress"}
        self.assertIsNone(cr.gives_up(run, 1000, 180, 1500))
        self.assertIn("not complete", cr.gives_up(run, 1500, 180, 1500))


class Planner(unittest.TestCase):
    def write(self, content):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_a_missing_file_is_not_zero(self):
        self.assertIsNone(cr.planner_false_reuse("/nonexistent/comparison.json"))
        self.assertIsNone(cr.planner_false_reuse(None))

    def test_broken_or_unexpected_shapes_are_not_zero(self):
        for bad in ("{not json", "{}", '{"false_reuse": 3}', '{"false_reuse": null}'):
            self.assertIsNone(cr.planner_false_reuse(self.write(bad)), bad)

    def test_the_rows_are_counted(self):
        self.assertEqual(cr.planner_false_reuse(self.write('{"false_reuse": []}')), 0)
        self.assertEqual(cr.planner_false_reuse(self.write('{"false_reuse": [{"key": "a"}, {"key": "b"}]}')), 2)


if __name__ == "__main__":
    unittest.main()
