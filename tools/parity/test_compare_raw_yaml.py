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

    def run_compare_sub(self, cases, rust, go, py, subtypes, known=None):
        d = tempfile.mkdtemp()
        paths = {}
        for name, rows in (("input", cases), ("rust", rust), ("go", go), ("python", py)):
            paths[name] = os.path.join(d, name)
            with open(paths[name], "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
        return cmp_.compare(paths["input"], {k: paths[k] for k in ("rust", "go", "python")}, known, None, subtypes)

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

    def test_a_crash_or_timeout_of_the_python_oracle_is_red_too_but_an_error_of_pyyaml_itself_is_only_listed(self):
        cases, rows = self.base()
        for cls in ("CRASH", "TIMEOUT"):
            rep = self.run_compare(cases, rows, copy.deepcopy(rows), [no("c1", cls), rows[1]])
            self.assertTrue(rep["is_red"] and rep["red_cases"] == 1, cls)
        rep = self.run_compare(cases, rows, copy.deepcopy(rows), [no("c1", "python_error"), rows[1]])
        self.assertFalse(rep["is_red"], rep)
        self.assertEqual(rep["events"]["python_error"], ["c1"])

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
        # p1: the same tree and (here) the same positions: it agrees completely; p2: both refuse the same way. Both are "no longer diverges", named as their own event
        self.assertEqual(rep["events"]["pinned_no_longer_diverges"], ["p1", "p2"])
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
        entry = {"id": "w1", "kind": "read_member_difference", "members": ["release.envelope"], "python_tree_sha256": cmp_.tree_sha(py_t), "acceptance": "t"}
        self.assertFalse(self.run_compare(c, rows, copy.deepcopy(rows), py, {"entries": [entry]})["is_red"])
        # a class is not enough, another id is not enough, and another answer is not enough
        self.assertTrue(self.run_compare(c, rows, copy.deepcopy(rows), py, {"entries": [dict(entry, id="other")]})["is_red"])
        self.assertTrue(self.run_compare(c, rows, copy.deepcopy(rows), py, {"entries": [dict(entry, python_tree_sha256="0" * 64)]})["is_red"])
        self.assertTrue(self.run_compare(c, rows, copy.deepcopy(rows), py, {"entries": [dict(entry, members=["release.build_hash"])]})["is_red"])
        self.assertTrue(self.run_compare(c, rows, copy.deepcopy(rows), py, {"entries": [{k: v for k, v in entry.items() if k != "acceptance"}]})["is_red"])

    def test_pyyaml_accepting_what_forms_refuses_is_the_safe_direction_and_a_known_difference_is_exact_per_case(self):
        c = [case("d1", "dup", {"result": "refuse", "class": "duplicate_key"}, allowed=False), case("d2", "dup", {"result": "refuse", "class": "duplicate_key"}, allowed=False)]
        forms = [no("d1", "duplicate_key"), no("d2", "duplicate_key")]
        py = [ok("d1"), ok("d2")]
        mk = lambda i: {"id": i, "kind": "python_accepts_forms_refuses", "rust_class": "duplicate_key", "go_class": "duplicate_key", "python_tree_sha256": cmp_.tree_sha(tree(a=1)), "acceptance": "t"}
        known = {"entries": [mk("d1"), mk("d2")]}
        rep = self.run_compare(c, forms, copy.deepcopy(forms), py, known)
        self.assertFalse(rep["is_red"], rep)
        self.assertEqual(rep["python"]["more_permissive_by_class"], {"dup": 2})
        # the oracle now refuses d1: that known difference no longer occurs
        self.assertTrue(self.run_compare(c, forms, copy.deepcopy(forms), [no("d1", "syntax"), ok("d2")], known)["is_red"])
        # an entry for d1 only: d2 is the same class and the same answer, but it is not accepted by being in the class (it is counted, not blessed): the entry for d1 still has to match exactly
        rep = self.run_compare(c, forms, copy.deepcopy(forms), [ok("d1", tree(a=2)), ok("d2")], {"entries": [mk("d1")]})
        self.assertTrue(rep["is_red"])

    def test_semantic_parity_and_position_parity_are_apart_a_position_only_pinned_divergence_must_be_declared(self):
        pin = {"result": "pinned_divergence"}
        c = [case("p1", "pinned", pin, "undetermined")]
        r = dict(ok("p1"), positions=[[1, 1, 0]])
        g = dict(ok("p1"), positions=[[1, 2, 1]])
        # not declared: the measured subtype (positions_only) differs from the declared default (semantic): red, a change of classification
        rep = self.run_compare(c, [r], [g], [ok("p1")])
        self.assertTrue(rep["is_red"])
        self.assertEqual(rep["events"]["pinned_subtype_changed"][0]["measured"], "positions_only")
        sub = {c[0]["bytes_hex"]: {"subtype": "positions_only", "original": {"rust": [[1, 1, 0]], "go": [[1, 2, 1]]}}}
        d = tempfile.mkdtemp()
        rep = self.run_compare_sub(c, [r], [g], [ok("p1")], sub)
        self.assertFalse(rep["is_red"], rep)
        self.assertEqual(rep["p_effective"]["count"], 0)       # allowed is not true: P_parity holds but nothing is admitted
        self.assertEqual(rep["position_findings"][0]["kind"], "positions_differ")
        # the original answers changed: red
        g2 = dict(ok("p1"), positions=[[9, 9, 9]])
        self.assertTrue(self.run_compare_sub(c, [r], [g2], [ok("p1")], sub)["is_red"])
        # declared positions_only but the positions now agree, or the trees now differ: red
        self.assertTrue(self.run_compare_sub(c, [r], [dict(ok("p1"), positions=[[1, 1, 0]])], [ok("p1")], sub)["is_red"])
        self.assertTrue(self.run_compare_sub(c, [r], [dict(ok("p1", tree(b=2)), positions=[[1, 2, 1]])], [ok("p1")], sub)["is_red"])

    def test_a_refusal_position_only_divergence_is_declared_and_checked_the_same_way(self):
        pin = {"result": "pinned_divergence"}
        c = [case("p1", "pinned", pin, False)]
        r = dict(no("p1", "duplicate_key"), locations=[[1, 11, 13]])
        g = dict(no("p1", "duplicate_key"), locations=[[1, 15, 17]])
        sub = {c[0]["bytes_hex"]: {"subtype": "refusal_position_only", "original": {"rust": [[1, 11, 13]], "go": [[1, 15, 17]]}}}
        self.assertFalse(self.run_compare_sub(c, [r], [g], [ok("p1")], sub)["is_red"])
        self.assertTrue(self.run_compare(c, [r], [g], [ok("p1")])["is_red"])

    def test_a_mapping_answer_that_is_not_the_expected_one_is_red_only_where_the_parsers_agree(self):
        m = {"expect": None, "refuse": "envelope_not_3", "field": "release.envelope"}
        c = [case("m1", "value:x", mapping=m)]
        good = [dict(ok("m1"), mapping={"refuse": "envelope_not_3", "field": "release.envelope"})]
        bad = [dict(ok("m1"), mapping={"refuse": "field_type", "field": "release.envelope"})]
        self.assertFalse(self.run_compare(c, good, copy.deepcopy(good), copy.deepcopy(good))["is_red"])
        self.assertTrue(self.run_compare(c, good, bad, copy.deepcopy(good))["is_red"])


if __name__ == "__main__":
    unittest.main()
