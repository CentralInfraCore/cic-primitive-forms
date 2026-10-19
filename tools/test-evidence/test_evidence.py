"""Tests and probes of the test evidence tools. Every trust boundary of the verifier has a probe that breaks it and
requires the refusal; a verifier that passes all of them is a candidate, one without a probe for a case is not trusted
for that case. The canonical JSON is held by vectors/canon.json, which canon-go/ reads as well (evidence.py selftest)."""
import copy
import datetime
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import canon  # noqa: E402
import key as keymod  # noqa: E402
import record  # noqa: E402
import verify  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
C1, C2, C3, TREE = "1" * 40, "2" * 40, "3" * 40, "4" * 40
ENV = {
    "rustc": "1.99.0", "cargo": "1.99.0", "go": "go1.27.2", "python": "3.12.3",
    "tools": {"cargo-llvm-cov": "0.9.1", "cargo-mutants": "27.1.0", "go-mutesting": "0.0.0-20261002", "cargo-fuzz": "0.13.2"},
    "runner": {"image_os": "ubuntu24", "image_version": "20261001.1", "os": "Linux", "arch": "X64"},
    "actions": {"actions/checkout": "a" * 40, "actions/setup-go": "b" * 40},
}
CHECKS = json.load(open(os.path.join(HERE, "required-checks.json")))
FILES = ["rust/Cargo.toml", "rust/Cargo.lock", "rust/fuzz/Cargo.toml", "rust/fuzz/Cargo.lock", "go/go.mod", "go/go.sum",
         "coverage/tools.yaml", "coverage/scope.yaml", "tools/quality_gate.py", "tools/test-evidence/x.py", "corpus/pins.yaml",
         "testdata/p1/a.json", ".github/workflows/p1.yml", ".github/actions/a/action.yml"]


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Fixture:
    """A checkout with the files the key reads, an environment, and records made for it."""

    def __init__(self, tc):
        self.tmp = tempfile.TemporaryDirectory()
        tc.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        for f in FILES:
            self.write(f, "content of " + f)
        self.env = copy.deepcopy(ENV)

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)

    def key(self, env=None):
        return keymod.validity_key(self.root, env or self.env)

    def record(self, measured=None, commit=C1, outcome="pass", slot=None, tree=TREE, kind="main", **over):
        k, named = self.key()
        measured = measured or NOW - datetime.timedelta(hours=2)
        slot = slot if slot is not None else (measured.date().isoformat() if kind == "main" else None)
        rec = {
            "schema": "test-evidence/1", "kind": "full", "outcome": outcome, "slot": slot,
            "target": {"kind": kind, "commit": commit, "tree": tree,
                       "base_commit": None if kind == "main" else C2, "head_commit": None if kind == "main" else C3},
            "validity_key": k, "validity_inputs": named, "environment": self.env,
            "run": {"repository": "CentralInfraCore/cic-primitive-forms", "workflow_path": ".github/workflows/evidence.yml",
                    "ref": "refs/heads/main", "run_id": 1, "run_attempt": 1, "commit": commit},
            "measured_at": ts(measured),
            "quality_full": {
                "rust_lines": {"covered": 98, "total": 100}, "go_statements": {"covered": 100, "total": 100},
                "rust_branches": {"covered": 97, "total": 100}, "go_branches": {"covered": 100, "total": 100},
                "rust_mutation": {"killed": 99, "survived": 1, "unviable": 3}, "go_mutation": {"killed": 97, "survived": 3, "uncompilable": 2},
                "thresholds": {"line": 90, "mutation": 90, "branch": 90},
                "gate_verdict": "PASS" if outcome == "pass" else "FAIL", "shards": {"rust": 4, "go": 6}, "completeness": "complete"},
            "required_checks": [{"name": n, "conclusion": "success" if outcome == "pass" or i else "failure",
                                 "job_url": "https://github.com/x/y/actions/runs/1/job/%d" % i, "run_id": 1, "artifact_digest": None}
                                for i, n in enumerate(CHECKS)],
            "anchor": outcome == "pass", "mutants": None, "coverage_map": None, "artifacts": [],
        }
        rec.update(over)
        return rec


