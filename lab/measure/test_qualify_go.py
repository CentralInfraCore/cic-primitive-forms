"""The per-construct qualification of the Go extractor build (aliases, generics), its negative controls, and the chain to the kernel.

    TYPEDDEPS_BIN=path/to/typeddeps python3 -m unittest lab/measure/test_qualify_go.py     (needs `go` on PATH)

What is claimed is only what the probes of qualify.py cover. The RED -> GREEN of dynamic dispatch (generics, promoted methods, slice elements,
fields) is shown on two builds of the same source: the extractor as it is, and the same binary with the rules planted away.
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
NEW = ()
NO_CONV, NO_IMPL = ("-planted-no-conversions",), ("-planted-no-implementers",)
NO_TYPEARGS, OLD_METHODS = ("-planted-no-typeargs",), ("-planted-old-methodsof",)
BUILDS = (NEW, NO_CONV, NO_IMPL, NO_TYPEARGS, OLD_METHODS)
DATA = hashlib.sha256(b"no data read").hexdigest()
ENV = hashlib.sha256(subprocess.run(["go", "version"], capture_output=True, text=True).stdout.encode()).hexdigest() if BIN else ""
GENERIC = (("go-generic-fmt", "genfmt"), ("go-generic-ptr", "genptr"), ("go-generic-promoted", "genprom"), ("go-generic-pass", "genpass"),
           ("go-generic-slice", "genslice"), ("go-generic-type", "gentype"))
CONV = (("go-conv-promoted", "convprom"), ("go-conv-slice", "convslice"), ("go-conv-field", "convfield"))


def extract(rel, args):
    g = json.loads(subprocess.run([os.path.abspath(BIN), *args, "."], cwd=os.path.join(TD, rel), check=True, capture_output=True,
                                  text=True).stdout)
    return g, merkle.keys(g)[0]


def passes(rel):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(TD, rel), capture_output=True).returncode == 0


def failing(args):
    return [n for ps in q.construct_probes(BIN, args).values() for n, ok, _ in ps if not ok] + [n for n, ok, _ in q.probes(BIN, args) if not ok]


@unittest.skipUnless(BIN, "TYPEDDEPS_BIN is not set")
class QualifyGo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.id, cls.prov, cls.assurance = {}, {}, {}
        for args in BUILDS:
            cls.id[args], cls.prov[args] = be.extractor_identity(BIN, args)
            cls.assurance[args] = ev.Assurance.of([ev.CONTRACT_V2], [be.RULE_VERSION], {cls.id[args]: q.qualify(BIN, args)})

    def evidence(self, rel, pkg, args, verdict=None):
        g, keys = extract(rel, args)
        return be.build_evidence(g, keys, pkg + ".TestG", self.id[args], self.prov[args], DATA, ENV, verdict=verdict)

    def decide(self, rel_prev, rel_cur, pkg, args=NEW):
        prev, cur = self.evidence(rel_prev, pkg, args, "pass"), self.evidence(rel_cur, pkg, args)
        return prev, cur, ev.evaluate_reuse(prev, cur, self.assurance[args])

    # --- what the probes decide ----------------------------------------------------------------------------------------------

    def test_the_build_as_it_is_passes_every_probe(self):
        self.assertEqual(failing(NEW), [])
        got = q.qualify(BIN, NEW)
        for c in ("go.type.alias", "go.func.generic", "go.type.generic"):
            self.assertIn(c, got)

    def test_negative_controls_the_probes_can_fail(self):
        # no edges from an instantiation to the type arguments: the generic probes that dispatch through the type parameter fail,
        # the construct is left out, and nothing else is (the generic function is itself in the closure that reaches it)
        got = q.qualify(BIN, NO_TYPEARGS)
        self.assertNotIn("go.func.generic", got)
        self.assertNotIn("go.type.generic", got)
        self.assertIn("go.type.alias", got)
        self.assertGreaterEqual(len(failing(NO_TYPEARGS)), 6)
        # The edges from a call of an interface method to its implementers (-planted-no-implementers) are NOT shown to matter by any
        # probe any more: the instance rule gives the declaration that instantiates a generic function an edge to the methods of the
        # type argument, so the constraint probe holds without them (it failed, before the instance rule existed). The rule is covered
        # by the coverage check of RESULTS.md section 3 and by no probe; this assertion says so instead of leaving it unsaid.
        self.assertEqual(failing(NO_IMPL), [])
        # the conversion rule before the fix: a REQUIRED probe fails, so the build is qualified for nothing
        self.assertEqual(q.qualify(BIN, OLD_METHODS), frozenset())
        self.assertEqual(sorted(n for n, ok, _ in q.probes(BIN, OLD_METHODS) if not ok),
                         ["a promoted method reached by fmt", "a slice element reached by fmt", "a struct field reached by fmt"])
        # no conversion edges at all: the same, and the alias probe through fmt fails too
        self.assertEqual(q.qualify(BIN, NO_CONV), frozenset())
        names = {n: ok for ps in q.construct_probes(BIN, NO_CONV).values() for n, ok, _ in ps}
        self.assertFalse(names["a value of the alias type is formatted by fmt, which calls String on the target"])
        # every build is a different extractor identity
        self.assertEqual(len({self.id[a] for a in BUILDS}), len(BUILDS))

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

    # --- dynamic dispatch: RED -> GREEN ----------------------------------------------------------------------------------------------

    def test_red_the_build_without_the_type_argument_rule(self):
        for rel, pkg in GENERIC:
            with self.subTest(rel):
                prev, cur, d = self.decide(rel + "/a", rel + "/b", pkg, NO_TYPEARGS)
                # the dangerous situation, from real source: equal keys, an earlier pass, the test now fails
                self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
                self.assertFalse(passes(rel + "/b"))
                self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))

    def test_red_the_build_with_the_old_conversion_rule_is_refused_whole(self):
        for rel, pkg in CONV:
            with self.subTest(rel):
                prev, cur, d = self.decide(rel + "/a", rel + "/b", pkg, OLD_METHODS)
                self.assertEqual(prev["code_closure_digest"], cur["code_closure_digest"])
                self.assertLessEqual(set(cur["graph"]["constructs"]), {"go.func", "go.type"})  # nothing unusual in what was reported
                self.assertEqual((d.status, d.reasons), (ev.UNVERIFIABLE, ("CONSTRUCT_NOT_VERIFIED",)))

    def test_green_the_build_as_it_is(self):
        for rel, pkg in GENERIC + CONV + (("go-generic-impl", "genimpl"), ("go-generic-iface", "geniface")):
            with self.subTest(rel):
                same = self.decide(rel + "/a", rel + "/a", pkg)[2]
                self.assertEqual((same.status, same.reasons), (ev.REUSE_ELIGIBLE, (ev.ALL_CONDITIONS_HOLD,)))
                prev, cur, d = self.decide(rel + "/a", rel + "/b", pkg)
                self.assertFalse(passes(rel + "/b"))
                self.assertNotEqual(prev["code_closure_digest"], cur["code_closure_digest"])
                self.assertEqual((d.status, d.reasons), (ev.RERUN_REQUIRED, ("CODE_CHANGED",)))


if __name__ == "__main__":
    unittest.main()
