"""The carry-over rule of mutplan.py on a small graph: every clause of the rule must fail closed.

    python3 -m unittest test_mutplan
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mutplan as mp  # noqa: E402

SCHEMA = "cic-lab/typeddeps/v2"


def graph(f_src="f1", t_src="t1", tests=("t",)):
    nodes = [{"id": "f()", "file": "src/a.rs", "line": 10, "end_line": 20, "test": False, "src_sha256": f_src, "deps": []}]
    for t in tests:
        nodes.append({"id": t, "file": "tests/x.rs", "line": 1, "end_line": 5, "test": True, "entry": True, "src_sha256": t_src,
                      "deps": ["f()"]})
    return {"schema": SCHEMA, "nodes": nodes}


MUT = {"name": "m", "file": "src/a.rs", "span": {"start": {"line": 12}}}


def run(prev, cur, summary="CaughtMutant"):
    return mp.decide(MUT, summary, mp.index(prev), mp.index(cur))


class Rule(unittest.TestCase):
    def test_unchanged_is_carried(self):
        self.assertEqual(run(graph(), graph()), (True, "CARRY"))

    def test_only_caught_is_carried(self):
        for s in ("MissedMutant", "Timeout", "Unviable"):
            self.assertFalse(run(graph(), graph(), s)[0], s)

    def test_code_of_the_function_changed(self):
        self.assertEqual(run(graph(), graph(f_src="f2")), (False, "CODE_CHANGED"))

    def test_a_reaching_test_changed(self):
        self.assertEqual(run(graph(), graph(t_src="t2")), (False, "A_REACHING_TEST_CHANGED"))

    def test_a_reaching_test_lost(self):
        self.assertEqual(run(graph(tests=("t", "u")), graph(tests=("t",))), (False, "A_REACHING_TEST_LOST"))

    def test_a_test_added_only_adds_kills(self):
        self.assertEqual(run(graph(), graph(tests=("t", "u"))), (True, "CARRY"))

    def test_no_test_reaches_it(self):
        self.assertEqual(run(graph(tests=()), graph(tests=())), (False, "NO_TEST_REACHES"))

    def test_the_node_is_gone(self):
        cur = copy.deepcopy(graph())
        cur["nodes"][0]["id"] = "g()"
        cur["nodes"][1]["deps"] = ["g()"]
        self.assertEqual(run(graph(), cur), (False, "NODE_GONE"))

    def test_a_dependency_of_the_function_changed(self):
        p, c = graph(), graph()
        for g, h in ((p, "h1"), (c, "h2")):
            g["nodes"][0]["deps"] = ["h()"]
            g["nodes"].append({"id": "h()", "file": "src/b.rs", "line": 1, "end_line": 3, "test": False, "src_sha256": h, "deps": []})
        self.assertEqual(run(p, c), (False, "DEPENDENCY_CHANGED"))


if __name__ == "__main__":
    unittest.main()
