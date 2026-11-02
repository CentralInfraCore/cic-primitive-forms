"""Carrying mutation verdicts into a shard's report, and the gate accepting that report.

    python3 -m unittest test_mutcarry
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import mutcarry as mc  # noqa: E402
import quality_gate as qg  # noqa: E402


def outcome(name, summary="CaughtMutant"):
    return {"scenario": {"Mutant": {"name": name}}, "summary": summary, "phase_results": []}


def report(outcomes):
    r = {"outcomes": [{"scenario": "Baseline", "summary": "Success"}] + outcomes, "success": 0}
    r.update(mc.recount(outcomes))
    return r


class Carry(unittest.TestCase):
    def test_carried_set_needs_listing_rule_no_conflict(self):
        raw = {"a": (outcome("a"), False), "b": (outcome("b"), False), "c": (outcome("c"), True), "d": (outcome("d", "MissedMutant"), False)}
        rows = [{"mutant": n, "carry": True} for n in "abcd"] + [{"mutant": "e", "carry": False}]
        got = mc.carried_set(rows, raw, {"a", "c", "d", "e"})
        self.assertEqual(sorted(got), ["a"])   # b is not listed now, c is in conflict, d was not caught, e is not carried by the rule

    def test_exclude_regex_is_exact_and_escaped(self):
        import re
        rx = mc.exclude_regex({"src/a.rs:1:2: replace f -> u8 with 0": outcome("x")}, ["src/a.rs:1:2: replace f -> u8 with 0", "other"])
        self.assertTrue(re.search(rx.replace("(?:", "("), "src/a.rs:1:2: replace f -> u8 with 0"))
        self.assertFalse(re.search(rx.replace("(?:", "("), "src/a.rs:1:2: replace f -> u8 with 01"))
        self.assertEqual(mc.exclude_regex({"a": outcome("a")}, ["b"]), "")

    def test_merge_recounts_and_marks(self):
        fresh = report([outcome("x", "MissedMutant"), outcome("y")])
        m = mc.merge(fresh, {"a": outcome("a"), "z": outcome("z")}, ["x", "y", "a"], "run-1")
        self.assertEqual((m["caught"], m["missed"], m["total_mutants"]), (2, 1, 3))
        self.assertEqual([o["carried_from"] for o in m["outcomes"] if "carried_from" in o], ["run-1"])   # z is another shard's

    def test_a_carried_mutant_that_also_ran_is_an_error(self):
        with self.assertRaises(ValueError):
            mc.merge(report([outcome("a")]), {"a": outcome("a")}, ["a"], "r")

    def test_the_gate_accepts_the_merged_report_and_counts_the_carried(self):
        fresh = report([outcome("x", "MissedMutant"), outcome("y", "Unviable")])
        m = mc.merge(fresh, {"a": outcome("a"), "b": outcome("b", "CaughtMutant")}, ["x", "y", "a", "b"], "r")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(m, f)
        try:
            caught, missed, names = qg._rust_outcome(f.name, True)
        finally:
            os.unlink(f.name)
        self.assertEqual((caught, missed, sorted(names)), (2, 1, ["a", "b", "x", "y"]))

    def test_a_report_with_stale_totals_is_refused_by_the_gate(self):
        m = mc.merge(report([outcome("x")]), {"a": outcome("a")}, ["x", "a"], "r")
        m["caught"] = 1   # the totals of the fresh report, not recounted
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(m, f)
        try:
            with self.assertRaises(qg.Refused):
                qg._rust_outcome(f.name, True)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
