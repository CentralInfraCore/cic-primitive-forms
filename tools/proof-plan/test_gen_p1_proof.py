"""p1-proof.yml is what the generator makes from p1.yml, and the job graph is p1's.

    python3 -m unittest test_gen_p1_proof
"""
import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_p1_proof as g  # noqa: E402

P1 = yaml.safe_load(open(g.SRC))
PROOF = yaml.safe_load(open(g.DST))


class Generated(unittest.TestCase):
    def test_the_file_in_the_repository_is_what_the_generator_makes(self):
        self.assertEqual(g.main(["--check"]), 0)

    def test_same_job_graph(self):
        self.assertEqual(list(P1["jobs"]), list(PROOF["jobs"]))
        for k, j in P1["jobs"].items():
            p = PROOF["jobs"][k]
            for field in ("needs", "if", "strategy", "runs-on", "steps", "outputs", "timeout-minutes"):
                self.assertEqual(j.get(field), p.get(field), (k, field))

    def test_every_job_is_named_apart_from_the_required_checks(self):
        for k, j in PROOF["jobs"].items():
            self.assertTrue(j["name"].startswith("proof / "), k)
            self.assertNotEqual(j["name"], k)

    def test_matrix_job_name_is_kept_after_the_prefix(self):
        self.assertEqual(PROOF["jobs"]["fuzz-target"]["name"], "proof / " + P1["jobs"]["fuzz-target"]["name"])

    def test_triggers(self):
        on = PROOF[True] if True in PROOF else PROOF["on"]  # PyYAML reads the key `on` as True
        self.assertNotIn("workflow_call", on)
        self.assertEqual(set(on), {"workflow_dispatch", "push", "pull_request"})

    def test_inherit_asks_about_itself(self):
        w = PROOF["jobs"]["inherit"]["with"]
        self.assertEqual((w["caller"], w["workflows"]), ("p1-proof", "p1-proof.yml,corpus.yml"))

    def test_an_unknown_shape_of_p1_is_an_error(self):
        with self.assertRaises(g.Unsupported):
            g.generate("name: other\njobs: {}\n")
        with self.assertRaises(g.Unsupported):
            g.generate(open(g.SRC).read().replace("      caller: p1\n", "      caller: p1\n      caller: p1\n"))


if __name__ == "__main__":
    unittest.main()
