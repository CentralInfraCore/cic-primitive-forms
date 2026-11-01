"""The verification kernel (evidence.py): the cases of lab/testdata/evidence and the properties of the function.

    python3 -m unittest lab/measure/test_evidence.py
"""
import copy
import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import evidence as ev  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata", "evidence")


def load(name):
    with open(os.path.join(FX, name + ".json")) as f:
        return json.load(f)


def assurance(d=None):
    d = d or load("assurance")
    return ev.Assurance.of(d["contract_versions"], d["rule_versions"], {k: v for k, v in d["extractors"].items()})


A = assurance()


class Cases(unittest.TestCase):
    def test_every_fixture(self):
        names = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(FX, "*.json")))
        names.remove("assurance")
        self.assertGreaterEqual(len(names), 13)
        for n in names:
            with self.subTest(n):
                c = load(n)
                d = ev.evaluate_reuse(c["previous"], c["current"], A)
                self.assertEqual((d.status, list(d.reasons)), (c["expect"]["status"], c["expect"]["reasons"]), c["why"])

    def test_iota_old_extractor_is_unverifiable_and_the_keys_are_the_equal_ones_of_the_red_case(self):
        c = load("iota_old_extractor")
        self.assertEqual(c["previous"]["code_closure_digest"], c["current"]["code_closure_digest"])
        d = ev.evaluate_reuse(c["previous"], c["current"], A)
        self.assertEqual(d.status, ev.UNVERIFIABLE)

    def test_the_barrier_is_the_assurance_not_the_evidence(self):
        # If the caller declared the extractor verified for implicit repeat, the equal keys give a false reuse. The kernel creates no
        # trust: this is what stands between the blind graph and a REUSE_ELIGIBLE, and why the assurance must come from outside.
        c = load("iota_old_extractor")
        d = load("assurance")
        (ext, constructs), = d["extractors"].items()
        d["extractors"][ext] = constructs + ["go.const.implicit_repeat"]
        self.assertEqual(ev.evaluate_reuse(c["previous"], c["current"], assurance(d)).status, ev.REUSE_ELIGIBLE)


class Properties(unittest.TestCase):
    def pair(self, name="valid"):
        c = load(name)
        return c["previous"], c["current"]

    def test_pure_no_mutation_and_deterministic(self):
        for n in ("valid", "code_changed", "graph_unverified"):
            p, c = self.pair(n)
            p0, c0 = copy.deepcopy(p), copy.deepcopy(c)
            d1, d2 = ev.evaluate_reuse(p, c, A), ev.evaluate_reuse(p, c, A)
            self.assertEqual((p, c), (p0, c0))
            self.assertEqual(d1, d2)

    def test_every_reason_is_a_stable_code(self):
        for path in sorted(glob.glob(os.path.join(FX, "*.json"))):
            if path.endswith("assurance.json"):
                continue
            c = load(os.path.basename(path)[:-5])
            for r in ev.evaluate_reuse(c["previous"], c["current"], A).reasons:
                self.assertIn(r, ev.REASONS)

    def test_the_decision_names_what_was_compared(self):
        p, c = self.pair("code_changed")
        d = ev.evaluate_reuse(p, c, A)
        self.assertEqual(d.compared["code_closure_digest"], {"previous": "code-1", "current": "code-2"})
        self.assertEqual(d.compared["previous_evidence"], "run-0001")

    def test_missing_or_mistyped_fields_are_unverifiable_not_an_exception(self):
        p, c = self.pair()
        for edit in (lambda e: e.pop("code_closure_digest"), lambda e: e.update(environment_digest=3), lambda e: e.pop("graph"),
                     lambda e: e["graph"].pop("opaque"), lambda e: e.update(opaque_reads="x"), lambda e: e.pop("subject")):
            q = copy.deepcopy(p)
            edit(q)
            d = ev.evaluate_reuse(q, c, A)
            self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("MALFORMED_EVIDENCE",)))
        for bad in (None, [], "x"):
            self.assertEqual(ev.evaluate_reuse(bad, c, A).status, ev.UNVERIFIABLE)
        q = copy.deepcopy(p)
        q["verdict"] = "unknown"
        self.assertEqual(ev.evaluate_reuse(q, c, A).status, ev.UNVERIFIABLE)

    def test_an_unstable_subject_is_unverifiable_whatever_the_keys(self):
        p, c = self.pair()
        for side in ("previous", "current"):
            q_, r_ = copy.deepcopy(p), copy.deepcopy(c)
            (q_ if side == "previous" else r_)["stability"] = "unstable"
            d = ev.evaluate_reuse(q_, r_, A)
            self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("NONDETERMINISTIC_SUBJECT",)), side)
        q_ = copy.deepcopy(p)
        q_["stability"] = "stable"
        self.assertEqual(ev.evaluate_reuse(q_, c, A).status, ev.REUSE_ELIGIBLE)
        q_["stability"] = "sometimes"
        self.assertEqual(ev.evaluate_reuse(q_, c, A).reasons, ("MALFORMED_EVIDENCE",))

    def test_a_rule_version_is_not_inherited(self):
        p, c = self.pair()
        p["rule_version"] = "cic-lab/reuse-rules/v0"
        self.assertEqual(ev.evaluate_reuse(p, c, A).reasons, ("RULE_VERSION_NOT_ACCEPTED",))

    def test_another_subject_or_extractor_or_kind_is_not_comparable(self):
        p, c = self.pair()
        q = copy.deepcopy(c)
        q["subject"]["id"] = "go:TestOther"
        self.assertEqual(ev.evaluate_reuse(p, q, A).reasons, ("SUBJECT_MISMATCH",))
        q = copy.deepcopy(c)
        q["graph"]["extractor"] = "typeddeps:other"
        self.assertIn("EXTRACTOR_MISMATCH", ev.evaluate_reuse(p, q, A).reasons)
        p2, c2 = copy.deepcopy(p), copy.deepcopy(c)
        p2["subject"]["kind"] = c2["subject"]["kind"] = "mutant"
        self.assertEqual(ev.evaluate_reuse(p2, c2, A).reasons, ("SUBJECT_KIND_UNSUPPORTED",))

    def test_nothing_is_reusable_with_an_empty_assurance(self):
        p, c = self.pair()
        nothing = ev.Assurance.of([], [], {})
        self.assertEqual(ev.evaluate_reuse(p, c, nothing).status, ev.UNVERIFIABLE)

    def test_the_only_way_to_reuse_is_every_digest_equal(self):
        p, c = self.pair()
        for k in ("code_closure_digest", "data_dependencies_digest", "environment_digest"):
            q = copy.deepcopy(c)
            q[k] = "different"
            self.assertEqual(ev.evaluate_reuse(p, q, A).status, ev.RERUN_REQUIRED, k)


if __name__ == "__main__":
    unittest.main()
