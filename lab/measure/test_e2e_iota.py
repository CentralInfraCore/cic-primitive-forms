"""End to end: real Go source -> the extractor -> the evidence builder -> the kernel. No `constructs` is written by hand, and the
Assurance of an extractor build is MEASURED (qualify.py), not declared.

    TYPEDDEPS_BIN=path/to/typeddeps python3 -m unittest lab/measure/test_e2e_iota.py     (needs `go` on PATH)

The RED -> GREEN chain of the iota defect, on two builds of the same source: the extractor with -planted-no-const-group (the model
before the const group rule) and the extractor as it is.

    the old model, iota case                       UNVERIFIABLE   (it recognises implicit iota; it is not qualified for it)
    the fixed model, a const group that changed    RERUN_REQUIRED
    the fixed model, dependencies that did not     REUSE_ELIGIBLE

A construct the extractor recognises and that is not qualified cannot lead to a reuse authorisation, however equal the Merkle keys are.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402
import qualify as q  # noqa: E402

BIN = os.environ.get("TYPEDDEPS_BIN")
TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata")
OLD = ("-planted-no-const-group",)
NEW = ()


def extract(rel, args):
    d = os.path.join(TD, rel)
    g = json.loads(subprocess.run([os.path.abspath(BIN), *args, "."], cwd=d, check=True, capture_output=True, text=True).stdout)
    return g, merkle.keys(g)[0]


def passes(rel):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(TD, rel), capture_output=True).returncode == 0


ENV = hashlib.sha256(subprocess.run(["go", "version"], capture_output=True, text=True).stdout.encode()).hexdigest() if BIN else ""
DATA = hashlib.sha256(b"no data read").hexdigest()


@unittest.skipUnless(BIN, "TYPEDDEPS_BIN is not set")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.id = {}
        cls.prov = {}
        cls.assurance = {}
        for args in (OLD, NEW):
            ident, cls.prov[args] = be.extractor_identity(BIN, args)
            cls.id[args] = ident
            cls.assurance[args] = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {ident: q.qualify(BIN, args)})

    def evidence(self, rel, subject, args, verdict=None, graph=None):
        g, keys = extract(rel, args)
        return be.build_evidence(graph or g, keys, subject, self.id[args], self.prov[args], DATA, ENV, verdict=verdict)

    def decide(self, prev, cur, args):
        return ev.evaluate_reuse(prev, cur, self.assurance[args])

    # --- the chain ---------------------------------------------------------------------------------------------------------

    def test_red_the_old_model_and_the_iota_case_is_unverifiable(self):
        prev = self.evidence("iota/a", "iotarepro.TestB", OLD, "pass")
        cur = self.evidence("iota/b", "iotarepro.TestB", OLD)
        # the dangerous situation, from the real source: equal keys, an earlier pass, the test now fails
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        self.assertFalse(passes("iota/b"))
        self.assertIn("go.const.implicit_repeat", cur["graph"]["constructs"])
        self.assertEqual(cur["graph"]["analysis"], {"status": "COMPLETE", "unresolved": []})
        d = self.decide(prev, cur, OLD)
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))

    def test_green_the_fixed_model_and_a_changed_const_group_needs_a_rerun(self):
        for rel, pkg in (("iota", "iotarepro"), ("iota-insert", "insrepro"), ("iota-inherit", "inhrepro")):
            with self.subTest(rel):
                prev = self.evidence(rel + "/a", pkg + ".TestB", NEW, "pass")
                cur = self.evidence(rel + "/b", pkg + ".TestB", NEW)
                self.assertTrue(q.CONST_GROUP <= self.assurance[NEW].extractors[self.id[NEW]])
                self.assertFalse(passes(rel + "/b"))  # the value did move
                d = self.decide(prev, cur, NEW)
                self.assertEqual((d.status, d.reasons), (ev.RERUN_REQUIRED, ("CODE_CHANGED",)))

    def test_green_the_fixed_model_and_dependencies_that_did_not_change_are_eligible(self):
        # an exposed test (it reaches implicit iota) whose const group is the same: reusable again, and legitimately
        prev = self.evidence("iota/a", "iotarepro.TestB", NEW, "pass")
        cur = self.evidence("iota/a", "iotarepro.TestB", NEW)
        self.assertIn("go.const.implicit_repeat", cur["graph"]["constructs"])
        d = self.decide(prev, cur, NEW)
        self.assertEqual((d.status, d.reasons), (ev.REUSE_ELIGIBLE, (ev.ALL_CONDITIONS_HOLD,)))
        # and a change outside the closure of the test
        prev = self.evidence("plain/a", "plainrepro.TestF", NEW, "pass")
        cur = self.evidence("plain/b", "plainrepro.TestF", NEW)  # G changed; TestF does not reach it
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        self.assertEqual(self.decide(prev, cur, NEW).status, ev.REUSE_ELIGIBLE)

    # --- the boundary ------------------------------------------------------------------------------------------------------

    def test_what_the_extractor_reports_is_not_what_authorises(self):
        # Vouching from outside for the old model is the one way to a false reuse: the barrier is the assurance.
        prev = self.evidence("iota/a", "iotarepro.TestB", OLD, "pass")
        cur = self.evidence("iota/b", "iotarepro.TestB", OLD)
        vouched = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {self.id[OLD]: q.BASE | q.CONST_GROUP})
        self.assertEqual(ev.evaluate_reuse(prev, cur, vouched).status, ev.REUSE_ELIGIBLE)

    def test_the_old_model_is_not_qualified_and_the_new_one_is(self):
        self.assertTrue(q.CONST_GROUP.isdisjoint(q.qualify(BIN, OLD)))
        self.assertTrue(q.CONST_GROUP <= q.qualify(BIN, NEW))
        self.assertNotEqual(self.id[OLD], self.id[NEW])

    def test_generics_are_recognised_and_not_qualified_either(self):
        a = self.evidence("generic", "genericrepro.TestId", NEW, "pass")
        b = self.evidence("generic", "genericrepro.TestId", NEW)
        self.assertIn("go.func.generic", a["graph"]["constructs"])
        self.assertEqual(self.decide(a, b, NEW).reasons, ("CONSTRUCT_NOT_VERIFIED",))

    def test_incomplete_analysis_is_unverifiable(self):
        g, keys = extract("plain/a", NEW)
        prev = be.build_evidence(g, keys, "plainrepro.TestF", self.id[NEW], self.prov[NEW], DATA, ENV, verdict="pass")

        def incomplete(x):
            for n in x["nodes"]:
                if n["id"] == "plainrepro.F":  # in the closure of TestF
                    n["analysis"] = {"status": "INCOMPLETE", "unresolved": ["x"]}

        for name, edit in (("a node of the closure says INCOMPLETE", incomplete),
                           ("a node has no analysis", lambda x: [n.pop("analysis") for n in x["nodes"]]),
                           ("a node has no constructs", lambda x: [n.pop("constructs") for n in x["nodes"]]),
                           ("another schema", lambda x: x.update(schema="cic-lab/typeddeps/v1"))):
            with self.subTest(name):
                bad = copy.deepcopy(g)
                edit(bad)
                cur = be.build_evidence(bad, keys, "plainrepro.TestF", self.id[NEW], self.prov[NEW], DATA, ENV)
                d = self.decide(prev, cur, NEW)
                self.assertEqual(d.status, ev.UNVERIFIABLE)
                self.assertIn("ANALYSIS_INCOMPLETE", d.reasons)

    def test_unknown_or_missing_extractor_identity(self):
        other = "typeddeps:sha256:" + "0" * 64
        g, keys = extract("plain/a", NEW)
        prev = be.build_evidence(g, keys, "plainrepro.TestF", other, {}, DATA, ENV, verdict="pass")
        cur = be.build_evidence(g, keys, "plainrepro.TestF", other, {}, DATA, ENV)
        self.assertEqual(self.decide(prev, cur, NEW).reasons, ("EXTRACTOR_NOT_ALLOWED",))
        prev2 = self.evidence("plain/a", "plainrepro.TestF", NEW, "pass")
        self.assertIn("EXTRACTOR_MISMATCH", self.decide(prev2, cur, NEW).reasons)
        with self.assertRaises(be.BuilderError):
            be.extractor_identity(os.path.join(TD, "no-such-extractor"))

    def test_complete_verified_and_a_changed_dependency_needs_a_rerun(self):
        prev = self.evidence("plain/a", "plainrepro.TestF", NEW, "pass")
        cur = self.evidence("plain/c", "plainrepro.TestF", NEW)  # F changed
        d = self.decide(prev, cur, NEW)
        self.assertEqual((d.status, d.reasons), (ev.RERUN_REQUIRED, ("CODE_CHANGED",)))

    def test_a_test_with_no_node_is_unverifiable(self):
        prev = self.evidence("plain/a", "plainrepro.TestF", NEW, "pass")
        cur = self.evidence("plain/a", "plainrepro.TestNoSuch", NEW)
        self.assertIn("SUBJECT_NODE_MISSING", self.decide(prev, cur, NEW).reasons)


if __name__ == "__main__":
    unittest.main()
