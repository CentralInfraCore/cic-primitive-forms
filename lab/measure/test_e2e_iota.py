"""End to end: real Go source -> the extractor -> the evidence builder -> the kernel. No `constructs` is written by hand.

    TYPEDDEPS_BIN=path/to/typeddeps python3 -m unittest lab/measure/test_e2e_iota.py     (needs `go` on PATH)

The Assurance below is what a reviewer outside the extractor would hold for THIS binary: it is verified for the constructs listed
and has not been verified for implicit iota, which it recognises (reports) but does not model. The test shows that a construct the
extractor recognises and that is not verified cannot lead to a reuse authorisation, however equal the Merkle keys are.
"""
import copy
import hashlib
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402

BIN = os.environ.get("TYPEDDEPS_BIN")
TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata")
VERIFIED = ["go.func", "go.method", "go.type", "go.var", "go.const.explicit"]  # NOT go.const.iota, go.const.implicit_repeat, generics


def extract(rel):
    import json
    d = os.path.join(TD, rel)
    g = json.loads(subprocess.run([os.path.abspath(BIN), "."], cwd=d, check=True, capture_output=True, text=True).stdout)
    return g, merkle.keys(g)[0]


def passes(rel):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(TD, rel), capture_output=True).returncode == 0


ENV = hashlib.sha256(subprocess.run(["go", "version"], capture_output=True, text=True).stdout.encode()).hexdigest() if BIN else ""
DATA = hashlib.sha256(b"no data read").hexdigest()


@unittest.skipUnless(BIN, "TYPEDDEPS_BIN is not set")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ident, cls.prov = be.extractor_identity(BIN)
        cls.assurance = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {cls.ident: VERIFIED})

    def evidence(self, rel, subject, verdict=None, graph=None, ident=None):
        g, keys = extract(rel)
        return be.build_evidence(graph or g, keys, subject, ident or self.ident, self.prov, DATA, ENV, verdict=verdict)

    def decide(self, prev, cur, assurance=None):
        return ev.evaluate_reuse(prev, cur, assurance or self.assurance)

    def test_iota_is_recognised_and_not_verified_so_it_is_unverifiable(self):
        prev = self.evidence("iota/a", "iotarepro.TestB", "pass" if passes("iota/a") else "fail")
        cur = self.evidence("iota/b", "iotarepro.TestB")
        # the dangerous situation, from the real source: the keys are equal, the earlier result is a pass, the test now fails
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        self.assertEqual(prev["verdict"], "pass")
        self.assertFalse(passes("iota/b"))
        # the extractor reported the construct; nobody wrote it into a fixture
        self.assertIn("go.const.implicit_repeat", cur["graph"]["constructs"])
        self.assertEqual(cur["graph"]["analysis"], {"status": "COMPLETE", "unresolved": []})
        d = self.decide(prev, cur)
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))

    def test_what_the_extractor_reports_is_not_what_authorises(self):
        # Vouching for the construct from outside would be the one way to a false reuse: the barrier is the assurance.
        prev = self.evidence("iota/a", "iotarepro.TestB", "pass")
        cur = self.evidence("iota/b", "iotarepro.TestB")
        vouched = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {self.ident: VERIFIED + ["go.const.implicit_repeat"]})
        self.assertEqual(self.decide(prev, cur, vouched).status, ev.REUSE_ELIGIBLE)

    def test_generics_are_recognised_and_not_verified_either(self):
        a = self.evidence("generic", "genericrepro.TestId", "pass")
        b = self.evidence("generic", "genericrepro.TestId")
        self.assertIn("go.func.generic", a["graph"]["constructs"])
        self.assertEqual(self.decide(a, b).reasons, ("CONSTRUCT_NOT_VERIFIED",))

    def test_incomplete_analysis_is_unverifiable(self):
        g, keys = extract("plain/a")
        prev = be.build_evidence(g, keys, "plainrepro.TestF", self.ident, self.prov, DATA, ENV, verdict="pass")
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
                cur = be.build_evidence(bad, keys, "plainrepro.TestF", self.ident, self.prov, DATA, ENV)
                d = self.decide(prev, cur)
                self.assertEqual(d.status, ev.UNVERIFIABLE)
                self.assertIn("ANALYSIS_INCOMPLETE", d.reasons)

    def test_unknown_or_missing_extractor_identity(self):
        prev = self.evidence("plain/a", "plainrepro.TestF", "pass")
        cur = self.evidence("plain/a", "plainrepro.TestF", ident="typeddeps:sha256:" + "0" * 64)
        prev2 = self.evidence("plain/a", "plainrepro.TestF", "pass", ident="typeddeps:sha256:" + "0" * 64)
        self.assertEqual(self.decide(prev2, cur).reasons, ("EXTRACTOR_NOT_ALLOWED",))
        self.assertIn("EXTRACTOR_MISMATCH", self.decide(prev, cur).reasons)
        with self.assertRaises(be.BuilderError):
            be.extractor_identity(os.path.join(TD, "no-such-extractor"))

    def test_complete_verified_and_equal_keys_is_eligible(self):
        prev = self.evidence("plain/a", "plainrepro.TestF", "pass")
        cur = self.evidence("plain/b", "plainrepro.TestF")  # G changed; TestF does not reach it
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        self.assertEqual(prev["graph"]["analysis"], {"status": "COMPLETE", "unresolved": []})
        d = self.decide(prev, cur)
        self.assertEqual((d.status, d.reasons), (ev.REUSE_ELIGIBLE, (ev.ALL_CONDITIONS_HOLD,)))

    def test_complete_verified_and_a_changed_dependency_needs_a_rerun(self):
        prev = self.evidence("plain/a", "plainrepro.TestF", "pass")
        cur = self.evidence("plain/c", "plainrepro.TestF")  # F changed
        d = self.decide(prev, cur)
        self.assertEqual((d.status, d.reasons), (ev.RERUN_REQUIRED, ("CODE_CHANGED",)))

    def test_a_test_with_no_node_is_unverifiable(self):
        prev = self.evidence("plain/a", "plainrepro.TestF", "pass")
        cur = self.evidence("plain/a", "plainrepro.TestNoSuch")
        self.assertIn("SUBJECT_NODE_MISSING", self.decide(prev, cur).reasons)


if __name__ == "__main__":
    unittest.main()
