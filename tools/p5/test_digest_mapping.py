"""Tests of the release record to receipt digest mapping: the shared cases, the independent recomputation of their values, and the proof that the cases can fail.

The mapping is a yardstick for the Rust and the Go implementations (docs/P5-DIGEST-MAPPING-CONTRACT.md). A yardstick that cannot fail proves nothing, so the last class plants
a defect in a copy of the reference, one at a time, and the cases have to turn red; the two mutants that cannot change a result are listed and have to survive.
"""
import base64
import importlib.util
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "p5"
CASES = ROOT / "testdata" / "p5" / "digest-mapping.json"
sys.path.insert(0, str(TOOLS))
import digest_mapping_cases as gen  # noqa: E402


def load_reference():
    """The reference under test; DIGEST_MAPPING_REFERENCE points at a mutated copy during the planted-defect runs."""
    path = os.environ.get("DIGEST_MAPPING_REFERENCE") or str(TOOLS / "digest_mapping.py")
    spec = importlib.util.spec_from_file_location("digest_mapping_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dm = load_reference()
DOC = json.loads(CASES.read_text(encoding="utf-8"))


def answer(case):
    try:
        if case["kind"] == "convert":
            return ("ok", dm.to_receipt_digest(case["input"]), "")
        return ("ok", dm.map_release(case["record"], case["pin"]), "")
    except dm.Refusal as e:
        return ("refuse", e.reason, e.field)
    except Exception as e:  # noqa: BLE001  # a crash is a wrong answer for THAT vector, not the end of the run
        return ("crash", type(e).__name__, "")


class SharedVectors(unittest.TestCase):
    maxDiff = None      # the planted-defect runs read the ids of the failing vectors from this output

    def test_every_vector_gets_the_expected_answer(self):
        wrong = []
        for case in DOC["cases"]:
            got = answer(case)
            if case["refuse"] is None:
                good = got[0] == "ok" and got[1] == case["expect"]
            else:
                good = got[0] == "refuse" and got[1] == case["refuse"] and (case.get("field") is None or got[2] == case["field"])
            if not good:
                wrong.append((case["id"], got, case["refuse"], case.get("field")))
        self.assertEqual(wrong, [])

    def test_vector_ids_are_unique(self):
        ids = [c["id"] for c in DOC["cases"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_reason_has_a_targeted_vector(self):
        used = {c["refuse"] for c in DOC["cases"] if c["refuse"]}
        # base64_length is the defensive check behind the shape check: no input reaches it (the shape of 43 characters and a padding already means 32 bytes)
        self.assertEqual(set(DOC["reasons"]) - used, {"base64_length"})
        self.assertEqual(set(DOC["reasons"]), set(dm.REASONS))

    def test_the_vector_file_is_the_one_the_generator_writes(self):
        self.assertEqual(CASES.read_text(encoding="utf-8"), gen.render())

    def test_a_refusal_never_returns_a_value_and_a_value_is_never_a_refusal(self):
        for case in DOC["cases"]:
            self.assertEqual(case["refuse"] is None, case["expect"] is not None, case["id"])

    def test_a_forged_or_unsigned_record_still_maps_because_the_mapping_does_not_look_at_signatures(self):
        forged = [c for c in DOC["cases"] if c["id"].startswith("R4-")]
        self.assertEqual(len(forged), 1)
        self.assertEqual(answer(forged[0])[0], "ok")


class IndependentValues(unittest.TestCase):
    """The expected values are recomputed by tools that have never seen this code: openssl and od, hashlib, the pin of this repository."""

    @staticmethod
    def openssl_hex(b64_text):
        out = subprocess.run(["bash", "-c", "printf %s '%s' | openssl base64 -d -A | od -An -tx1 -v | tr -d ' \n'" % (b64_text, b64_text)], capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_the_tools_are_there(self):
        # a skipped check proves nothing: where the tools are missing this is a failure
        self.assertTrue(shutil.which("openssl") and shutil.which("od") and shutil.which("tr") and shutil.which("bash"))

    def test_the_conversion_of_the_real_values_is_what_openssl_says(self):
        for b64_text, hex_text in ((gen.BUILD_B64, gen.BUILD_HEX), (gen.GRAMMAR_B64, gen.GRAMMAR_HEX), (gen.SCHEMA_B64, gen.SCHEMA_HEX)):
            self.assertEqual(self.openssl_hex(b64_text), hex_text)
            self.assertEqual(dm.to_receipt_digest(b64_text), "sha256:" + hex_text)

    def test_every_accepted_conversion_is_what_openssl_says(self):
        accepted = [c for c in DOC["cases"] if c["kind"] == "convert" and c["refuse"] is None]
        self.assertGreaterEqual(len(accepted), 6)
        for case in accepted:
            self.assertEqual("sha256:" + self.openssl_hex(case["input"]), case["expect"], case["id"])

    def test_the_two_grammar_digests_are_those_of_the_pin_of_this_repository(self):
        pin = yaml.safe_load((ROOT / "corpus" / "pins.yaml").read_text(encoding="utf-8"))["grammar"]["cic-primitives-73ae469-6a188078"]["files"]
        by_path = {f["path"]: f["sha256"] for f in pin}
        self.assertEqual(by_path[gen.CHECK_GRAMMAR], gen.GRAMMAR_HEX)
        self.assertEqual(by_path[gen.GRAMMAR_SCHEMA], gen.SCHEMA_HEX)
        self.assertEqual(by_path["LICENSE.md"], gen.LICENSE_HEX)

    def test_a_synthetic_value_is_the_hex_of_the_bytes_it_was_made_from(self):
        rng = random.Random(20261102)
        for _ in range(200):
            raw = bytes(rng.randrange(256) for _ in range(32))
            self.assertEqual(dm.to_receipt_digest(base64.b64encode(raw).decode("ascii")), "sha256:" + raw.hex())


class Property(unittest.TestCase):
    """Around the canonical form: change one character of a valid value, and the answer has to be the standard library's own verdict on 32 bytes in one spelling."""

    ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

    @staticmethod
    def oracle(text):
        try:
            raw = base64.b64decode(text, validate=True)
        except Exception:
            return None
        return raw if len(raw) == 32 and base64.b64encode(raw).decode("ascii") == text and text.endswith("=") and len(text) == 44 else None

    def test_one_changed_character_is_accepted_exactly_when_the_result_is_canonical(self):
        rng = random.Random(7)
        accepted = refused = 0
        for _ in range(60):
            text = base64.b64encode(bytes(rng.randrange(256) for _ in range(32))).decode("ascii")
            for _ in range(10):
                i = rng.randrange(len(text))
                mutated = text[:i] + rng.choice(self.ALPHABET + "=- _\n") + text[i + 1:]
                want = self.oracle(mutated)
                try:
                    got = dm.to_receipt_digest(mutated)
                except dm.Refusal:
                    got = None
                self.assertEqual(got, None if want is None else "sha256:" + want.hex(), repr(mutated))
                accepted, refused = accepted + (got is not None), refused + (got is None)
        self.assertGreater(accepted, 0)
        self.assertGreater(refused, 0)


class ContractAgrees(unittest.TestCase):
    """The contract states two numbers and one set of reasons; they may not drift from the file and the reference."""

    DOC_TEXT = (ROOT / "docs" / "P5-DIGEST-MAPPING-CONTRACT.md").read_text(encoding="utf-8")

    def test_the_contract_counts_the_cases_and_the_planted_defects(self):
        self.assertIn("holds %d cases" % len(DOC["cases"]), self.DOC_TEXT)
        self.assertIn("plants %d defects" % len(MUTANTS), self.DOC_TEXT)

    def test_the_contract_names_every_reason(self):
        for reason in dm.REASONS:
            self.assertIn("`%s`" % reason, self.DOC_TEXT, reason)


# (name, old text, new text, vectors that must fail). Every `old` occurs once in digest_mapping.py.
MUTANTS = [
    ("the shape is a prefix match", ("BASE64_32_BYTES.fullmatch(value)", "BASE64_32_BYTES.match(value)"), ["N2-trailing-newline"]),
    ("the canonical spelling is not required", ("if base64.b64encode(raw).decode(\"ascii\") != value:", "if False:"), ["N1-non-canonical-spelling-of-the-same-32-bytes"]),
    ("the receipt digest accepts uppercase hex", ("RECEIPT_DIGEST = re.compile(r\"sha256:[0-9a-f]{64}\")", "RECEIPT_DIGEST = re.compile(r\"sha256:[0-9a-fA-F]{64}\")"), ["D1-digest-uppercase"]),
    ("the receipt digest is a prefix match", ("RECEIPT_DIGEST.fullmatch(digest)", "RECEIPT_DIGEST.match(digest)"), ["D6-digest-trailing-newline"]),
    ("provenance is not cross-checked", ("if cross != primary:", "if False:"), ["V6-provenance-differs-from-the-artifact-by-one-bit"]),
    ("the pin is not checked", ("if \"sha256:\" + pinned != primary:", "if False:"), ["K1-artifact-differs-from-the-pin-by-one-bit"]),
    ("envelope 2 is accepted", ("value != 3:", "value not in (2, 3):"), ["E1-release-envelope-2"]),
    ("the envelope is not required to be an integer", ("type(value) is not int or value != 3", "value != 3"), ["E4-release-envelope-as-a-float"]),
    ("a path listed twice is accepted", ("if len(entries) > 1:", "if False:"), ["A6-check-grammar-listed-twice-both-right"]),
    ("a missing artifact is not refused", ("if not entries:", "if False:"), ["A4-check-grammar-absent"]),
    ("the grammar fields are judged in reverse order", ("for receipt_field, path, provenance_member in GRAMMAR_FIELDS:", "for receipt_field, path, provenance_member in reversed(GRAMMAR_FIELDS):"),
     ["O2-grammar-sha256-is-judged-before-the-schema"]),
    ("the build hash is not converted", ("out = {\"primitive_build_hash\": to_receipt_digest(_text(record, \"release.build_hash\"), \"release.build_hash\")}",
                                        "out = {\"primitive_build_hash\": \"sha256:\" + _text(record, \"release.build_hash\")}"), ["R1-the-record-of-v0.2.1"]),
    ("the type of a digest is not checked", ("    if not isinstance(digest, str):\n        raise Refusal(\"field_type\", \"artifacts.files[%s].digest\" % path)\n", ""), ["D9-digest-an-integer"]),
]
# cannot change a result, and are listed so that this is a statement and not a surprise
EQUIVALENT = [
    ("the length check behind the shape check is removed", ("    if len(raw) != 32:\n        raise Refusal(\"base64_length\", field)\n", ""),
     "the shape (43 characters and one padding) already means 32 bytes; the check is defensive for a decoder that returns another length"),
    ("the provenance value is returned instead of the artifact digest", ("out[receipt_field] = primary", "out[receipt_field] = cross"),
     "the two are equal at that point, the cross-check has just refused otherwise"),
]


def run_cases_against(source_text, tmp):
    path = pathlib.Path(tmp) / "digest_mapping.py"
    path.write_text(source_text, encoding="utf-8")
    compile(source_text, str(path), "exec")        # a mutant that does not compile would die for the wrong reason
    env = {"DIGEST_MAPPING_REFERENCE": str(path), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([sys.executable, "-m", "unittest", "-k", "every_vector", str(TOOLS / "test_digest_mapping.py")], cwd=ROOT, env=env, capture_output=True, text=True, check=False)


class PlantedDefects(unittest.TestCase):
    SOURCE = (TOOLS / "digest_mapping.py").read_text(encoding="utf-8")

    def test_planted_the_unmutated_copy_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_cases_against(self.SOURCE, tmp).returncode, 0)

    def test_planted_every_defect_turns_the_vectors_red(self):
        import tempfile
        survivors = []
        for name, (old, new), targets in MUTANTS:
            self.assertEqual(self.SOURCE.count(old), 1, "%s: the text to mutate occurs %d times" % (name, self.SOURCE.count(old)))
            with tempfile.TemporaryDirectory() as tmp:
                result = run_cases_against(self.SOURCE.replace(old, new), tmp)
            if result.returncode == 0:
                survivors.append(name)
                continue
            for target in targets:
                if target not in result.stdout + result.stderr:
                    survivors.append("%s (the vector %s is not among the failures)" % (name, target))
        self.assertEqual(survivors, [])

    def test_planted_the_equivalent_mutants_survive_and_say_why(self):
        import tempfile
        for name, (old, new), why in EQUIVALENT:
            self.assertEqual(self.SOURCE.count(old), 1, name)
            with tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(run_cases_against(self.SOURCE.replace(old, new), tmp).returncode, 0, "%s: %s" % (name, why))


if __name__ == "__main__":
    unittest.main()
