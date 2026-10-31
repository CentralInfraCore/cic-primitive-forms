# SPDX-License-Identifier: Apache-2.0
"""The comparison of the two receipt fuzz replays has to turn red on each break of a correct pair, and not on the one named parser class."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_receipt_fuzz as c  # noqa: E402


def hexed(text):
    return (b"\x00\x00" + text.encode()).hex()


def good():
    trace = [["0", "Receipt", hexed("type: t\n")], ["1", "Document", hexed("zzz: 1\n")], ["2", "Left", hexed("a: [\n")]]
    rec = {0: ["ok", '{"engine_identity":"X"}', "a", {}, ["k"], "p"], 1_000_000: ["ok", '{"engine_identity":"X"}', "a", {}, ["k"], "p"]}
    return trace, copy.deepcopy(trace), rec, copy.deepcopy(rec)


class TheComparison(unittest.TestCase):
    def run_check(self, rt, gt, rr, gr):
        return c.check(rt, gt, rr, gr)

    def test_a_correct_pair_is_clean(self):
        problems, explained, ends = self.run_check(*good())
        self.assertEqual((problems, explained), ([], []))
        self.assertEqual(sum(ends.values()), 3)

    def test_other_inputs_are_a_defect_of_the_replay(self):
        rt, gt, rr, gr = good()
        gt[0][2] = hexed("type: u\n")
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_a_different_end_is_red(self):
        rt, gt, rr, gr = good()
        gt[1][1] = "Capability"
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_left_against_something_else_is_red_outside_the_parser_class(self):
        rt, gt, rr, gr = good()
        rt[1][1] = "Left"
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_the_named_parser_class_is_counted_and_not_red(self):
        rt, gt, rr, gr = good()
        text = 'a: "x\ny"\n'
        rt[1] = ["1", "Left", hexed(text)]
        gt[1] = ["1", "Document", hexed(text)]
        problems, explained, _ = self.run_check(rt, gt, rr, gr)
        self.assertEqual(problems, [])
        self.assertEqual(len(explained), 1)

    def test_the_class_does_not_excuse_another_end(self):
        rt, gt, rr, gr = good()
        text = 'a: "x\ny"\n'
        rt[1] = ["1", "Document", hexed(text)]
        gt[1] = ["1", "Capability", hexed(text)]
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_a_receipt_that_differs_is_red(self):
        rt, gt, rr, gr = good()
        gr[0][1] = '{"engine_identity":"X","more":1}'
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_a_receipt_only_one_language_made_is_red(self):
        rt, gt, rr, gr = good()
        del gr[1_000_000]
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_a_round_of_another_length_is_red(self):
        rt, gt, rr, gr = good()
        gt.pop()
        self.assertTrue(self.run_check(rt, gt, rr, gr)[0])

    def test_the_class_is_a_line_break_inside_a_quote_at_column_zero(self):
        self.assertTrue(c.quoted_line_break_at_column_zero('a: "x\ny"\n'))
        self.assertTrue(c.quoted_line_break_at_column_zero("a: 'x\ny'\n"))
        self.assertFalse(c.quoted_line_break_at_column_zero('a: "x\n  y"\n'))
        self.assertFalse(c.quoted_line_break_at_column_zero('a: "x"\nb: y\n'))
        self.assertFalse(c.quoted_line_break_at_column_zero('a: "x\\" y"\nb: 1\n'))   # an escaped quote does not close the scalar, the next one does
        self.assertTrue(c.quoted_line_break_at_column_zero('a: "x\\"\nb"\n'))


if __name__ == "__main__":
    unittest.main()
