"""End to end for Rust: real source -> rust-analyzer's SCIP index -> scipdeps -> evidence builder -> kernel. No construct is written by
hand; the Assurance of the extractor identity is MEASURED (qualify_rust.py) and is the Rust one only.

    SCIPDEPS_BIN=path/to/scipdeps RUST_TOOLCHAIN=1.99.1 python3 -m unittest lab/measure/test_e2e_rust.py

The identity of a Rust extractor is its binary AND the rust-analyzer that makes its input, so an Assurance of the Go extractor cannot
authorise Rust evidence, nor the other way round.
"""
import collections
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import build_evidence as be  # noqa: E402
import evidence as ev  # noqa: E402
import merkle  # noqa: E402
import qualify_rust as qr  # noqa: E402
import rust_graph as rg  # noqa: E402

BIN = os.environ.get("SCIPDEPS_BIN")
TC = os.environ.get("RUST_TOOLCHAIN")
HERE = os.path.dirname(os.path.abspath(__file__))
TD = os.path.join(HERE, "..", "testdata")
CRATE = os.path.join(HERE, "..", "..", "rust")
DATA = hashlib.sha256(b"no data read").hexdigest()
PLANTED = {"SCIPDEPS_PLANTED_NO_IMPL": "1"}
PROBE_TESTS = [t for _, t in qr.PROBES]