class Attested(verify.Attestation):
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def verify(self, record_path, digest, trust, source_commit):
        self.calls.append((digest, trust, source_commit))
        return self.ok


def raw_of(rec):
    raw = canon.canonicalize(rec)
    return raw, hashlib.sha256(raw).hexdigest() + ".json"


def context(fx, att=None, ancestor=True, now=NOW, artifacts=None, target=C2, **kw):
    return verify.Context(now=now, target_commit=target, root=fx.root, environment=fx.env, is_ancestor=lambda a, d: ancestor,
                          attestation=att or Attested(), expected_checks=CHECKS, artifacts=artifacts, **kw)


class CanonTests(unittest.TestCase):
    def test_the_vectors(self):
        doc = json.load(open(os.path.join(HERE, "vectors", "canon.json")))
        self.assertGreater(len(doc["cases"]), 50)
        for c in doc["cases"]:
            with self.subTest(c["name"]):
                data = bytes.fromhex(c["input_hex"])
                if c["refuse"]:
                    with self.assertRaises(canon.CanonError) as cm:
                        canon.parse_strict(data)
                    self.assertEqual(cm.exception.kind, c["refuse"])
                    continue
                want = bytes.fromhex(c["canonical_hex"])
                self.assertEqual(canon.canonicalize(canon.parse_strict(data)), want)
                if data == want:
                    canon.read_canonical(data)
                else:
                    with self.assertRaises(canon.CanonError) as cm:
                        canon.read_canonical(data)
                    self.assertEqual(cm.exception.kind, "NOT_CANONICAL")

    def test_the_writer_refuses_what_the_format_has_no_place_for(self):
        for name, value in (("a float", 1.5), ("a big integer", 2**53), ("a lone surrogate", "\ud800"),
                            ("a name that is not a string", {1: 2}), ("a set", {1}), ("depth 65", [[]] * 1 and eval("[" * 65 + "]" * 65))):
            with self.subTest(name), self.assertRaises(canon.CanonError):
                canon.canonicalize(value)

    def test_go_gives_the_same_bytes_for_a_record(self):
        fx = Fixture(self)
        raw, _ = raw_of(fx.record())
        try:
            subprocess.run(["go", "version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no go")
        out = subprocess.run(["go", "run", ".", "canon"], cwd=os.path.join(HERE, "canon-go"), input=raw, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, raw)


class KeyTests(unittest.TestCase):
    def test_it_is_stable_and_every_class_of_input_moves_it(self):
        fx = Fixture(self)
        base = fx.key()[0]
        self.assertEqual(base, fx.key()[0])
        for rel in ("rust/Cargo.lock", "go/go.sum", "coverage/tools.yaml", "tools/quality_gate.py", ".github/workflows/p1.yml",
                    ".github/actions/a/action.yml", "corpus/pins.yaml", "testdata/p1/a.json"):
            with self.subTest(rel):
                fx.write(rel, "changed")
                self.assertNotEqual(base, fx.key()[0])
                fx.write(rel, "content of " + rel)
                self.assertEqual(base, fx.key()[0])
        for name, edit in (("the rustc version", lambda e: e.update(rustc="1.99.1")),
                           ("the runner image", lambda e: e["runner"].update(image_version="20261002.1")),
                           ("an action revision", lambda e: e["actions"].update({"actions/checkout": "c" * 40})),
                           ("a tool version", lambda e: e["tools"].update({"cargo-mutants": "27.2.0"})),
                           ("a new action", lambda e: e["actions"].update({"actions/cache": "d" * 40}))):
            with self.subTest(name):
                env = copy.deepcopy(fx.env)
                edit(env)
                self.assertNotEqual(base, fx.key(env)[0])

    def test_the_selector_the_schema_and_the_pattern_list_are_inputs(self):
        fx = Fixture(self)
        base = fx.key()[0]
        old = keymod.SELECTOR_VERSION
        self.addCleanup(setattr, keymod, "SELECTOR_VERSION", old)
        keymod.SELECTOR_VERSION = old + 1
        self.assertNotEqual(base, fx.key()[0])
        keymod.SELECTOR_VERSION = old
        pats = keymod.load_patterns()
        pats["include"] = pats["include"] + ["go/go.mod"]  # a pattern that adds no file still changes the list
        self.assertNotEqual(base, keymod.validity_key(fx.root, fx.env, pats)[0])

    def test_generated_files_do_not_move_it(self):
        fx = Fixture(self)
        base = fx.key()[0]
        fx.write("tools/__pycache__/x.pyc", "junk")
        fx.write("tools/test-evidence/__pycache__/y.pyc", "junk")
        fx.write("rust/target/debug/z", "junk")
        fx.write("go/testdata/rapid/q.fail", "junk")
        self.assertEqual(base, fx.key()[0])

    def test_it_fails_closed(self):
        fx = Fixture(self)
        pats = keymod.load_patterns()
        for name, bad_pats, exc in (("a pattern that matches nothing", dict(pats, include=pats["include"] + ["nothing/**"]), "NO_MATCH"),):
            with self.subTest(name), self.assertRaises(keymod.KeyError_) as cm:
                keymod.validity_key(fx.root, fx.env, bad_pats)
            self.assertEqual(cm.exception.kind, exc)
        os.symlink("go.mod", os.path.join(fx.root, "go/link.mod"))
        os.makedirs(os.path.join(fx.root, "tools"), exist_ok=True)
        os.symlink("../go/go.sum", os.path.join(fx.root, "tools/link"))
        with self.assertRaises(keymod.KeyError_) as cm:
            fx.key()
        self.assertEqual(cm.exception.kind, "NOT_A_REGULAR_FILE")
        os.unlink(os.path.join(fx.root, "tools/link"))
        for label, edit in (("a label for the toolchain", lambda e: e.update(rustc="stable")),
                            ("a tag for an action", lambda e: e["actions"].update({"actions/checkout": "v4"})),
                            ("a missing tool", lambda e: e["tools"].pop("cargo-fuzz")),
                            ("an extra member", lambda e: e.update(note="x"))):
            env = copy.deepcopy(fx.env)
            edit(env)
            os.unlink(os.path.join(fx.root, "go/link.mod"))
            with self.subTest(label), self.assertRaises(keymod.KeyError_) as cm:
                fx.key(env)
            self.assertEqual(cm.exception.kind, "ENVIRONMENT")
            os.symlink("go.mod", os.path.join(fx.root, "go/link.mod"))
        os.unlink(os.path.join(fx.root, "go/link.mod"))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def refuses(self, rec, kind=None, checks=CHECKS):
        with self.assertRaises(record.RecordError) as cm:
            record.validate(rec, checks)
        if kind:
            self.assertEqual(cm.exception.kind, kind)

    def test_a_good_record(self):
        record.validate(self.fx.record(), CHECKS)
        record.validate(self.fx.record(outcome="fail"), CHECKS)
        record.validate(self.fx.record(kind="merge_candidate"), CHECKS)

    def test_a_member_missing_or_extra_at_any_level(self):
        rec = self.fx.record()
        paths = [("schema",), ("target", "tree"), ("run", "commit"), ("quality_full", "rust_lines", "total"),
                 ("environment", "runner", "arch"), ("required_checks", 0, "conclusion")]
        for path in paths:
            with self.subTest("missing", path=path):
                bad = copy.deepcopy(rec)
                node = bad
                for p in path[:-1]:
                    node = node[p]
                del node[path[-1]]
                self.refuses(bad, "MISSING")
        for path in ((), ("target",), ("quality_full",), ("quality_full", "thresholds"), ("environment", "tools"), ("required_checks", 0)):
            with self.subTest("extra", path=path):
                bad = copy.deepcopy(rec)
                node = bad
                for p in path:
                    node = node[p]
                node["surprise"] = 1
                self.refuses(bad, "EXTRA")

    def test_wrong_types_and_values(self):
        rec = self.fx.record()
        edits = {
            "a bool where an integer is": lambda r: r["run"].update(run_id=True),
            "a negative count": lambda r: r["quality_full"]["rust_lines"].update(covered=-1),
            "a float": lambda r: r["quality_full"]["rust_lines"].update(covered=1.5),
            "a short commit": lambda r: r["target"].update(commit="abc"),
            "an upper case digest": lambda r: r.update(validity_key="A" * 64),
            "a time with a zone offset": lambda r: r.update(measured_at="2026-10-20T10:00:00+00:00"),
            "a time with a fraction": lambda r: r.update(measured_at="2026-10-20T10:00:00.5Z"),
            "a date that does not exist": lambda r: r.update(slot="2026-02-30"),
            "an unknown schema": lambda r: r.update(schema="test-evidence/2"),
            "an unknown kind": lambda r: r.update(kind="delta"),
            "an unknown conclusion": lambda r: r["required_checks"][0].update(conclusion="great"),
            "an anchor that is 1": lambda r: r.update(anchor=1),
            "a toolchain label": lambda r: r["environment"].update(rustc="stable"),
            "a runner label": lambda r: r["environment"]["runner"].update(image_version=""),
            "an action tag": lambda r: r["environment"]["actions"].update({"actions/checkout": "v4"}),
            "an empty action list": lambda r: r["environment"].update(actions={}),
            "covering more than there is": lambda r: r["quality_full"]["rust_lines"].update(covered=101),
            "an artifact listed twice": lambda r: r.update(artifacts=[{"name": "a", "sha256": "0" * 64, "size": 1}] * 2),
            "a check listed twice": lambda r: r["required_checks"].append(copy.deepcopy(r["required_checks"][0])),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                bad = copy.deepcopy(rec)
                edit(bad)
                self.refuses(bad)

    def test_the_consistency_rules(self):
        rec = self.fx.record()
        cases = {
            "pass, with a required check that failed": lambda r: r["required_checks"][1].update(conclusion="failure"),
            "pass, with a required check skipped": lambda r: r["required_checks"][1].update(conclusion="skipped"),
            "pass, with the gate failed": lambda r: r["quality_full"].update(gate_verdict="FAIL"),
            "pass, with the shards incomplete": lambda r: r["quality_full"].update(completeness="incomplete"),
            "pass, with a threshold below 90": lambda r: r["quality_full"]["thresholds"].update(mutation=50),
            "an anchor that is not": lambda r: r.update(anchor=False),
            "a merge candidate with a slot": lambda r: r["target"].update(kind="merge_candidate", base_commit=C2, head_commit=C3) or r.update(slot="2026-10-20"),
            "a record of main naming a base": lambda r: r["target"].update(base_commit=C2),
            "a record of main made on another commit": lambda r: r["run"].update(commit=C3),
            "inputs without the environment": lambda r: r["validity_inputs"].pop("environment"),
        }
        for name, edit in cases.items():
            with self.subTest(name):
                bad = copy.deepcopy(rec)
                edit(bad)
                self.refuses(bad, "CONSISTENCY" if "slot" not in name else None)
        failed = self.fx.record(outcome="fail")
        failed["quality_full"]["gate_verdict"] = "PASS"
        failed["required_checks"][0]["conclusion"] = "success"
        failed["anchor"] = False
        self.refuses(failed, "CONSISTENCY")  # fail, but everything passed

    def test_the_checks_have_to_be_the_expected_ones(self):
        rec = self.fx.record()
        self.refuses(rec, "CHECKS", checks=CHECKS + ["one more"])
        self.refuses(rec, "CHECKS", checks=CHECKS[1:])
        bad = copy.deepcopy(rec)
        bad["required_checks"][0]["name"] = "renamed"
        self.refuses(bad, "CHECKS")


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def code(self, fn, *a, **kw):
        with self.assertRaises(verify.VerifyError) as cm:
            fn(*a, **kw)
        return cm.exception.code

    def test_a_genuine_baseline(self):
        rec = self.fx.record()
        raw, name = raw_of(rec)
        att = Attested()
        v = verify.verify_baseline(raw, name, context(self.fx, att))
        self.assertEqual(v.digest + ".json", name)
        # the attestation was asked about exactly this record, this trust and the commit the record names
        self.assertEqual(att.calls, [(v.digest, verify.Trust(), rec["run"]["commit"])])
        verify.verify_baseline(raw, name, context(self.fx, target=C1, ancestor=False))  # the target itself

    def test_the_bytes_and_the_name(self):
        rec = self.fx.record()
        raw, name = raw_of(rec)
        ctx = context(self.fx)
        self.assertEqual(self.code(verify.verify_baseline, raw, "0" * 64 + ".json", ctx), "NAME_MISMATCH")
        self.assertEqual(self.code(verify.verify_baseline, raw, name[:-5], ctx), "NAME_MISMATCH")
        flipped = raw.replace(b'"run_id":1', b'"run_id":2')
        self.assertNotEqual(flipped, raw)
        self.assertEqual(self.code(verify.verify_baseline, flipped, name, ctx), "NAME_MISMATCH")  # one byte, still canonical
        spaced = raw.replace(b'"kind":"full"', b'"kind": "full"')
        self.assertEqual(self.code(verify.verify_baseline, spaced, hashlib.sha256(spaced).hexdigest() + ".json", ctx), "NOT_CANONICAL")
        unsorted = json.dumps(rec, sort_keys=False, separators=(",", ":")).encode()
        self.assertEqual(self.code(verify.verify_baseline, unsorted, hashlib.sha256(unsorted).hexdigest() + ".json", ctx), "NOT_CANONICAL")
        dup = raw[:-1] + b',"kind":"full"}'
        self.assertEqual(self.code(verify.verify_baseline, dup, hashlib.sha256(dup).hexdigest() + ".json", ctx), "NOT_JSON")
        for junk in (b"", b"{", b"\xef\xbb\xbf" + raw, raw + b"\n"):
            self.assertIn(self.code(verify.verify_baseline, junk, hashlib.sha256(junk).hexdigest() + ".json", ctx), ("NOT_JSON", "NOT_CANONICAL"))

    def test_the_attestation_is_a_boundary(self):
        raw, name = raw_of(self.fx.record())
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, Attested(ok=False))), "ATTESTATION")

        class Truthy(verify.Attestation):
            def verify(self, *a):
                return "yes"  # anything but True is a refusal
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, Truthy())), "ATTESTATION")

    def test_the_schema_is_checked_before_anything_is_trusted(self):
        rec = self.fx.record()
        rec["surprise"] = 1
        raw, name = raw_of(rec)
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx)), "SCHEMA")
        rec = self.fx.record()
        rec["required_checks"].pop()
        raw, name = raw_of(rec)
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx)), "SCHEMA")

    def test_only_a_passing_anchor_of_main_is_a_baseline(self):
        raw, name = raw_of(self.fx.record(outcome="fail"))
        ctx = context(self.fx)
        self.assertEqual(self.code(verify.verify_baseline, raw, name, ctx), "NOT_A_BASELINE")
        verify.verify_record(raw, name, ctx)  # but it is a genuine record, and the chain needs to see it
        raw, name = raw_of(self.fx.record(kind="merge_candidate"))
        self.assertEqual(self.code(verify.verify_baseline, raw, name, ctx), "NOT_A_BASELINE")

    def test_the_validity_key(self):
        raw, name = raw_of(self.fx.record())
        for rel in ("rust/Cargo.lock", "go/go.sum", "coverage/scope.yaml", "tools/quality_gate.py", ".github/workflows/p1.yml",
                    "corpus/pins.yaml", "testdata/p1/a.json"):
            with self.subTest(rel):
                self.fx.write(rel, "changed after the measurement")
                self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx)), "KEY_MISMATCH")
                self.fx.write(rel, "content of " + rel)
        for label, edit in (("a new rustc", lambda e: e.update(rustc="1.99.1")),
                            ("a new runner image", lambda e: e["runner"].update(image_version="20261002.1")),
                            ("another action revision", lambda e: e["actions"].update({"actions/checkout": "e" * 40}))):
            with self.subTest(label):
                ctx = context(self.fx)
                edit(ctx.environment)
                self.assertEqual(self.code(verify.verify_baseline, raw, name, ctx), "KEY_MISMATCH")
                ctx.environment.clear()
                ctx.environment.update(copy.deepcopy(ENV))
        # a key that does not belong to the inputs written in the record
        rec = self.fx.record()
        rec["validity_key"] = "0" * 64
        raw2, name2 = raw_of(rec)
        self.assertEqual(self.code(verify.verify_baseline, raw2, name2, context(self.fx)), "KEY_INCONSISTENT")
        # inputs and key rewritten to something else that is consistent in itself: still not the key of the target
        rec = self.fx.record()
        rec["validity_inputs"]["file:rust/Cargo.lock"] = "f" * 64
        rec["validity_key"] = keymod.key_of(rec["validity_inputs"])
        raw3, name3 = raw_of(rec)
        self.assertEqual(self.code(verify.verify_baseline, raw3, name3, context(self.fx)), "KEY_MISMATCH")

    def test_the_key_cannot_be_computed(self):
        raw, name = raw_of(self.fx.record())
        ctx = context(self.fx)
        ctx.environment["rustc"] = "stable"
        self.assertEqual(self.code(verify.verify_baseline, raw, name, ctx), "KEY_UNAVAILABLE")

    def test_ancestry(self):
        raw, name = raw_of(self.fx.record(commit=C1))
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, ancestor=False)), "NOT_ANCESTOR")
        verify.verify_baseline(raw, name, context(self.fx, ancestor=True))

    def test_age(self):
        measured = NOW - datetime.timedelta(days=3)
        raw, name = raw_of(self.fx.record(measured=measured))
        verify.verify_baseline(raw, name, context(self.fx))  # exactly three days is allowed
        late = NOW + datetime.timedelta(seconds=1)
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, now=late)), "EXPIRED")
        future = NOW + datetime.timedelta(hours=1)
        raw, name = raw_of(self.fx.record(measured=future))
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx)), "EXPIRED")  # a record from the future

    def test_artifacts(self):
        data = b"the coverage map"
        ref = {"digest": hashlib.sha256(data).hexdigest(), "size": len(data), "artifact": "coverage-map"}
        raw, name = raw_of(self.fx.record(coverage_map=ref))
        found = lambda n: data if n == "coverage-map" else None
        verify.verify_baseline(raw, name, context(self.fx, artifacts=found))
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, artifacts=lambda n: None)), "ARTIFACT_MISSING")
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx)), "ARTIFACT_MISSING")  # nobody to ask
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, artifacts=lambda n: data + b"!")), "ARTIFACT_DIGEST")
        wrong_size = dict(ref, size=len(data) + 1)
        raw, name = raw_of(self.fx.record(coverage_map=wrong_size))
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, artifacts=found)), "ARTIFACT_DIGEST")
        raw, name = raw_of(self.fx.record(mutants=ref))
        self.assertEqual(self.code(verify.verify_baseline, raw, name, context(self.fx, artifacts=lambda n: b"other")), "ARTIFACT_DIGEST")


