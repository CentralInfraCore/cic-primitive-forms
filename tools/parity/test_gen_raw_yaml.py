# SPDX-License-Identifier: Apache-2.0
"""Tests of the byte-level cases of the raw YAML differential (tools/parity/gen_raw_yaml.py; docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3a).

The generator is the INDEPENDENT half of the later proof: its labels are fixed by construction. These tests show that this half is what it says it is: deterministic, thick enough in
every class, never mixing a parse expectation with a mapping expectation, consistent with an executable reading of the documented subset (tools/parity/raw_yaml_subset_ref.py) and
with the Unit 2 mapping reference, and able to tell when that reading is wrong (defects planted in a copy of it).

    python3 -W error::ResourceWarning -m unittest tools/parity/test_gen_raw_yaml.py

Not wired into a workflow: the pipeline is frozen and where this runs is the owner's decision.
"""
import collections
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "parity"))
sys.path.insert(0, str(ROOT / "tools" / "p5"))
import digest_mapping as dm  # noqa: E402
import gen_raw_yaml as gen  # noqa: E402

REF = ROOT / "tools" / "parity" / "raw_yaml_subset_ref.py"
CS = gen.generate()
ROWS = CS.rows


def load_ref(path=None):
    path = str(path or REF)
    spec = importlib.util.spec_from_file_location("raw_ref_%d" % abs(hash(path)), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plain(n):
    if n["k"] == "map":
        return {k: plain(v) for k, v in n["v"]}
    if n["k"] == "seq":
        return [plain(v) for v in n["v"]]
    return n["v"]


def judge(ref, row):
    """-> None if the reading agrees with the labels, else why not. Undetermined and pinned cases are not judged."""
    pe = row["parse_expect"]
    try:
        got = ref.read(bytes.fromhex(row["bytes_hex"]))
    except Exception as e:  # noqa: BLE001  # a crash is a wrong answer for THAT case
        return "crash: %s" % type(e).__name__
    if pe["result"] == "accept":
        if got[0] != "accept" or got[1] != pe["tree"]:
            return "expected accept of the tree, got %s" % (got[:1] + (got[1] if got[0] == "refuse" else "(another tree)",),)
        m = row["mapping_expect"]
        if m is not None:
            try:
                out = ("ok", dm.map_release(plain(pe["tree"]), row["pin"]))
            except dm.Refusal as e:
                out = ("refuse", e.reason, e.field)
            if m["refuse"] is None and out != ("ok", m["expect"]):
                return "mapping: expected %s, got %s" % (m["expect"], out)
            if m["refuse"] is not None and not (out[0] == "refuse" and out[1] == m["refuse"] and out[2] == m["field"]):
                return "mapping: expected %s/%s, got %s" % (m["refuse"], m["field"], out)
        return None
    if pe["result"] == "refuse":
        if got[0] != "refuse" or (pe["class"] != "any" and got[1] != pe["class"]):
            return "expected a %s refusal, got %s" % (pe["class"], got[:2])
    return None


def disagreements(ref, rows=ROWS):
    out = []
    for r in rows:
        why = judge(ref, r)
        if why is not None:
            out.append((r["id"], r["class"], why))
    return out


class Generator(unittest.TestCase):
    def test_the_same_seed_gives_the_same_bytes_and_another_seed_another_file(self):
        self.assertEqual(gen.render(gen.generate().rows), gen.render(ROWS))
        self.assertNotEqual(gen.render(gen.generate(7).rows), gen.render(ROWS))

    def test_the_ids_are_unique_and_the_bytes_are_hex(self):
        ids = [r["id"] for r in ROWS]
        self.assertEqual(len(ids), len(set(ids)))
        for r in ROWS:
            bytes.fromhex(r["bytes_hex"])

    def test_a_thin_run_is_red(self):
        floors = json.loads((ROOT / "tools" / "parity" / "floors_raw_yaml.json").read_text(encoding="utf-8"))
        have = collections.Counter(r["class"].split(":")[0] for r in ROWS)
        for k, floor in floors.items():
            self.assertGreaterEqual(have[k], floor, "the class %s has %d cases, the floor is %d" % (k, have[k], floor))
        self.assertEqual(set(have), set(floors), "a class without a floor, or a floor without a class")

    def test_the_labels_are_separate_fields_and_never_mixed(self):
        for r in ROWS:
            self.assertEqual(set(r), {"id", "class", "bytes_hex", "parse_expect", "allowed", "mapping_expect", "pin"})
            pe = r["parse_expect"]["result"]
            self.assertIn(pe, ("accept", "refuse", "undetermined", "pinned_divergence"))
            self.assertIn(r["allowed"], (True, False, "undetermined"))
            if pe == "refuse":
                self.assertIs(r["allowed"], False, r["id"])
                self.assertIsNone(r["mapping_expect"], r["id"])
            if pe == "accept":
                self.assertIs(r["allowed"], True, r["id"])
            if pe == "undetermined":
                self.assertEqual(r["allowed"], "undetermined", r["id"])
            if pe in ("undetermined", "pinned_divergence"):
                self.assertIsNone(r["mapping_expect"], r["id"])
            if r["mapping_expect"] is not None:
                self.assertEqual(pe, "accept", r["id"])

    def test_a_valid_text_whose_value_the_mapping_refuses_is_not_a_parse_refusal(self):
        rows = [r for r in ROWS if r["class"] == "value:envelope" and r["mapping_expect"]["refuse"] == "envelope_not_3"]
        self.assertGreaterEqual(len(rows), 20)
        for r in rows:
            self.assertEqual(r["parse_expect"]["result"], "accept", r["id"])
            self.assertIs(r["allowed"], True)
        # the plain 3 is not refused at all
        three = [r for r in ROWS if r["class"] == "value:envelope" and r["mapping_expect"]["refuse"] is None]
        self.assertTrue(three)

    def test_every_catalogue_entry_has_cases(self):
        have = {r["class"] for r in ROWS}
        for needed in ("syntax-text:tab-after-value", "syntax-text:tab-indent", "syntax-text:nul", "syntax-text:bom-inside", "syntax-text:directive", "syntax-text:document-end",
                       "syntax-text:second-document", "syntax-text:second-marker", "limit-over:size", "limit-over:depth", "limit-over:nodes", "dup-unread", "dup-flow",
                       "value:block-digest", "undetermined:crlf", "undetermined:missing-final-newline", "undetermined:utf16-le-bom", "undetermined:plain-yes", "undetermined:size-at-limit",
                       "undetermined:depth-64"):
            self.assertIn(needed, have)
        for style in gen.STYLES:
            self.assertIn("built:" + style.name, have)

    def test_every_pinned_divergence_input_is_in_the_set(self):
        with open(gen.KNOWN_TXT, encoding="utf-8") as f:
            lines = [l.split()[0] for l in f.read().splitlines() if l.strip() and not l.startswith("#")]
        pinned = {r["bytes_hex"] for r in ROWS if r["class"] == "pinned"}
        self.assertEqual(pinned, set(lines))

    def test_a_pinned_divergence_has_its_own_normative_allowed_label_not_a_blanket_undetermined(self):
        pinned = [r for r in ROWS if r["class"] == "pinned"]
        labels = collections.Counter(str(r["allowed"]) for r in pinned)
        self.assertGreater(labels["True"], 0, "no pinned input is allowed: the point that a permitted text can be read differently by two parsers is lost")
        self.assertGreater(labels["False"], 0)
        for r in pinned:
            self.assertEqual(r["parse_expect"]["result"], "pinned_divergence")
            if r["allowed"] == "undetermined":
                self.assertTrue(r["parse_expect"]["allowed_rule"] in ("yaml-error",) or r["parse_expect"]["allowed_rule"].startswith("undocumented:"), r["id"])
            if r["allowed"] is False:
                self.assertTrue(r["parse_expect"]["allowed_rule"].startswith("documented:"), r["id"])
        # the labels do not depend on whether the Rust and Go parsers agree: the committed file is exactly the reading of the documented table
        sys.path.insert(0, str(ROOT / "tools" / "parity"))
        import classify_pinned_raw_yaml as cls
        committed = json.loads((ROOT / "tools" / "parity" / "pinned_classification_raw_yaml.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, json.loads(json.dumps(cls.classify(), sort_keys=True)))

    def test_the_committed_list_is_the_generated_one(self):
        committed = json.loads((ROOT / "tools" / "parity" / "undetermined_raw_yaml.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, json.loads(json.dumps(gen.listing(CS), sort_keys=True)))

    def test_the_size_is_inside_the_limits_the_readers_will_run_under(self):
        total = 0
        for r in ROWS:
            n = len(r["bytes_hex"]) // 2
            self.assertLessEqual(n, gen.LIMITS["max_case_bytes"], r["id"])
            total += len(json.dumps(r))
        self.assertLessEqual(total, gen.LIMITS["max_input_bytes"])


class TheLabelsMeetTheDocumentedSubset(unittest.TestCase):
    def test_every_judged_case_agrees_with_the_reading_of_the_documented_table(self):
        wrong = disagreements(load_ref())
        self.assertEqual(wrong[:5], [], "%d of %d cases differ from the documented reading" % (len(wrong), len(ROWS)))

    def test_the_undetermined_cases_are_not_judged_and_are_counted(self):
        n = sum(1 for r in ROWS if r["parse_expect"]["result"] == "undetermined")
        self.assertEqual(n, len(CS.undetermined))
        self.assertGreaterEqual(n, 20)

    def test_the_pair_of_a_duplicate_and_a_syntax_defect_accepts_either_refusal(self):
        pairs = [r for r in ROWS if r["class"] == "pair"]
        self.assertTrue(pairs)
        self.assertEqual({r["parse_expect"]["class"] for r in pairs}, {"any"})


# (name, old text (occurs once in raw_yaml_subset_ref.py), new text, the case classes that must turn red)
MUTANTS = [
    ("a duplicate key is accepted", ('top["dup"] = True', "pass"), ["dup"]),
    ("an anchor or a tag is accepted", ("if ev.anchor is not None or (ev.tag is not None):", "if False:"), ["syntax-injected"]),
    ("a float, hex or octal is read as a string", ('if FLOATISH.fullmatch(value):\n        raise Syntax("float, hex or octal")', 'if False:\n        raise Syntax("float, hex or octal")'), ["syntax-scalar"]),
    ("a second document is accepted", ("if documents > 1:", "if False:"), ["syntax-text:second-document"]),
    ("the depth limit is gone", ("MAX_DEPTH = 64", "MAX_DEPTH = 10 ** 6"), ["limit-over:depth"]),
    ("the size limit is gone", ("MAX_BYTES = 1 << 20", "MAX_BYTES = 1 << 40"), ["limit-over:size"]),
    ("the node limit is gone", ("MAX_NODES = 100000", "MAX_NODES = 10 ** 9"), ["limit-over:nodes"]),
    ("a leading zero is accepted", ('if len(digits) > 1 and digits.startswith("0"):', "if False:"), ["syntax-scalar"]),
    ("a merge key is accepted", ('if key == "<<" and ev.style is None:', "if False:"), ["syntax-injected"]),
    ("a quoted scalar is read as plain", ("plain = ev.style is None and ev.implicit[0]", "plain = True"), ["value:envelope"]),
    ("a byte order mark inside the text is accepted", (" or (c == 0xFEFF and i > 0)", ""), ["syntax-text:bom-inside"]),
    ("a non-scalar key is accepted", ('raise Syntax("non-scalar key")', "pass"), ["syntax-nonscalar-key"]),
    ("a block scalar's trailing newline is dropped", ('return {"k": "str", "v": value}\n    if value in', 'return {"k": "str", "v": value.rstrip("\\n")}\n    if value in'), ["value:block-digest"]),
]


# cannot change an answer on any generated case, with the reason (measured: PyYAML itself refuses these characters, so the reader's own pre-scan of them is redundant for these cases)
EQUIVALENT = [
    ("the pre-scan no longer refuses a tab", ('or ch == "\\t" or', "or"), "PyYAML's scanner refuses a tab after a value and as indentation by itself"),
    ("the pre-scan no longer refuses a control character", ("if c in CONTROL or ", "if "), "PyYAML's reader refuses the control characters by itself"),
]


def mutated(old, new):
    text = REF.read_text(encoding="utf-8")
    assert text.count(old) == 1, "the text to mutate occurs %d times: %r" % (text.count(old), old)
    compile(text.replace(old, new), "mutant", "exec")
    handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    handle.write(text.replace(old, new))
    handle.close()
    return handle.name


class TheLabelsCanTellAWrongReader(unittest.TestCase):
    def test_every_planted_defect_turns_the_named_cases_red(self):
        survivors = []
        for name, (old, new), targets in MUTANTS:
            path = mutated(old, new)
            try:
                red = disagreements(load_ref(path))
            finally:
                os.unlink(path)
            classes = {c for _, c, _ in red}
            if not red:
                survivors.append(name)
                continue
            for t in targets:
                if not any(c == t or c.startswith(t) for c in classes):
                    survivors.append("%s (%s did not turn red; red: %s)" % (name, t, sorted(classes)[:6]))
        self.assertEqual(survivors, [])

    def test_the_equivalent_changes_stay_green_and_say_why(self):
        for name, (old, new), why in EQUIVALENT:
            path = mutated(old, new)
            try:
                self.assertEqual(disagreements(load_ref(path))[:3], [], "%s: %s" % (name, why))
            finally:
                os.unlink(path)

    def test_a_reader_that_echoes_the_labels_is_found_out_by_the_independent_checks(self):
        # a reader that answers what the labels say would pass the first test; the mapping and the table are what it cannot fake
        class Echo:
            def read(self, data):
                return ("refuse", "syntax")
        wrong = disagreements(Echo())
        self.assertTrue(wrong)
        self.assertTrue(any(c.startswith("built:") for _, c, _ in wrong))


if __name__ == "__main__":
    unittest.main()
