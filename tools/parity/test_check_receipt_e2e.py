# SPDX-License-Identifier: Apache-2.0
"""Self-test of tools/parity/check_receipt_e2e.py: the proof of the end-to-end chain has to be able to fail.

The clean dumps are built here from the Python reference (the way a correct language would write them); each test breaks ONE thing and reads the MESSAGE, not the
number of problems: a check that is red for another reason is not a check of this one.
"""
import copy
import hashlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_receipt_e2e as ck  # noqa: E402

FIXTURE = json.loads(ck.FIXTURE.read_text())


def run_of(case, which, lang):
    want = case[which]
    document = want["document"].replace(FIXTURE["engine_identity_token"], ck.IDENTITY[lang])
    receipt = {
        "document": document,
        "digest": "sha256:" + hashlib.sha256(document.encode()).hexdigest(),
        "binding": copy.deepcopy(want["binding"]),
        "authored_input": want["authored_input"],
        "projection": want["projection"],
        "output_digest": want["output_digest"],
    }
    name, version = ck.rc.identity(case["schema"])
    run = {
        "capabilities": copy.deepcopy(want["capabilities"]),
        "schema_name": name,
        "schema_version": version,
        "schema_digest": "sha256:" + case["sha256"],
        "authored_input": want["authored_input"],
        "standalone": {"projection": want["projection"], "expected_keys": list(want["expected_keys"]), "output_digest": want["output_digest"]},
        "receipt": receipt,
    }
    run["chain"] = ck.chain_digest(run)
    return run


def clean(lang):
    return [{"id": c["id"], "base": run_of(c, "base", lang), "alt": run_of(c, "alt", lang)} for c in FIXTURE["cases"]]


def dumps():
    return {"rust": clean("rust"), "go": clean("go")}


def rechain(run):
    run["chain"] = ck.chain_digest(run)


def messages(d, fixture=FIXTURE):
    return ck.check(fixture, d)


def has(problems, *words):
    return any(all(w in p for w in words) for p in problems)