class GhTests(unittest.TestCase):
    """The attestation check against a `gh` that insists on exact arguments: no network, no GitHub."""

    def fake_gh(self, body):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = os.path.join(d.name, "gh")
        with open(path, "w") as fh:
            fh.write("#!/usr/bin/env python3\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_exact_arguments(self):
        expect = ["attestation", "verify", "REC", "--repo", "CentralInfraCore/cic-primitive-forms", "--signer-workflow",
                  "CentralInfraCore/cic-primitive-forms/.github/workflows/evidence.yml", "--source-ref", "refs/heads/main",
                  "--source-digest", C1]
        gh = self.fake_gh("import sys\nwant = %r\nsys.exit(0 if sys.argv[1:] == want else 3)\n" % expect)
        a = verify.GhAttestation(gh)
        self.assertTrue(a.verify("REC", "d", verify.Trust(), C1))
        for name, trust, commit in (("another repository", verify.Trust(repository="Evil/x"), C1),
                                    ("another workflow", verify.Trust(signer_workflow=".github/workflows/other.yml"), C1),
                                    ("another ref", verify.Trust(source_ref="refs/heads/feature"), C1),
                                    ("another commit", verify.Trust(), C2)):
            with self.subTest(name):
                self.assertFalse(a.verify("REC", "d", trust, commit))

    def test_every_other_outcome_is_a_refusal(self):
        self.assertFalse(verify.GhAttestation(self.fake_gh("import sys\nsys.exit(1)\n")).verify("R", "d", verify.Trust(), C1))
        self.assertFalse(verify.GhAttestation("/nonexistent/gh").verify("R", "d", verify.Trust(), C1))
        slow = verify.GhAttestation(self.fake_gh("import time\ntime.sleep(30)\n"), timeout=1)
        self.assertFalse(slow.verify("R", "d", verify.Trust(), C1))
        self.assertTrue(verify.GhAttestation(self.fake_gh("")).verify("R", "d", verify.Trust(), C1))  # only a clean exit passes

    def test_the_flags_it_uses_must_exist(self):
        listing = "Usage: gh attestation verify\\n  --repo  --signer-workflow  --source-ref  --source-digest\\n"
        ok = self.fake_gh("import sys\nsys.stdout.write('%s')\n" % listing)
        self.assertEqual(verify.GhAttestation(ok).missing_flags(), [])
        old = self.fake_gh("import sys\nsys.stdout.write('Usage: gh attestation verify\\\\n  --repo  --signer-workflow\\\\n')\n")
        self.assertEqual(verify.GhAttestation(old).missing_flags(), ["--source-ref", "--source-digest"])
        self.assertEqual(verify.GhAttestation(self.fake_gh("import sys\nsys.exit(1)\n")).missing_flags(), list(verify.GH_FLAGS))
        self.assertEqual(verify.GhAttestation("/nonexistent/gh").missing_flags(), list(verify.GH_FLAGS))


class ChainTests(unittest.TestCase):
    """Section 5.7: a failed, missed or unverifiable run breaks the chain."""

    def setUp(self):
        self.fx = Fixture(self)

    def v(self, hours_ago, outcome="pass", slot=None, commit=C1):
        measured = NOW - datetime.timedelta(hours=hours_ago)
        rec = self.fx.record(measured=measured, outcome=outcome, commit=commit, slot=slot if slot is not None else "")
        if slot is None:
            rec["slot"] = None
        raw = canon.canonicalize(rec)
        return verify.Verified(rec, hashlib.sha256(raw).hexdigest())

    def choose(self, records, now=NOW, ancestor=True, **kw):
        return verify.choose_baseline(records, now, C2, lambda a, d: ancestor, **kw)

    def test_the_due_slot(self):
        self.assertEqual(verify.latest_due_slot(datetime.datetime(2026, 10, 20, 5, 59, 59, tzinfo=UTC)), "2026-10-19")
        self.assertEqual(verify.latest_due_slot(datetime.datetime(2026, 10, 20, 6, 0, 0, tzinfo=UTC)), "2026-10-20")
        self.assertEqual(verify.latest_due_slot(datetime.datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC)), "2026-10-20")

    def test_a_good_chain(self):
        got = self.choose([self.v(2, slot="2026-10-20")])
        self.assertEqual((got.reason, got.baseline.rec["slot"]), ("OK", "2026-10-20"))
        before_due = datetime.datetime(2026, 10, 20, 5, 0, tzinfo=UTC)  # the slot of the 20th is not due yet
        got = self.choose([self.v(20, slot="2026-10-19")], now=before_due)
        self.assertEqual(got.reason, "OK")

    def test_nothing_to_choose_from(self):
        self.assertEqual(self.choose([]).reason, "NO_RECORDS")
        self.assertEqual(self.choose([self.fx_merge_candidate()]).reason, "NO_RECORDS")

    def fx_merge_candidate(self):
        rec = self.fx.record(kind="merge_candidate", measured=NOW - datetime.timedelta(hours=1))
        return verify.Verified(rec, hashlib.sha256(canon.canonicalize(rec)).hexdigest())

    def test_a_missed_run_leaves_no_baseline(self):
        # the run of the 20th did not happen: the success of the 19th is not usable
        got = self.choose([self.v(20, slot="2026-10-19")])
        self.assertEqual(got.reason, "SLOT_MISSED")
        # a push to main leaves no slot: it does not stand in for the schedule
        self.assertEqual(self.choose([self.v(2, slot=None)]).reason, "SLOT_MISSED")

    def test_a_failure_breaks_the_chain(self):
        # Sunday passes, Monday fails, nothing after: Sunday is not a baseline on Tuesday
        sunday = self.v(40, slot="2026-10-18")
        monday = self.v(2, outcome="fail", slot="2026-10-20")
        got = self.choose([sunday, monday])
        self.assertEqual(got.reason, "NEWEST_NOT_PASS")
        # a pass after the failure restores it, and the baseline is that pass, not the one before the failure
        tuesday = self.v(1, slot=None)
        got = self.choose([sunday, monday, tuesday])
        self.assertEqual((got.reason, got.baseline.digest), ("OK", tuesday.digest))

    def test_a_pass_before_a_failure_is_not_a_fallback(self):
        # A (an ancestor of the target) passes, F fails, N passes but is not an ancestor of the target: the answer is not A
        a = self.v(40, slot="2026-10-18", commit=C1)
        f = self.v(30, outcome="fail", slot="2026-10-19")
        n = self.v(2, slot="2026-10-20", commit=C3)
        got = verify.choose_baseline([a, f, n], NOW, C2, lambda anc, desc: anc == C1)
        self.assertEqual(got.reason, "NO_ANCESTOR")

    def test_a_failed_push_breaks_it_too(self):
        good = self.v(5, slot="2026-10-20")
        bad_push = self.v(1, outcome="fail", slot=None)
        self.assertEqual(self.choose([good, bad_push]).reason, "NEWEST_NOT_PASS")

    def test_age(self):
        got = self.choose([self.v(2, slot="2026-10-20")], max_age=datetime.timedelta(hours=1))
        self.assertEqual(got.reason, "TOO_OLD")

    def test_ancestry(self):
        self.assertEqual(self.choose([self.v(2, slot="2026-10-20")], ancestor=False).reason, "NO_ANCESTOR")

    def test_records_that_disagree_are_uncertain(self):
        a = self.v(2, slot="2026-10-20")
        b = self.v(2, outcome="fail", slot=None)
        self.assertEqual(self.choose([a, b]).reason, "UNCERTAIN")

    def test_load_and_choose(self):
        good = self.fx.record(measured=NOW - datetime.timedelta(hours=2), slot="2026-10-20")
        raw, name = raw_of(good)
        digest = name[:-5]
        store = {digest: raw}
        fetch = lambda h, kind: store.get(h)
        ctx = context(self.fx)
        self.assertEqual(verify.load_and_choose([(digest, "full")], fetch, ctx).reason, "OK")
        # listed and not there
        self.assertEqual(verify.load_and_choose([(digest, "full"), ("9" * 64, "full")], fetch, ctx).reason, "UNCERTAIN")
        # there, and tampered with
        store[digest] = raw.replace(b'"run_id":1', b'"run_id":2')
        self.assertEqual(verify.load_and_choose([(digest, "full")], fetch, ctx).reason, "UNCERTAIN")
        store[digest] = raw
        # not attested
        self.assertEqual(verify.load_and_choose([(digest, "full")], fetch, context(self.fx, Attested(ok=False))).reason, "UNCERTAIN")
        # listed twice
        self.assertEqual(verify.load_and_choose([(digest, "full"), (digest, "full")], fetch, ctx).reason, "UNCERTAIN")
        # a failure that is listed and genuine breaks the chain
        bad = self.fx.record(measured=NOW - datetime.timedelta(hours=1), outcome="fail", slot=None)
        braw, bname = raw_of(bad)
        store[bname[:-5]] = braw
        got = verify.load_and_choose([(digest, "full"), (bname[:-5], "audit")], fetch, ctx)
        self.assertEqual(got.reason, "NEWEST_NOT_PASS")
        # a good chain whose baseline does not fit the target (the harness changed since)
        self.fx.write("tools/quality_gate.py", "changed")
        store.pop(bname[:-5])
        got = verify.load_and_choose([(digest, "full")], fetch, context(self.fx))
        self.assertEqual((got.reason, got.detail), ("BASELINE_REFUSED", "KEY_MISMATCH"))


if __name__ == "__main__":
    unittest.main()
