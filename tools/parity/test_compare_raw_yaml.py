# SPDX-License-Identifier: Apache-2.0
"""Tests of the fail-closed rules of tools/parity/compare_raw_yaml.py (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, section 6), on small synthetic dumps.

This is NOT the selftest of Unit 3c (planted defects in the parsers, the common-mode runs, the report checks): that unit is on hold. These tests only keep the comparison itself honest.

    python3 -W error::ResourceWarning -m unittest tools/parity/test_compare_raw_yaml.py
"""
import copy
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compare_raw_yaml as cmp_  # noqa: E402


def tree(**members):
    return {"k": "map", "v": [[k, {"k": "int", "v": v}] for k, v in members.items()]}


def case(id_, class_="built:x", pe=None, allowed=True, mapping=None):
    return {"id": id_, "class": class_, "bytes_hex": id_.encode().hex(), "parse_expect": pe or {"result": "accept", "tree": tree(a=1)}, "allowed": allowed, "mapping_expect": mapping, "pin": None}


def ok(id_, t=None):
    return {"id": id_, "ok": True, "tree": t or tree(a=1), "mapping": None}


def no(id_, cls="syntax"):
    return {"id": id_, "ok": False, "class": cls, "code": cls.upper()}


class Compare(unittest.TestCase):
    def run_compare(self, cases, rust, go, py, known=None, floors=None, mutate=None):
        d = tempfile.mkdtemp()
        paths = {}
        for name, rows in (("input", cases), ("rust", rust), ("go", go), ("python", py)):
            paths[name] = os.path.join(d, name)
            with open(paths[name], "w") as f:
                f.write("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
        if mutate:
            mutate(paths)
        return cmp_.compare(paths["input"], {k: paths[k] for k in ("rust", "go", "python")}, known, floors)

    def base(self):
        cases = [case("c1"), case("c2")]
        rows = [ok("c1"), ok("c2")]
        return cases, rows

    def test_a_clean_run_is_green(self):
        cases, rows = self.base()
        rep = self.run_compare(cases, rows, copy.deepcopy(rows), copy.deepcopy(rows))
        self.assertFalse(rep["is_red"], rep)
        self.assertEqual(rep["p_effective"]["count"], 2)

    def test_a_dropped_a_duplicated_and_a_reordered_line_are_red(self):
        cases, rows = self.base()
        for bad in ([rows[0]], [rows[0], rows[1], rows[1]], [rows[1], rows[0]]):
            rep = self.run_compare(cases, bad, copy.deepcopy(rows), copy.deepcopy(rows))
            self.assertTrue(rep["is_red"])

    def test_an_unfinished_last_line_and_a_line_that_is_not_json_are_red(self):
        cases, rows = self.base()
        def cut(paths):
            with open(paths["go"], "a") as f:
                f.write('{"id": "zz"')
        self.assertTrue(self.run_compare(cases, rows, copy.deepcopy(rows), copy.deepcopy(rows), mutate=cut)["is_red"])
        self.assertTrue(self.run_compare(cases, rows, ["{not json", json.dumps(rows[1])], copy.deepcopy(rows))["is_red"])

    def test_crash_and_timeout_are_red(self):
        cases, rows = self.base()
        for cls in ("CRASH", "TIMEOUT"):
            bad = [no("c1", cls), rows[1]]
            rep = self.run_compare(cases, bad, copy.deepcopy(rows), copy.deepcopy(rows))
            self.assertTrue(rep["is_red"] and rep["red_cases"] == 1)

    def test_a_word_outside_the_vocabulary_is_red(self):
        cases, rows = self.base()
        bad = [dict(no("c1"), **{"class": "mystery"}), rows[1]]
        self.assertTrue(self.run_compare(cases, bad, copy.deepcopy(rows), copy.deepcopy(rows))["is_red"])

    def test_a_thin_class_is_red(self):
        cases, rows = self.base()
        rep = self.run_compare(cases, rows, copy.deepcopy(rows), copy.deepcopy(rows), floors={"built": 99})
        self.assertTrue(rep["is_red"])

    def test_parity_is_measured_not_assumed_one_parser_refusing_is_a_finding_and_red_where_accept_was_constructed(self):
        cases, rows = self.base()
        rust = [ok("c1"), no("c2")]
        rep = self.run_compare(cases, rust, copy.deepcopy(rows), copy.deepcopy(rows))
        self.assertTrue(rep["is_red"])
        self.assertEqual(rep["findings_rust_go"][0]["kind"], "accept_vs_refuse")

    def test_both_parsers_accepting_a_forbidden_construct_is_blocking_even_though_they_agree(self):
        c = [case("f1", "dup", {"result": "refuse", "class": "duplicate_key"}, allowed=False)]
        rows = [ok("f1")]
        rep = self.run_compare(c, rows, copy.deepcopy(rows), copy.deepcopy(rows))
        self.assertTrue(rep["is_red"])
        self.assertIn("forbidden_construct_in_parity", rep["events"])

    def test_a_pinned_divergence_in_parity_is_red_and_one_that_no_longer_diverges_is_its_own_event(self):
        pin = {"result": "pinned_divergence"}
        c = [case("p1", "pinned", pin, "undetermined"), case("p2", "pinned", pin, "undetermined"), case("p3", "pinned", pin, "undetermined")]
        rust = [ok("p1"), no("p2"), ok("p3", tree(b=2))]
        go = [ok("p1"), no("p2"), ok("p3")]
        rep = self.run_compare(c, rust, go, copy.deepcopy(go))
        self.assertTrue(rep["is_red"])
        self.assertEqual(rep["events"]["pinned_no_longer_diverges"], ["p2"])
        self.assertEqual(rep["pinned"]["in_parity"], 1)
        self.assertEqual(rep["pinned"]["still_diverge"], 1)
        self.assertEqual(rep["pinned"]["occurrences"], 3)

    def test_pyyaml_differing_on_a_member_the_mapping_reads_for_an_admitted_input_is_blocking_unless_known(self):
        t = {"k": "map", "v": [["release", {"k": "map", "v": [["envelope", {"k": "int", "v": 3}]]}]]}
        py_t = {"k": "map", "v": [["release", {"k": "map", "v": [["envelope", {"k": "str", "v": "3"}]]}]]}
        c = [case("w1", "built:x", {"result": "accept", "tree": t})]
        rows = [ok("w1", t)]
        py = [ok("w1", py_t)]
        rep = self.run_compare(c, rows, copy.deepcopy(rows), py)
        self.assertTrue(rep["is_red"])
        self.assertEqual(rep["events"]["python_differs_on_read_members"][0]["members"], ["release.envelope"])
        known = {"entries": [{"kind": "read_member_difference", "class_prefix": "built", "reason": "t"}]}
        self.assertFalse(self.run_compare(c, rows, copy.deepcopy(rows), py, known)["is_red"])

    def test_pyyaml_accepting_what_forms_refuses_is_the_safe_direction_and_a_known_difference_must_keep_occurring(self):
        c = [case("d1", "dup", {"result": "refuse", "class": "duplicate_key"}, allowed=False)]
        forms = [no("d1", "duplicate_key")]
        known = {"entries": [{"kind": "python_accepts_forms_refuses", "class_prefix": "dup", "forms_class": "duplicate_key", "reason": "t"}]}
        rep = self.run_compare(c, forms, copy.deepcopy(forms), [ok("d1")], known)
        self.assertFalse(rep["is_red"], rep)
        self.assertEqual(rep["python"]["more_permissive_by_class"], {"dup": 1})
        # the oracle now refuses as well: the known difference no longer occurs
        rep = self.run_compare(c, forms, copy.deepcopy(forms), [no("d1", "syntax")], known)
        self.assertTrue(rep["is_red"])

    def test_a_mapping_answer_that_is_not_the_expected_one_is_red_only_where_the_parsers_agree(self):
        m = {"expect": None, "refuse": "envelope_not_3", "field": "release.envelope"}
        c = [case("m1", "value:x", mapping=m)]
        good = [dict(ok("m1"), mapping={"refuse": "envelope_not_3", "field": "release.envelope"})]
        bad = [dict(ok("m1"), mapping={"refuse": "field_type", "field": "release.envelope"})]
        self.assertFalse(self.run_compare(c, good, copy.deepcopy(good), copy.deepcopy(good))["is_red"])
        self.assertTrue(self.run_compare(c, good, bad, copy.deepcopy(good))["is_red"])


if __name__ == "__main__":
    unittest.main()
