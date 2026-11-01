"""The classification of iota_exposure.py: a test with the same Merkle key on both commits is clean, exposed, a semantic change
candidate (the group of an exposed const changed while the key did not), or unreadable (fail closed).

    python3 -m unittest lab/measure/test_iota_exposure.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from iota_exposure import classify  # noqa: E402


def e(exposed=None, unresolved=()):
    return {"exposed": {k: {"group": g, "constructs": ["go.const.iota"]} for k, g in (exposed or {}).items()}, "unresolved": list(unresolved)}


class Classify(unittest.TestCase):
    def test_no_exposed_const_is_clean(self):
        self.assertEqual(classify(e(), e()), ("clean", []))

    def test_an_exposed_const_whose_group_is_unchanged_is_exposed_not_a_candidate(self):
        self.assertEqual(classify(e({"p.B": "g1"}), e({"p.B": "g1"})), ("exposed", []))

    def test_a_changed_group_with_the_same_key_is_a_candidate(self):  # the lab/testdata/iota case: A = iota + 1 -> iota + 2
        self.assertEqual(classify(e({"p.B": "g1"}), e({"p.B": "g2"})), ("candidate", ["p.B"]))

    def test_a_const_that_appeared_or_left_the_closure_is_a_candidate(self):
        self.assertEqual(classify(e({"p.B": "g1"}), e({"p.B": "g1", "p.C": "g1"})), ("candidate", ["p.C"]))
        self.assertEqual(classify(e({"p.B": "g1", "p.C": "g1"}), e({"p.B": "g1"})), ("candidate", ["p.C"]))

    def test_an_unreadable_closure_or_a_missing_graph_is_unreadable(self):
        self.assertEqual(classify(e(unresolved=["p.X"]), e())[0], "unreadable")
        self.assertEqual(classify(e(), e(unresolved=["p.X"]))[0], "unreadable")
        self.assertEqual(classify(None, e())[0], "unreadable")
        self.assertEqual(classify(e(), None)[0], "unreadable")


if __name__ == "__main__":
    unittest.main()