@unittest.skipUnless(BIN and TC, "SCIPDEPS_BIN and RUST_TOOLCHAIN are not set")
class EndToEndRust(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tc = rg.toolchain_identity(TC)
        cls.env_digest = hashlib.sha256(json.dumps({k: tc[k] for k in ("rustc", "rust-analyzer")}, sort_keys=True).encode()).hexdigest()
        chain = (("rust-analyzer", tc["rust-analyzer-path"]),)
        cls.ident, cls.prov = be.extractor_identity(BIN, (), name="scipdeps", chain=chain)
        cls.ident_planted, cls.prov_planted = be.extractor_identity(BIN, ("SCIPDEPS_PLANTED_NO_IMPL=1",), name="scipdeps", chain=chain)
        mk = lambda ident, env: ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {ident: qr.qualify(BIN, TC, env)})
        cls.assurance = mk(cls.ident, None)
        cls.assurance_planted = mk(cls.ident_planted, PLANTED)
        cls._graphs = {}

    @classmethod
    def graph(cls, rel, env=None):
        key = (rel, bool(env))
        if key not in cls._graphs:
            g = rg.graph(rel if os.path.isabs(rel) else os.path.join(TD, rel), BIN, TC, env)
            cls._graphs[key] = (g, merkle.keys(g)[0])
        return cls._graphs[key]

    def evidence(self, rel, subject, verdict=None, planted=False):
        g, keys = self.graph(rel, PLANTED if planted else None)
        ident, prov = (self.ident_planted, self.prov_planted) if planted else (self.ident, self.prov)
        return be.build_evidence(g, keys, subject, ident, prov, DATA, self.env_digest, verdict=verdict)

    def decide(self, prev, cur, planted=False):
        return ev.evaluate_reuse(prev, cur, self.assurance_planted if planted else self.assurance)

    # --- evidence from real Rust source ---------------------------------------------------------------------------------------

    def test_unchanged_qualified_constructs_are_eligible(self):
        for t in PROBE_TESTS:
            with self.subTest(t):
                prev = self.evidence("rust-probes/a", t, "pass")
                cur = self.evidence("rust-probes/a", t)
                self.assertEqual(prev["graph"]["analysis"], {"status": "COMPLETE", "unresolved": []})
                d = self.decide(prev, cur)
                self.assertEqual((d.status, d.reasons), (ev.REUSE_ELIGIBLE, (ev.ALL_CONDITIONS_HOLD,)))

    def test_qualified_constructs_and_a_changed_dependency_need_a_rerun(self):
        for t in PROBE_TESTS:
            with self.subTest(t):
                d = self.decide(self.evidence("rust-probes/a", t, "pass"), self.evidence("rust-probes/b", t))
                self.assertEqual((d.status, d.reasons), (ev.RERUN_REQUIRED, ("CODE_CHANGED",)))

    # --- the red cases ---------------------------------------------------------------------------------------------------------

    def test_a_macro_of_the_crate_is_recognised_and_not_qualified(self):
        t = "tests/m::t()."
        prev, cur = self.evidence("rust-macro/a", t, "pass"), self.evidence("rust-macro/b", t)
        # the dangerous situation, from real source: equal keys, an earlier pass, the test now fails
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        self.assertTrue(rg.passes(os.path.join(TD, "rust-macro", "a"), TC))
        self.assertFalse(rg.passes(os.path.join(TD, "rust-macro", "b"), TC))
        self.assertIn("rs.macro.local", cur["graph"]["constructs"])
        d = self.decide(prev, cur)
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))
        # vouching from outside would be the way to a false reuse: the barrier is the assurance
        vouched = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {self.ident: self.assurance.extractors[self.ident] | {"rs.macro.local"}})
        self.assertEqual(ev.evaluate_reuse(prev, cur, vouched).status, ev.REUSE_ELIGIBLE)

    def test_the_extractor_without_the_impl_edges_is_not_qualified_and_its_evidence_is_refused(self):
        # SCIPDEPS_PLANTED_NO_IMPL: the key of t_trait_impl does not move although the impl does
        t = "tests/probe::t_trait_impl()."
        prev, cur = self.evidence("rust-probes/a", t, "pass", planted=True), self.evidence("rust-probes/b", t, planted=True)
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        d = self.decide(prev, cur, planted=True)
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))
        self.assertNotEqual(self.ident, self.ident_planted)

    # --- the boundary ------------------------------------------------------------------------------------------------------------

    def test_a_go_assurance_does_not_authorise_rust_evidence(self):
        go_only = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {"typeddeps:sha256:" + "0" * 64: ["go.func", "go.method", "rs.fn"]})
        t = PROBE_TESTS[0]
        d = ev.evaluate_reuse(self.evidence("rust-probes/a", t, "pass"), self.evidence("rust-probes/a", t), go_only)
        self.assertEqual(d.reasons, ("EXTRACTOR_NOT_ALLOWED",))
        self.assertTrue(self.ident.startswith("scipdeps:") and "typeddeps" not in self.ident)

    def test_an_unknown_rust_identity_is_refused(self):
        g, keys = self.graph("rust-probes/a")
        t = PROBE_TESTS[0]
        other = "scipdeps:sha256:" + "0" * 64
        prev = be.build_evidence(g, keys, t, other, {}, DATA, self.env_digest, verdict="pass")
        cur = be.build_evidence(g, keys, t, other, {}, DATA, self.env_digest)
        self.assertEqual(self.decide(prev, cur).reasons, ("EXTRACTOR_NOT_ALLOWED",))

    # --- the crate itself ---------------------------------------------------------------------------------------------------------

    def test_the_tests_of_the_crate(self):
        g, keys = self.graph(CRATE)
        entries = [n["id"] for n in g["nodes"] if n.get("entry")]
        macro_made = {n["id"] for n in g["nodes"] if n["analysis"]["status"] != "COMPLETE"}
        count, reasons = collections.Counter(), collections.Counter()
        for t in entries:
            prev = be.build_evidence(g, keys, t, self.ident, self.prov, DATA, self.env_digest, verdict="pass")
            cur = be.build_evidence(g, keys, t, self.ident, self.prov, DATA, self.env_digest)
            d = ev.evaluate_reuse(prev, cur, self.assurance)
            count[d.status] += 1
            reasons[d.reasons] += 1
            self.assertNotEqual(d.status, ev.RERUN_REQUIRED)  # the same keys
            if t in macro_made:
                self.assertEqual((d.status, "ANALYSIS_INCOMPLETE" in d.reasons), (ev.UNVERIFIABLE, True), t)
            else:
                self.assertNotIn("ANALYSIS_INCOMPLETE", d.reasons)
        print("\nrust crate, tests with unchanged keys: %s; reasons: %s" % (dict(count), {",".join(k): v for k, v in reasons.items()}),
              file=sys.stderr)
        self.assertEqual(len(entries), sum(count.values()))
        self.assertGreater(count[ev.REUSE_ELIGIBLE], 0)


if __name__ == "__main__":
    unittest.main()