class CheckReceiptE2E(unittest.TestCase):
    def test_clean_dumps_are_green(self):
        self.assertEqual(messages(dumps()), [])

    def test_the_real_reference_has_the_cases_the_proof_needs(self):
        counts = {c["id"]: len(json.loads(c["base"]["document"])["materialization"]["applied_defaults"]) for c in FIXTURE["cases"]}
        self.assertEqual(counts["zero-defaults"], 0)
        self.assertEqual(counts["one-default"], 1)
        self.assertGreater(counts["many-defaults"], 1)
        self.assertEqual(counts["twin-defaults"], 2)

    def test_a_case_missing_from_one_language_is_red(self):
        d = dumps()
        d["go"] = [r for r in d["go"] if r["id"] != "one-default"]
        self.assertTrue(has(messages(d), "go", "one-default", "is missing"))

    def test_a_case_missing_from_both_languages_is_red(self):
        d = dumps()
        for lang in d:
            d[lang] = [r for r in d[lang] if r["id"] != "many-defaults"]
        p = messages(d)
        self.assertTrue(has(p, "rust", "many-defaults", "is missing"))
        self.assertTrue(has(p, "go", "many-defaults", "is missing"))

    def test_a_repeated_case_does_not_stand_for_another(self):
        d = dumps()
        d["rust"][1] = copy.deepcopy(d["rust"][0])
        p = messages(d)
        self.assertTrue(has(p, "rust", "zero-defaults", "is repeated"))
        self.assertTrue(has(p, "rust", "one-default", "is missing"))

    def test_a_case_the_reference_does_not_have_is_red(self):
        d = dumps()
        extra = copy.deepcopy(d["go"][0])
        extra["id"] = "invented"
        d["go"].append(extra)
        self.assertTrue(has(messages(d), "go", "invented", "not one the reference has"))

    def test_a_skipped_step_is_red(self):
        for key in ("standalone", "receipt", "chain", "authored_input", "schema_digest"):
            d = dumps()
            del d["rust"][0]["base"][key]
            self.assertTrue(has(messages(d), "rust zero-defaults base", "a step was skipped"), key)
        d = dumps()
        del d["go"][2]["alt"]["receipt"]["projection"]
        self.assertTrue(has(messages(d), "go many-defaults alt", "lacks an artefact"))
        d = dumps()
        del d["go"][2]["alt"]
        self.assertTrue(has(messages(d), "go many-defaults alt", "a step was skipped"))

    def test_an_invented_step_is_red(self):
        d = dumps()
        d["go"][0]["base"]["resolve"] = {"snapshot": "x"}
        self.assertTrue(has(messages(d), "go zero-defaults base", "invented"))

    def test_a_wrong_schema_identity_is_red(self):
        d = dumps()
        d["rust"][1]["base"]["schema_digest"] = "sha256:" + "0" * 64
        rechain(d["rust"][1]["base"])
        self.assertTrue(has(messages(d), "rust one-default base", "schema identity"))

    def test_another_authored_input_is_red(self):
        d = dumps()
        run = d["go"][2]["base"]
        run["authored_input"] = run["authored_input"].replace('"t"', '"u"')
        rechain(run)
        self.assertTrue(has(messages(d), "go many-defaults base", "authored input"))

    def test_a_changed_standalone_object_is_red(self):
        d = dumps()
        run = d["rust"][2]["base"]
        run["standalone"]["projection"] = run["standalone"]["projection"].replace("implemented", "deprecated", 1)
        rechain(run)
        self.assertTrue(has(messages(d), "rust many-defaults base", "standalone object"))

    def test_two_materializations_that_are_two_objects_are_red(self):
        d = dumps()
        run = d["go"][3]["base"]
        run["receipt"]["projection"] = d["go"][3]["alt"]["receipt"]["projection"]
        self.assertTrue(has(messages(d), "go twin-defaults base", "two executions, two objects"))
        d = dumps()
        d["go"][3]["base"]["receipt"]["output_digest"] = d["go"][3]["alt"]["receipt"]["output_digest"]
        self.assertTrue(has(messages(d), "go twin-defaults base", "two executions, two objects"))

    def test_a_receipt_that_differs_from_the_reference_is_red_even_with_its_own_digest(self):
        d = dumps()
        run = d["rust"][2]["base"]
        doc = run["receipt"]["document"].replace(run["standalone"]["output_digest"], d["rust"][2]["alt"]["standalone"]["output_digest"])
        run["receipt"]["document"] = doc
        run["receipt"]["digest"] = "sha256:" + hashlib.sha256(doc.encode()).hexdigest()
        rechain(run)
        self.assertTrue(has(messages(d), "rust many-defaults base", "the receipt differs from the reference"))

    def test_a_receipt_digest_that_is_not_the_digest_of_the_receipt_is_red(self):
        d = dumps()
        d["go"][0]["base"]["receipt"]["digest"] = "sha256:" + "1" * 64
        self.assertTrue(has(messages(d), "go zero-defaults base", "not the digest of the receipt"))

    def test_an_artefact_of_another_run_breaks_the_chain_digest(self):
        # the receipt of the ALT run put into the BASE run: the chain digest was made for the artefacts of the base run
        d = dumps()
        d["rust"][2]["base"]["receipt"] = copy.deepcopy(d["rust"][2]["alt"]["receipt"])
        p = messages(d)
        self.assertTrue(has(p, "rust many-defaults base", "chain digest"))
        self.assertTrue(has(p, "rust many-defaults base", "two executions, two objects"))
        # the same with only the chain digest of another run
        d = dumps()
        d["go"][1]["base"]["chain"] = d["go"][1]["alt"]["chain"]
        self.assertTrue(has(messages(d), "go one-default base", "chain digest"))
        # the capability map of another run
        d = dumps()
        d["go"][1]["base"]["capabilities"] = copy.deepcopy(d["go"][1]["alt"]["capabilities"])
        self.assertTrue(has(messages(d), "go one-default base", "capability map"))

    def test_the_chain_digest_covers_the_path_binding(self):
        # the binding is a separate input of the engine's verifier: changing it, and nothing else, has to change the chain digest and turn the chain check red
        d = dumps()
        run = d["rust"][3]["base"]
        before = run["chain"]
        b = run["receipt"]["binding"]
        b["$.active"], b["$.enabled"] = b["$.enabled"], b["$.active"]
        self.assertNotEqual(ck.chain_digest(run), before)
        self.assertEqual(run["chain"], before)
        p = messages(d)
        self.assertTrue(has(p, "rust twin-defaults base", "chain digest"))
        # the chain check alone: with the reference's binding check out of the way, the digest still catches the swap
        original = ck.check_run

        def without_binding_reference(lang, cid, which, run, case, want, fixture):
            want = dict(want, binding=run["receipt"]["binding"] if isinstance(run, dict) and "receipt" in run else want["binding"])
            return original(lang, cid, which, run, case, want, fixture)

        ck.check_run = without_binding_reference
        try:
            p = messages(d)
        finally:
            ck.check_run = original
        self.assertTrue(has(p, "rust twin-defaults base", "chain digest"))
        self.assertFalse(has(p, "rust twin-defaults base", "path binding"))

    def test_a_receipt_of_another_case_is_red(self):
        d = dumps()
        d["rust"][2]["base"]["receipt"] = copy.deepcopy(d["rust"][0]["base"]["receipt"])
        p = messages(d)
        self.assertTrue(has(p, "rust many-defaults base", "receipt differs from the reference"))
        self.assertTrue(has(p, "rust many-defaults base", "chain digest"))

    def test_the_swapped_binding_of_two_defaults_of_equal_value_is_red(self):
        d = dumps()
        run = d["rust"][3]["base"]
        binding = run["receipt"]["binding"]
        self.assertEqual(set(binding), {"$.active", "$.enabled"})
        binding["$.active"], binding["$.enabled"] = binding["$.enabled"], binding["$.active"]
        self.assertTrue(has(messages(d), "rust twin-defaults base", "path binding"))

    def test_a_wrong_or_missing_binding_is_red(self):
        d = dumps()
        d["go"][2]["base"]["receipt"]["binding"].pop("$.mtu")
        self.assertTrue(has(messages(d), "go many-defaults base", "path binding"))
        d = dumps()
        d["go"][2]["base"]["receipt"]["binding"]["$.mtu"] = "label"
        self.assertTrue(has(messages(d), "go many-defaults base", "path binding"))

    def test_the_twin_case_without_its_point_is_red(self):
        f = copy.deepcopy(FIXTURE)
        twin = [c for c in f["cases"] if c["id"] == "twin-defaults"][0]
        doc = json.loads(twin["base"]["document"])
        doc["materialization"]["applied_defaults"][1]["value_digest"] = "sha256:" + "2" * 64
        twin["base"]["document"] = json.dumps(doc)
        self.assertTrue(has(messages(dumps(), f), "twin-defaults has no two defaulted fields of one value"))
        f = copy.deepcopy(FIXTURE)
        f["cases"] = [c for c in f["cases"] if c["id"] != "twin-defaults"]
        self.assertTrue(has(messages(dumps(), f), "no case twin-defaults"))

    def test_a_capability_change_that_moves_nothing_is_red(self):
        d = dumps()
        d["rust"][1]["alt"] = copy.deepcopy(d["rust"][1]["base"])
        self.assertTrue(has(messages(d), "rust one-default", "did not move the object"))

    def test_a_capability_change_that_moves_the_input_is_red(self):
        d = dumps()
        run = d["go"][0]["alt"]
        run["authored_input"] = run["authored_input"].replace('"t"', '"u"')
        rechain(run)
        self.assertTrue(has(messages(d), "go zero-defaults", "moved the authored input"))

    def test_a_capability_change_that_moves_more_than_the_output_digest_is_red(self):
        d = dumps()
        run = d["go"][2]["alt"]
        doc = json.loads(run["receipt"]["document"])
        doc["materialization"]["engine_version"] = "9.9.9"
        run["receipt"]["document"] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        self.assertTrue(has(messages(d), "go many-defaults", "more of the receipt than its output digest"))

    def test_a_language_that_differs_in_more_than_the_identity_is_red(self):
        d = dumps()
        d["go"][0]["base"]["schema_name"] = "other:Name"
        rechain(d["go"][0]["base"])
        p = messages(d)
        self.assertTrue(has(p, "go zero-defaults base", "schema identity"))
        self.assertTrue(has(p, "zero-defaults base", "differ in more than the engine identity"))


if __name__ == "__main__":
    unittest.main()
