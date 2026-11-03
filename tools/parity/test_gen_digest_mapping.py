# SPDX-License-Identifier: Apache-2.0
"""Tests of the generated cases of the digest mapping differential (tools/parity/gen_digest_mapping.py; docs/P5-DIGEST-MAPPING-DIFFERENTIAL-PLAN.md).

The generator is the INDEPENDENT half of the proof: its expected answers are fixed by construction, not by one of the three implementations. These tests show that this half is
what it says it is: deterministic, thick enough in every class, right about base64 by bit arithmetic (checked here against the standard library, which it does not use), in
agreement with the Python reference on every case, and able to tell when the reference is wrong (defects planted in a copy of it).

    python3 -W error::ResourceWarning -m unittest tools/parity/test_gen_digest_mapping.py

Not wired into a workflow: the pipeline is frozen and where this runs is the owner's decision (plan, section 8).
"""
import base64
import collections
import hashlib
import importlib.util
import json
import os
import pathlib
import random
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "parity"))
import gen_digest_mapping as gen  # noqa: E402

REFERENCE = ROOT / "tools" / "p5" / "digest_mapping.py"


def load_reference(path=None):
    path = path or os.environ.get("DIGEST_MAPPING_REFERENCE") or str(REFERENCE)
    spec = importlib.util.spec_from_file_location("digest_mapping_under_test_%d" % abs(hash(str(path))), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def answer(dm, case):
    try:
        if case["kind"] == "convert":
            return ("ok", dm.to_receipt_digest(case["input"]), None)
        return ("ok", dm.map_release(case["record"], case["pin"]), None)
    except dm.Refusal as e:
        return ("refuse", e.reason, e.field)
    except Exception as e:  # noqa: BLE001  # a crash is a wrong answer for THAT case, not the end of the run
        return ("crash", type(e).__name__, None)


def agrees(case, got):
    if case["refuse"] is None:
        return got[0] == "ok" and got[1] == case["expect"]
    return got[0] == "refuse" and got[1] == case["refuse"] and (case.get("field") is None or got[2] == case["field"])


def disagreements(dm, cases):
    return [c for c in cases if not agrees(c, answer(dm, c))]


CASES = gen.generate()


class Generator(unittest.TestCase):
    def test_the_same_seed_gives_the_same_bytes_and_another_seed_another_file(self):
        self.assertEqual(gen.render(gen.generate()), gen.render(CASES))
        self.assertNotEqual(gen.render(gen.generate(seed=7)), gen.render(CASES))

    def test_the_ids_are_unique(self):
        ids = [c["id"] for c in CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_thin_run_is_red(self):
        floors = {"b64": 1000, "defect": 150, "pair": 300, "valid": 30, "base": 1}
        have = collections.Counter(c["class"].split(":")[0] for c in CASES)
        for k, floor in floors.items():
            self.assertGreaterEqual(have[k], floor, "the class %s has %d cases, the floor is %d" % (k, have[k], floor))

    def test_every_refusal_of_the_contract_is_drawn_except_the_one_no_input_reaches(self):
        used = {c["refuse"] for c in CASES if c["refuse"]}
        contract = {"envelope_not_3", "field_missing", "field_type", "base64_shape", "base64_not_canonical", "digest_form", "artifact_absent", "artifact_duplicate",
                    "provenance_artifacts_disagree", "record_pin_disagree"}
        self.assertEqual(used, contract)           # base64_length is behind the shape check; the implementations test it directly

    def test_the_case_set_is_one_file_with_a_digest(self):
        text = gen.render(CASES)
        self.assertEqual(len(hashlib.sha256(text.encode("utf-8")).hexdigest()), 64)
        for line in text.splitlines():
            json.loads(line)


class TheOracleIsRight(unittest.TestCase):
    """The base64 of the generator is bit arithmetic. It is checked against the standard library, which it never uses."""

    def test_the_bit_arithmetic_is_the_standard_base64_and_the_inverse_is_exact(self):
        rng = random.Random(1)
        for _ in range(2000):
            raw = bytes(rng.randrange(256) for _ in range(32))
            self.assertEqual(gen.canonical(raw), base64.b64encode(raw).decode("ascii"))
            self.assertEqual(gen.raw_of(gen.sextets_of(raw)), raw)

    def test_the_last_group_has_two_zero_bits_and_a_change_of_them_is_another_spelling_of_the_same_bytes(self):
        rng = random.Random(2)
        for _ in range(500):
            raw = bytes(rng.randrange(256) for _ in range(32))
            sx = gen.sextets_of(raw)
            self.assertEqual(sx[42] & 3, 0)
            for v in (1, 2, 3):
                other = sx[:42] + [sx[42] | v]
                self.assertEqual(gen.raw_of(other), raw)                      # the same 32 bytes
                self.assertNotEqual(gen.text_of(other), gen.text_of(sx))      # another text

    def test_a_bit_flip_changes_exactly_one_bit(self):
        raw = bytes(range(32))
        for n in (0, 7, 8, 100, 255):
            flipped = gen.flip_bit(raw, n)
            diff = [i for i in range(256) if (raw[i // 8] ^ flipped[i // 8]) & (0x80 >> (i % 8))]
            self.assertEqual(diff, [n])


class TheReferenceMeetsTheConstruction(unittest.TestCase):
    def test_every_generated_case_gets_the_constructed_answer(self):
        dm = load_reference()
        wrong = disagreements(dm, CASES)
        self.assertEqual([(c["id"], answer(dm, c)) for c in wrong[:5]], [], "%d of %d cases differ from the construction" % (len(wrong), len(CASES)))

    def test_the_83_shared_cases_agree_with_the_same_reference(self):
        dm = load_reference()
        shared = json.loads((ROOT / "testdata" / "p5" / "digest-mapping.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual([c["id"] for c in disagreements(dm, shared)], [])


# (name, old text, new text, the case classes or ids that must turn red). Every `old` occurs once in digest_mapping.py.
MUTANTS = [
    ("the shape is a prefix match", ("BASE64_32_BYTES.fullmatch(value)", "BASE64_32_BYTES.match(value)"), ["b64:final-newline", "b64:final-crlf"]),
    ("the canonical spelling is not required", ("if base64.b64encode(raw).decode(\"ascii\") != value:", "if False:"), ["b64:last-group-trailing-bits"]),
    ("the receipt digest accepts uppercase", ("RECEIPT_DIGEST = re.compile(r\"sha256:[0-9a-f]{64}\")", "RECEIPT_DIGEST = re.compile(r\"sha256:[0-9a-fA-F]{64}\")"), ["rec-grammar_sha256-dig-upper"]),
    ("provenance is not cross-checked", ("if cross != primary:", "if False:"), ["rec-grammar_sha256-disagree-bit"]),
    ("the pin is not checked", ("if \"sha256:\" + pinned != primary:", "if False:"), ["rec-grammar_sha256-pin-bit"]),
    ("the envelope is not required to be an integer", ("type(value) is not int or value != 3", "value != 3"), ["rec-env-release-float-3"]),
    ("a path listed twice is accepted", ("if len(entries) > 1:", "if False:"), ["rec-grammar_sha256-art-twice"]),
    ("the fields are judged in reverse order", ("for receipt_field, path, provenance_member in GRAMMAR_FIELDS:", "for receipt_field, path, provenance_member in reversed(GRAMMAR_FIELDS):"),
     ["pair-"]),
    ("a path that is a prefix of the pinned one is accepted", ("e.get(\"path\") == path", "str(e.get(\"path\")).startswith(path)"), ["rec-grammar_sha256-art-path-trailing-space"]),
    ("null is judged as a type error", ("if value is MISSING or value is None:\n        raise Refusal(\"field_missing\", path)", "if value is MISSING:\n        raise Refusal(\"field_missing\", path)"), ["rec-build-none"]),
]
# cannot change an answer on any generated case, listed with the reason
EQUIVALENT = [
    ("the length guard behind the shape check is removed", ("    if len(raw) != 32:\n        raise Refusal(\"base64_length\", field)\n", ""),
     "the shape (43 characters and one padding) already means 32 bytes: no input reaches the guard"),
]


def mutated(old, new):
    text = REFERENCE.read_text(encoding="utf-8")
    assert text.count(old) == 1, "the text to mutate occurs %d times: %r" % (text.count(old), old)
    compile(text.replace(old, new), "mutant", "exec")          # a mutant that does not compile would die for the wrong reason
    handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    handle.write(text.replace(old, new))
    handle.close()
    return handle.name


class TheConstructionCanTellAWrongReference(unittest.TestCase):
    def test_every_planted_defect_turns_the_named_cases_red(self):
        survivors = []
        for name, (old, new), targets in MUTANTS:
            path = mutated(old, new)
            try:
                red = disagreements(load_reference(path), CASES)
            finally:
                os.unlink(path)
            ids = [c["id"] for c in red] + [c["class"] for c in red]
            if not red:
                survivors.append(name)
                continue
            for target in targets:
                if not any(i.startswith(target) for i in ids):
                    survivors.append("%s (%s did not turn red)" % (name, target))
        self.assertEqual(survivors, [])

    def test_the_equivalent_change_stays_green_and_says_why(self):
        for name, (old, new), why in EQUIVALENT:
            path = mutated(old, new)
            try:
                self.assertEqual([c["id"] for c in disagreements(load_reference(path), CASES)], [], "%s: %s" % (name, why))
            finally:
                os.unlink(path)

    def test_a_reference_that_is_right_everywhere_but_one_place_is_found_in_the_pairs_too(self):
        # the reverse order of the two grammar fields is invisible to a single defect and visible only to a pair of defects of both fields
        name, (old, new), _ = [m for m in MUTANTS if m[0].startswith("the fields are judged")][0]
        path = mutated(old, new)
        try:
            red = disagreements(load_reference(path), CASES)
        finally:
            os.unlink(path)
        self.assertTrue(red and all(c["class"] == "pair" for c in red), "only pairs of defects show a wrong order: %s" % collections.Counter(c["class"] for c in red))


if __name__ == "__main__":
    unittest.main()
