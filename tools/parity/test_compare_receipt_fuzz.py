# SPDX-License-Identifier: Apache-2.0
"""The comparison of the two receipt fuzz replays has to turn red on each break of a correct pair, and not on the one quarantined parser case."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_receipt_fuzz as c  # noqa: E402

REASON = c.QUARANTINED_RUST_REASON
QUOTED = 'a: "x\ny"\n'


def hexed(text):
    return (b"\x00\x00" + text.encode()).hex()


def good():
    """Two seeds (ids 0 and 1), three mutations: a receipt, a refused document, an input left to the parser."""
    trace = [["0", "Receipt", hexed("type: t\n"), ""], ["1", "Document", hexed("zzz: 1\n"), ""], ["2", "Left", hexed("a: [\n"), "syntax: while parsing"]]
    rec = {0: ["ok", '{"engine_identity":"X"}', "a", {}, ["k"], "p"], 1: ["err", "Document", ""], c.MUTATION_ID: ["ok", '{"engine_identity":"X"}', "a", {}, ["k"], "p"]}
    return trace, copy.deepcopy(trace), rec, copy.deepcopy(rec), 2


def quarantined(rt, gt, go_end="Document"):
    """Mutation 1 as the one quarantined case: Rust refuses for its reason, Go reads."""
    rt[1] = ["1", "Left", hexed(QUOTED), REASON]
    gt[1] = ["1", go_end, hexed(QUOTED), ""]


class TheComparison(unittest.TestCase):
    def test_a_correct_pair_is_clean(self):
        problems, explained, ends = c.check(*good())
        self.assertEqual((problems, explained), ([], []))
        self.assertEqual(sum(ends.values()), 3)

    # the two rounds
    def test_other_inputs_are_a_defect_of_the_replay(self):
        rt, gt, rr, gr, n = good()
        gt[0][2] = hexed("type: u\n")
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_a_round_of_another_length_is_red(self):
        rt, gt, rr, gr, n = good()
        gt.pop()
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_a_trace_line_without_the_reason_column_is_red(self):
        rt, gt, rr, gr, n = good()
        rt[0] = rt[0][:3]
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_a_different_end_is_red(self):
        rt, gt, rr, gr, n = good()
        gt[1][1] = "Capability"
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    # the quarantine: one direction, one Rust reason, one kind of input
    def test_the_quarantined_case_is_counted_and_not_red(self):
        rt, gt, rr, gr, n = good()
        quarantined(rt, gt)
        problems, explained, _ = c.check(rt, gt, rr, gr, n)
        self.assertEqual(problems, [])
        self.assertEqual(len(explained), 1)

    def test_the_other_direction_is_red(self):
        rt, gt, rr, gr, n = good()
        rt[1] = ["1", "Document", hexed(QUOTED), ""]
        gt[1] = ["1", "Left", hexed(QUOTED), REASON]
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_another_rust_reason_is_red_although_the_input_looks_like_the_class(self):
        rt, gt, rr, gr, n = good()
        quarantined(rt, gt)
        rt[1][3] = "syntax: mapping values are not allowed in this context"
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_the_right_reason_without_the_line_break_in_a_quote_is_red(self):
        rt, gt, rr, gr, n = good()
        quarantined(rt, gt)
        rt[1][2] = gt[1][2] = hexed('a: "x y"\n')
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_the_quarantine_does_not_excuse_another_end(self):
        rt, gt, rr, gr, n = good()
        quarantined(rt, gt)
        rt[1] = ["1", "Document", hexed(QUOTED), ""]
        gt[1] = ["1", "Capability", hexed(QUOTED), ""]
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_the_quarantine_excuses_the_end_and_not_the_missing_record_of_the_language_that_reads(self):
        rt, gt, rr, gr, n = good()
        quarantined(rt, gt, "Receipt")
        gr[c.MUTATION_ID + 1] = ["ok", "d", "a", {}, ["k"], "p"]   # go read the input and made a receipt; rust refused it and has none
        problems, explained, _ = c.check(rt, gt, rr, gr, n)
        self.assertEqual((problems, len(explained)), ([], 1))
        del gr[c.MUTATION_ID + 1]                                    # go's own trace says Receipt: its record is called for
        self.assertEqual(len(c.check(rt, gt, rr, gr, n)[0]), 1)

    # the records: what the traces call for, in EACH language
    def test_a_receipt_that_differs_is_red(self):
        rt, gt, rr, gr, n = good()
        gr[0][1] = '{"engine_identity":"X","more":1}'
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_a_mutation_record_missing_from_one_language_is_red(self):
        rt, gt, rr, gr, n = good()
        del gr[c.MUTATION_ID]
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_a_mutation_record_missing_from_both_languages_is_red(self):
        rt, gt, rr, gr, n = good()
        del rr[c.MUTATION_ID]
        del gr[c.MUTATION_ID]
        problems = c.check(rt, gt, rr, gr, n)[0]
        # one per language, and both say the record is MISSING: comparing the two dumps with each other sees nothing
        self.assertEqual(sorted(t.split(":")[0] for _, t in problems), ["go", "rust"])
        self.assertTrue(all("is missing" in t and "mutation" in t for _, t in problems), problems)

    def test_a_seed_record_missing_from_both_languages_is_red(self):
        rt, gt, rr, gr, n = good()
        del rr[1]
        del gr[1]
        problems = c.check(rt, gt, rr, gr, n)[0]
        self.assertEqual(sorted(t.split(":")[0] for _, t in problems), ["go", "rust"])
        self.assertTrue(all("is missing" in t and "seed" in t for _, t in problems), problems)

    def test_a_record_the_trace_does_not_call_for_is_red(self):
        rt, gt, rr, gr, n = good()
        gr[c.MUTATION_ID + 1] = ["ok", "d", "a", {}, ["k"], "p"]   # mutation 1 did not end as a receipt
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_a_record_that_is_not_a_receipt_for_a_mutation_is_red(self):
        rt, gt, rr, gr, n = good()
        rr[c.MUTATION_ID] = gr[c.MUTATION_ID] = ["err", "Document", ""]
        self.assertTrue(c.check(rt, gt, rr, gr, n)[0])

    def test_the_dump_cap_limits_what_is_called_for(self):
        rt, gt, rr, gr, n = good()
        rt.append(["3", "Receipt", hexed("type: v\n"), ""])
        gt.append(["3", "Receipt", hexed("type: v\n"), ""])
        old = c.DUMP_CAP
        try:
            c.DUMP_CAP = 1          # only the first receipt of the round is dumped
            self.assertEqual(c.check(rt, gt, rr, gr, n)[0], [])
            c.DUMP_CAP = 2          # now the second one is called for as well
            self.assertEqual(len(c.check(rt, gt, rr, gr, n)[0]), 2)
        finally:
            c.DUMP_CAP = old

    def test_the_class_is_a_line_break_inside_a_quote_at_column_zero(self):
        self.assertTrue(c.quoted_line_break_at_column_zero('a: "x\ny"\n'))
        self.assertTrue(c.quoted_line_break_at_column_zero("a: 'x\ny'\n"))
        self.assertFalse(c.quoted_line_break_at_column_zero('a: "x\n  y"\n'))
        self.assertFalse(c.quoted_line_break_at_column_zero('a: "x"\nb: y\n'))
        self.assertFalse(c.quoted_line_break_at_column_zero('a: "x\\" y"\nb: 1\n'))   # an escaped quote does not close the scalar, the next one does
        self.assertTrue(c.quoted_line_break_at_column_zero('a: "x\\"\nb"\n'))


if __name__ == "__main__":
    unittest.main()
