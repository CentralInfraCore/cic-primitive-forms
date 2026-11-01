import copy
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import closeout_check as cc  # noqa: E402

ROOT = cc.ROOT


def evidence_paths(ledger):
    out = {ledger["doc"], ledger["workflow"], "docs/p5-closeout.json", "testdata/p5/engine-pin.txt", "corpus/pins.yaml"}
    for e in ledger["entries"]:
        ev = e.get("evidence", {})
        out.update(ev.get("files", []))
        out.update(c["file"] for c in ev.get("contains", []))
        out.update(c["file"] for c in ev.get("counts", []))
    return out


class Closeout(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = json.loads((ROOT / "docs" / "p5-closeout.json").read_text())
        for rel in evidence_paths(self.ledger):
            src, dst = ROOT / rel, self.tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy(src, dst)

    def entry(self, ledger, eid):
        return next(e for e in ledger["entries"] if e["id"] == eid)

    def problems(self, ledger=None):
        return cc.check(self.tmp, ledger if ledger is not None else self.ledger)

    def has(self, problems, text):
        self.assertTrue(any(text in p for p in problems), f"{text!r} not in {problems}")

    def test_the_committed_ledger_holds_in_the_repository(self):
        self.assertEqual(cc.check(), [])

    def test_the_copy_the_tests_break_is_clean(self):
        self.assertEqual(self.problems(), [])

    # a proved or fixture-only entry is held to its evidence

    def test_a_missing_evidence_file(self):
        (self.tmp / "go" / "canonical.go").unlink()
        self.has(self.problems(), "the evidence file go/canonical.go does not exist")

    def test_a_named_text_that_is_gone(self):
        p = self.tmp / "go" / "receipt.go"
        p.write_text(p.read_text().replace("func ReceiptFlatFixture", "func Renamed"))
        self.has(self.problems(), "does not contain 'func ReceiptFlatFixture'")

    def test_a_count_that_changed(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "path-binding-measured")["evidence"]["counts"][0]["equals"] = 15
        self.has(self.problems(led), "is 16, the ledger says 15")

    def test_a_minimum_that_is_no_longer_held(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "canonical-seeded-defects")["evidence"]["counts"][0]["at_least"] = 10000
        self.has(self.problems(led), "the ledger says at least 10000")

    def test_a_count_of_the_file_itself(self):
        p = self.tmp / "testdata" / "p5" / "end-to-end.json"
        d = json.loads(p.read_text())
        d["cases"].pop()
        p.write_text(json.dumps(d))
        self.has(self.problems(), "is 3, the ledger says 4")

    def test_a_ci_step_that_was_renamed(self):
        p = self.tmp / ".github" / "workflows" / "p1.yml"
        p.write_text(p.read_text().replace("P5-3 end-to-end proof, Rust and Go", "something else"))
        self.has(self.problems(), "the workflow has no step named 'P5-3 end-to-end proof")

    def test_a_doc_section_that_does_not_exist(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "materialize-flat")["doc"] = ["99"]
        self.has(self.problems(led), "the doc has no section 99")

    def test_a_proved_entry_with_no_evidence(self):
        led = copy.deepcopy(self.ledger)
        del self.entry(led, "materialize-flat")["evidence"]
        self.has(self.problems(led), "has no evidence files")

    def test_a_proved_entry_with_no_limit(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "materialize-flat")["limit"] = ""
        self.has(self.problems(led), "states no limit")

    def test_a_proved_entry_that_does_not_say_whether_it_touches_a_receipt(self):
        led = copy.deepcopy(self.ledger)
        del self.entry(led, "materialize-flat")["receipt"]
        self.has(self.problems(led), "'receipt' has to say")

    # P5-3 closes only as a flat fixture receipt

    def test_a_receipt_entry_cannot_be_proved(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "end-to-end-without-reference-resolution")["status"] = "proved"
        self.has(self.problems(led), "cannot be proved: P5-3 closes only as a flat fixture receipt")

    def test_the_flat_receipt_cannot_be_promoted_by_dropping_the_flag(self):
        for eid in ("flat-receipt-fixture", "receipt-fuzz", "end-to-end-without-reference-resolution", "path-binding-measured"):
            led = copy.deepcopy(self.ledger)
            e = self.entry(led, eid)
            e["status"] = "proved"
            e["receipt"] = False
            self.has(self.problems(led), f"{eid}: a proved entry rests on a receipt file")

    # an open item carries a reason and no evidence, and cannot be dropped or moved

    def test_evidence_on_an_open_item(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "reference-resolution")["evidence"] = {"files": ["go/receipt.go"]}
        self.has(self.problems(led), "carries evidence")

    def test_an_open_item_with_no_reason(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "engine-63-open")["reason"] = " "
        self.has(self.problems(led), "gives no reason")

    def test_a_required_open_item_is_dropped(self):
        for rid in self.ledger["required_not_proved_or_deferred"]:
            led = copy.deepcopy(self.ledger)
            led["entries"] = [e for e in led["entries"] if e["id"] != rid]
            self.has(self.problems(led), f"{rid}: a required open item is missing")

    def test_a_required_open_item_is_moved_to_proved(self):
        for rid in self.ledger["required_not_proved_or_deferred"]:
            led = copy.deepcopy(self.ledger)
            self.entry(led, rid)["status"] = "proved"
            self.has(self.problems(led), f"{rid}: a required open item is proved")

    # the ledger itself

    def test_a_repeated_id(self):
        led = copy.deepcopy(self.ledger)
        led["entries"].append(copy.deepcopy(led["entries"][0]))
        self.has(self.problems(led), "the id is repeated")

    def test_a_status_that_does_not_exist(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "materialize-flat")["status"] = "done"
        self.has(self.problems(led), "the status 'done' is not one of")

    def test_a_unit_that_does_not_exist(self):
        led = copy.deepcopy(self.ledger)
        self.entry(led, "materialize-flat")["unit"] = "P5-9"
        self.has(self.problems(led), "the unit 'P5-9'")

    def test_an_engine_pin_that_differs_from_the_file(self):
        led = copy.deepcopy(self.ledger)
        led["engine_pin"] = "0" * 40
        self.has(self.problems(led), "engine pin of the ledger")

    def test_a_grammar_pin_that_is_not_in_the_pins(self):
        led = copy.deepcopy(self.ledger)
        led["grammar_pin_commit"] = "f" * 40
        self.has(self.problems(led), "grammar pin commit of the ledger")

    # the doc and the ledger cannot drift apart

    def test_a_doc_section_that_no_entry_covers(self):
        led = copy.deepcopy(self.ledger)
        for e in led["entries"]:
            e["doc"] = [s for s in e["doc"] if s != "19"] or ["20"]
        self.has(self.problems(led), "section 19 of the doc is covered by no entry")

    def test_an_entry_the_doc_does_not_hold(self):
        p = self.tmp / "docs" / "P5-RESOLVE-MATERIALIZE-RECEIPT.md"
        p.write_text(p.read_text().replace("`engine-63-open`", "engine-63-open"))
        self.has(self.problems(), "engine-63-open: the id is not in the doc")

    def test_a_required_phrase_that_is_gone(self):
        p = self.tmp / "docs" / "P5-RESOLVE-MATERIALIZE-RECEIPT.md"
        p.write_text(p.read_text().replace("flat, fixture-based receipt", "receipt"))
        self.has(self.problems(), "lacks the phrase 'flat, fixture-based receipt'")

    def test_a_doc_without_the_closeout_section(self):
        p = self.tmp / "docs" / "P5-RESOLVE-MATERIALIZE-RECEIPT.md"
        p.write_text(p.read_text().replace("## 22. ", "## Closeout "))
        self.has(self.problems(), "the doc has no section 22")


if __name__ == "__main__":
    unittest.main()
