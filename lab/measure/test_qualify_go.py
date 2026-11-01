"""The per-construct qualification of the Go extractor build (aliases, generics), its negative controls, and the chain to the kernel.

    TYPEDDEPS_BIN=path/to/typeddeps python3 -m unittest lab/measure/test_qualify_go.py     (needs `go` on PATH)

What is claimed is only what the probes of qualify.py cover; the generic probe that fails is a known red case (testdata/go-generic-fmt).
"""
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
NEW, NO_CONV, NO_IMPL = (), ("-planted-no-conversions",), ("-planted-no-implementers",)
DATA = hashlib.sha256(b"no data read").hexdigest()
ENV = hashlib.sha256(subprocess.run(["go", "version"], capture_output=True, text=True).stdout.encode()).hexdigest() if BIN else ""


def extract(rel, args):
    g = json.loads(subprocess.run([os.path.abspath(BIN), *args, "."], cwd=os.path.join(TD, rel), check=True, capture_output=True,
                                  text=True).stdout)
    return g, merkle.keys(g)[0]


def passes(rel):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(TD, rel), capture_output=True).returncode == 0


@unittest.skipUnless(BIN, "TYPEDDEPS_BIN is not set")
class QualifyGo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.id, cls.prov, cls.assurance = {}, {}, {}
        for args in (NEW, NO_CONV, NO_IMPL):
            cls.id[args], cls.prov[args] = be.extractor_identity(BIN, args)
            cls.assurance[args] = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {cls.id[args]: q.qualify(BIN, args)})

    def evidence(self, rel, pkg, args, verdict=None):
        g, keys = extract(rel, args)
        return be.build_evidence(g, keys, pkg + ".TestG", self.id[args], self.prov[args], DATA, ENV, verdict=verdict)

    def decide(self, rel_prev, rel_cur, pkg, args=NEW):
        prev, cur = self.evidence(rel_prev, pkg, args, "pass"), self.evidence(rel_cur, pkg, args)
        return prev, cur, ev.evaluate_reuse(prev, cur, self.assurance[args])

    # --- what the probes decide ----------------------------------------------------------------------------------------------

    def test_aliases_are_qualified_and_generics_are_not(self):
        got = q.qualify(BIN, NEW)
        self.assertIn("go.type.alias", got)
        self.assertNotIn("go.func.generic", got)
        failing = [n for c, ps in q.construct_probes(BIN, NEW).items() for n, ok, _ in ps if not ok]
        self.assertEqual(len(failing), 1)
        self.assertIn("formats a value of its type parameter with fmt", failing[0])

    def test_negative_controls_the_probes_can_fail(self):
        # without the interface conversion edges the alias probe through fmt fails, and the alias is no longer qualified
        self.assertNotIn("go.type.alias", q.qualify(BIN, NO_CONV))
        names = {n: ok for ps in q.construct_probes(BIN, NO_CONV).values() for n, ok, _ in ps}
        self.assertFalse(names["a value of the alias type is formatted by fmt, which calls String on the target"])
        # without the edges to the implementers of an interface method the generic constraint probe fails
        names = {n: ok for ps in q.construct_probes(BIN, NO_IMPL).values() for n, ok, _ in ps}
        self.assertFalse(names["a generic function calls a method of its constraint; the implementer changes"])
        # and neither build is admitted under the identity of the other
        self.assertEqual(len({self.id[NEW], self.id[NO_CONV], self.id[NO_IMPL]}), 3)

    # --- aliases, through the kernel -----------------------------------------------------------------------------------------------

    def test_alias_unchanged_is_eligible_and_changed_needs_a_rerun(self):
        for rel, pkg in (("go-alias-target", "aliastarget"), ("go-alias-method", "aliasmethod"), ("go-alias-conv", "aliasconv")):
            with self.subTest(rel):
                prev, cur, d = self.decide(rel + "/a", rel + "/a", pkg)
                self.assertIn("go.type.alias", cur["graph"]["constructs"])
                self.assertEqual((d.status, d.reasons), (ev.REUSE_ELIGIBLE, (ev.ALL_CONDITIONS_HOLD,)))
                self.assertFalse(passes(rel + "/b"))  # the value did move
                d = self.decide(rel + "/a", rel + "/b", pkg)[2]
                self.assertEqual((d.status, d.reasons), (ev.RERUN_REQUIRED, ("CODE_CHANGED",)))

    def test_the_alias_build_that_lost_a_rule_is_refused(self):
        prev, cur, d = self.decide("go-alias-conv/a", "go-alias-conv/b", "aliasconv", NO_CONV)
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])  # the false reuse it would give
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))

    # --- generics: the red case ----------------------------------------------------------------------------------------------------

    def test_red_a_generic_function_that_formats_its_argument(self):
        prev, cur, d = self.decide("go-generic-fmt/a", "go-generic-fmt/b", "genfmt")
        # the dangerous situation, from real source: equal keys, an earlier pass, the test now fails
        self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
        self.assertTrue(passes("go-generic-fmt/a"))
        self.assertFalse(passes("go-generic-fmt/b"))
        self.assertIn("go.func.generic", cur["graph"]["constructs"])
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))

    def test_generics_stay_unverifiable_even_where_the_probe_passes(self):
        # one generic construct, qualified as a whole or not at all: the implementer case passes its probe but the construct does not qualify
        d = self.decide("go-generic-impl/a", "go-generic-impl/a", "genimpl")[2]
        self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))


if __name__ == "__main__":
    unittest.main()
