#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tests of tools/diff_gate.py: a throwaway git repository with a base commit, a change, and the classification of the change."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_gate  # noqa: E402

GO_TEST = """package forms_test

import (
\t"testing"
)

func TestOne(t *testing.T) {
\tif 1 != 1 {
\t\tt.Fatal("no")
\t}
}
"""
RUST_TEST = """use std::fs;

#[test]
fn one() {
    assert_eq!(1, 1);
}
"""
GO_SRC = "package forms\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n\nfunc Sub(a, b int) int {\n\treturn a - b\n}\n"
RUST_SRC = "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n\npub fn sub(a: i32, b: i32) -> i32 {\n    a - b\n}\n"
RUST_SRC_WITH_TESTS = RUST_SRC + "\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n    #[test]\n    fn adds() {\n        assert_eq!(add(1, 2), 3);\n    }\n}\n"


class Scope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("go/a.go", GO_SRC)
        self.write("go/a_test.go", GO_TEST)
        self.write("rust/src/lib.rs", RUST_SRC)
        self.write("rust/src/w.rs", RUST_SRC_WITH_TESTS)
        self.write("rust/tests/t.rs", RUST_TEST)
        self.write("rust/Cargo.toml", "[package]\nname = \"x\"\n")
        self.write("testdata/p1/c.json", '{"a": 1}\n')
        self.write("corpus/v.yaml", "- id: a\n- id: b\n")
        self.write("docs/x.md", "doc\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        return subprocess.run(["git", "-C", self.root] + list(a), capture_output=True, text=True, check=True).stdout

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def scope(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")
        verdict, pr, pg = diff_gate.classify(self.root, self.base, "HEAD")
        return verdict, pr, pg

    def mode(self):
        return self.scope()[0]["mode"]

    def write_bytes(self, rel, data):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)

    # ---- a file whose bytes are not UTF-8 (#115) -------------------------------------------------------------------------------
    def test_a_text_file_with_a_byte_that_is_not_utf8_does_not_crash_the_gate(self):
        # no NUL, so git diffs it as text; a fuzz seed of one byte 0xff is exactly this
        self.write_bytes("rust/fuzz/corpus/read_diff/seed", b"\xff")
        valid = self.mode()
        self.assertIn(valid, ("diff", "full"))

    def test_the_verdict_of_a_non_utf8_file_is_that_of_the_same_file_in_utf8(self):
        self.write_bytes("rust/fuzz/corpus/read_diff/seed", b"\xff")
        broken = self.scope()[0]
        self.git("reset", "-q", "--hard", self.base)
        self.write_bytes("rust/fuzz/corpus/read_diff/seed", b"x")
        plain = self.scope()[0]
        self.assertEqual((broken["mode"], broken["reasons"]), (plain["mode"], plain["reasons"]))

    def test_a_non_utf8_file_beside_a_product_change_leaves_the_product_ranges(self):
        self.write("go/a.go", GO_SRC.replace("a + b", "a + b + 0"))
        self.write_bytes("rust/fuzz/corpus/read_diff/seed", b"\xff\xfe")
        v, pr, pg = self.scope()
        self.assertEqual(pg["go/a.go"][0], [[4, 4]])

    # ---- the diff cases -------------------------------------------------------------------------------------------------------
    def test_documentation_only_has_nothing_to_mutate(self):
        self.write("docs/x.md", "doc\nmore\n")
        v, pr, pg = self.scope()
        self.assertEqual((v["mode"], pr, pg), ("diff", {}, {}))

    def test_a_product_change_is_the_diff(self):
        self.write("go/a.go", GO_SRC.replace("a + b", "a + b + 0"))
        v, pr, pg = self.scope()
        self.assertEqual(v["mode"], "diff")
        self.assertEqual(pg["go/a.go"][0], [[4, 4]])

    def test_a_deletion_in_product_code_counts_its_neighbours(self):
        self.write("go/a.go", "package forms\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n")
        v, pr, pg = self.scope()
        self.assertEqual(v["mode"], "diff")
        self.assertTrue(pg["go/a.go"][0], "the cut must leave a range of changed code")

    def test_new_tests_are_an_addition_go(self):
        self.write("go/a_test.go", GO_TEST + "\nfunc TestTwo(t *testing.T) {\n\tt.Log(1)\n}\n")
        self.assertEqual(self.mode(), "diff")

    def test_new_tests_are_an_addition_rust(self):
        self.write("rust/tests/t.rs", RUST_TEST + "\n#[test]\nfn two() {\n    assert_eq!(2, 2);\n}\n")
        self.assertEqual(self.mode(), "diff")

    def test_a_new_test_file_is_an_addition(self):
        self.write("go/b_test.go", GO_TEST)
        self.write("rust/tests/u.rs", RUST_TEST)
        self.assertEqual(self.mode(), "diff")

    def test_an_import_that_is_only_added_is_an_addition(self):
        self.write("go/a_test.go", GO_TEST.replace('\t"testing"\n', '\t"os"\n\t"testing"\n'))
        self.assertEqual(self.mode(), "diff")

    def test_a_name_added_to_a_rust_use_list_is_an_addition(self):
        self.write("rust/tests/t.rs", RUST_TEST.replace("use std::fs;", "use std::{fs, io};"))
        self.assertEqual(self.mode(), "diff")

    def test_lines_appended_to_a_data_file_are_an_addition(self):
        self.write("corpus/v.yaml", "- id: a\n- id: b\n- id: c\n")
        self.assertEqual(self.mode(), "diff")

    def test_a_new_unit_test_in_the_test_module_of_a_source_file_is_an_addition(self):
        self.write("rust/src/w.rs", RUST_SRC_WITH_TESTS[:-2] + "\n    #[test]\n    fn adds_more() {\n        assert_eq!(add(2, 2), 4);\n    }\n}\n")
        v, pr, pg = self.scope()
        self.assertEqual(v["mode"], "diff", v["reasons"])
        self.assertEqual(pr, {})

    def test_production_code_after_a_test_module_is_still_the_diff(self):
        self.write("rust/src/w.rs", RUST_SRC_WITH_TESTS + "\npub fn mul(a: i32, b: i32) -> i32 {\n    a * b\n}\n")
        v, pr, pg = self.scope()
        self.assertEqual(v["mode"], "diff", v["reasons"])
        self.assertEqual(pr["rust/src/w.rs"][0], [[len(RUST_SRC_WITH_TESTS.splitlines()) + 1, len(RUST_SRC_WITH_TESTS.splitlines()) + 4]])

    def test_a_test_module_that_cannot_be_closed_is_full(self):
        self.write("rust/src/w.rs", RUST_SRC_WITH_TESTS[:-2] + "\npub fn mul() {}\n")
        self.assertEqual(self.mode(), "full")

    # ---- the full cases ---------------------------------------------------------------------------------------------------------
    def test_a_new_go_test_file_with_a_package_level_var_is_full(self):
        self.write("go/b_test.go", "package forms_test\n\nimport \"os\"\n\nvar _ = func() int { os.Exit(0); return 0 }()\n")
        self.assertEqual(self.mode(), "full")

    def test_a_new_go_test_file_with_a_side_effect_import_is_full(self):
        self.write("go/b_test.go", "package forms_test\n\nimport _ \"example.com/x\"\n")
        self.assertEqual(self.mode(), "full")

    def test_a_line_added_to_the_list_of_accepted_parity_differences_is_full(self):
        self.write("testdata/p1/parity-known.txt", "a\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "list")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write("testdata/p1/parity-known.txt", "a\nb\n")
        self.assertEqual(self.mode(), "full")

    def test_a_modified_assert_is_full(self):
        self.write("go/a_test.go", GO_TEST.replace("1 != 1", "1 != 2"))
        self.assertEqual(self.mode(), "full")

    def test_a_removed_test_is_full(self):
        self.write("rust/tests/t.rs", "use std::fs;\n")
        self.assertEqual(self.mode(), "full")

    def test_a_deleted_test_file_is_full(self):
        os.remove(os.path.join(self.root, "go", "a_test.go"))
        self.assertEqual(self.mode(), "full")

    def test_a_line_added_inside_an_existing_test_is_full(self):
        self.write("go/a_test.go", GO_TEST.replace("\tif 1 != 1 {", "\tt.Log(1)\n\tif 1 != 1 {"))
        self.assertEqual(self.mode(), "full")
        self.git("reset", "-q", "--hard", self.base)
        self.write("rust/tests/t.rs", RUST_TEST.replace("    assert_eq!(1, 1);", "    assert_eq!(1, 1);\n    assert_eq!(2, 2);"))
        self.assertEqual(self.mode(), "full")

    def test_a_modified_data_line_or_an_insertion_in_the_middle_is_full(self):
        self.write("testdata/p1/c.json", '{"a": 2}\n')
        self.assertEqual(self.mode(), "full")
        self.git("reset", "-q", "--hard", self.base)
        self.write("corpus/v.yaml", "- id: a\n- id: x\n- id: b\n")
        self.assertEqual(self.mode(), "full")

    def test_a_change_of_how_the_gate_measures_is_full(self):
        self.write("rust/Cargo.toml", "[package]\nname = \"x\"\nversion = \"1\"\n")
        self.assertEqual(self.mode(), "full")

    def test_a_tool_that_decides_what_runs_in_diff_mode_is_full(self):
        for rel in ("tools/shard_count.py", "tools/shard_budget.json", "tools/fuzz_matrix.py", "tools/seeded_defects.py"):
            self.assertEqual(diff_gate.kind(rel), "build", rel)

    def test_a_new_go_test_file_that_changes_how_every_test_runs_is_full(self):
        # the review of #108: a new test file is an addition only if it can only add kills; TestMain can end the process before a single test, init sets the world
        head = 'package forms_test\n\nimport (\n\t"os"\n\t"testing"\n)\n\nvar _ = os.Exit\nvar _ testing.TB\n\n'
        for body in ("func TestMain(m *testing.M) {\n\tos.Exit(0)\n}\n", 'func init() {\n\tos.Setenv("A", "b")\n}\n'):
            self.git("reset", "-q", "--hard", self.base)
            self.write("go/m_test.go", head + body)
            self.assertEqual(self.mode(), "full", body)

    def test_testmain_added_to_an_existing_go_test_file_is_full_too(self):
        self.write("go/a_test.go", GO_TEST + "\nfunc TestMain(m *testing.M) {\n\tm.Run()\n}\n")
        self.assertEqual(self.mode(), "full")

    def test_a_new_go_test_file_of_ordinary_tests_is_still_an_addition(self):
        self.write("go/n_test.go", GO_TEST.replace("TestOne", "TestTwo"))
        self.assertEqual(self.mode(), "diff")

    def test_a_change_of_the_gate_workflow_alone_is_diff_with_a_note(self):
        # the pull request stays fast; a FULL runs in the background after the merge (nightly.yml)
        self.write(".github/workflows/p1.yml", "name: p1\n")
        self.assertEqual(self.mode(), "diff")

    def test_the_gate_workflow_with_a_build_config_change_is_still_full(self):
        self.write(".github/workflows/p1.yml", "name: p1\n")
        self.write("rust/Cargo.toml", "[package]\nname = \"x\"\nversion = \"1\"\n")
        self.assertEqual(self.mode(), "full")

    def test_a_modified_unit_test_inside_a_source_file_is_full(self):
        self.write("rust/src/w.rs", RUST_SRC_WITH_TESTS.replace("add(1, 2), 3", "add(1, 2), 4"))
        self.assertEqual(self.mode(), "full")

    def test_product_and_a_weakened_test_together_is_full(self):
        self.write("go/a.go", GO_SRC.replace("a + b", "a + b + 0"))
        self.write("go/a_test.go", GO_TEST.replace("1 != 1", "1 != 2"))
        self.assertEqual(self.mode(), "full")


class Patch(unittest.TestCase):
    def test_the_patch_and_the_lines_file(self):
        with tempfile.TemporaryDirectory() as d:
            verdict = {"mode": "diff", "reasons": [], "notes": []}
            diff_gate.write_outputs(d, verdict, {"rust/src/a.rs": ([[3, 4]], ["l1", "l2", "l3", "l4", "l5"])}, {"go/a.go": ([[2, 2]], ["x", "y"])})
            with open(os.path.join(d, "rust.patch"), encoding="utf-8") as fh:
                patch = fh.read()
            self.assertIn("+++ b/src/a.rs", patch)
            self.assertIn("@@ -2,0 +3,2 @@\n+l3\n+l4\n", patch)
            with open(os.path.join(d, "diff-lines.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"go/a.go": [[2, 2]]})
            with open(os.path.join(d, "scope.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["rust_ranges"], 1)

    def test_a_mutant_is_in_the_diff_only_if_it_changes_a_changed_line(self):
        with tempfile.TemporaryDirectory() as d:
            orig = os.path.join(d, "o.go")
            with open(orig, "w") as fh:
                fh.write("a\nb\nc\nd\n")
            mut_in = os.path.join(d, "m1.go")
            with open(mut_in, "w") as fh:
                fh.write("a\nB\nc\nd\n")
            mut_out = os.path.join(d, "m2.go")
            with open(mut_out, "w") as fh:
                fh.write("a\nb\nc\nD\n")
            lines = os.path.join(d, "l.json")
            with open(lines, "w") as fh:
                json.dump({"go/x.go": [[2, 3]]}, fh)
            self.assertTrue(diff_gate.in_diff(lines, "go/x.go", orig, mut_in))
            self.assertFalse(diff_gate.in_diff(lines, "go/x.go", orig, mut_out))
            self.assertFalse(diff_gate.in_diff(lines, "go/other.go", orig, mut_in))



class Narrowing(unittest.TestCase):
    """#92: a change inside an existing test is narrowed to the product lines the base version of that test executed."""

    setUp, tearDown, git, write, scope = Scope.setUp, Scope.tearDown, Scope.git, Scope.write, Scope.scope

    def test_the_test_that_holds_a_line(self):
        go = GO_TEST.splitlines() + ["", "func helper() int {", "\treturn 1", "}"]
        self.assertEqual(diff_gate.enclosing_test("go", go, 7), "TestOne")       # inside the body
        self.assertEqual(diff_gate.enclosing_test("go", go, 11), "TestOne")      # the closing brace
        self.assertIsNone(diff_gate.enclosing_test("go", go, 3))                 # the import block
        self.assertIsNone(diff_gate.enclosing_test("go", go, 14))                # a helper
        self.assertIsNone(diff_gate.enclosing_test("go", go, 99))
        rs = RUST_TEST.splitlines() + ["", "fn helper() -> i32 {", "    1", "}"]
        self.assertEqual(diff_gate.enclosing_test("rust", rs, 5), "one")
        self.assertIsNone(diff_gate.enclosing_test("rust", rs, 9))               # a fn without #[test]
        self.assertIsNone(diff_gate.enclosing_test("rust", rs, 1))
        unit = RUST_SRC_WITH_TESTS.splitlines()
        self.assertEqual(diff_gate.enclosing_test("rust", unit, unit.index("        assert_eq!(add(1, 2), 3);") + 1), "adds")
        prop = ["proptest! {", "    /// doc", "    #[test]", "    fn holds(x in 0..3i32) {", "        prop_assert!(x < 3);", "    }", "}"]
        self.assertEqual(diff_gate.enclosing_test("rust", prop, 5), "holds")

    def test_a_modified_assert_is_pending_its_measurement(self):
        self.write("go/a_test.go", GO_TEST.replace("1 != 1", "1 != 2"))
        v, _, _ = self.scope()
        self.assertEqual((v["mode"], v["reasons"], v["widen"]), ("full", [diff_gate.PENDING], {"go": {"go": ["TestOne"]}}))

    def test_rust_tests_are_named_with_their_target(self):
        self.write("rust/tests/t.rs", RUST_TEST.replace("assert_eq!(1, 1);", "assert_eq!(1, 2 - 1);"))
        self.write("rust/src/w.rs", RUST_SRC_WITH_TESTS.replace("add(1, 2), 3", "add(2, 1), 3"))
        v, _, _ = self.scope()
        self.assertEqual(v["reasons"], [diff_gate.PENDING])
        self.assertEqual(v["widen"], {"rust": {"lib": ["adds"], "t": ["one"]}})

    def test_a_change_outside_a_test_function_stays_full(self):
        self.write("go/a_test.go", GO_TEST.replace('\t"testing"\n', '\t"testing"\n\t"fmt"\n').replace("import (", "import (\n\t_ \"os\"") + "\nvar table = 1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a helper")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write("go/a_test.go", GO_TEST.replace('\t"testing"\n', '\t"testing"\n\t"fmt"\n').replace("import (", "import (\n\t_ \"os\"") + "\nvar table = 2\n")
        v, _, _ = self.scope()
        self.assertEqual(v["mode"], "full")
        self.assertNotIn(diff_gate.PENDING, v["reasons"])
        self.assertIn("outside a test function", v["reasons"][0])
        self.assertEqual(v["widen"], {})

    def test_test_data_stays_full(self):
        self.write("testdata/p1/c.json", '{"a": 2}\n')
        v, _, _ = self.scope()
        self.assertEqual((v["mode"], v["widen"]), ("full", {}))

    def test_the_measurement_decides_the_mode(self):
        v = {"mode": "full", "reasons": [diff_gate.PENDING], "notes": [], "widen": {"go": {"go": ["TestOne"]}}}
        ok = diff_gate.narrowed(v, lambda: ["measured"])
        self.assertEqual((ok["mode"], ok["reasons"], ok["notes"]), ("diff", [], ["measured"]))

        def fails():
            raise diff_gate.MeasureError("the changed Go tests did not all run\nlog")
        bad = diff_gate.narrowed(v, fails)
        self.assertEqual(bad["mode"], "full")
        self.assertEqual(bad["reasons"], ["the coverage of the changed tests could not be measured: the changed Go tests did not all run"])
        other = diff_gate.narrowed(dict(v, reasons=[diff_gate.PENDING, "a data line"]), lambda: [])
        self.assertEqual((other["mode"], other["reasons"]), ("full", ["a data line"]))   # another reason keeps it full

    def test_the_lines_the_old_test_executed_join_the_diff_on_the_head(self):
        # the head shifts a.go by one line and changes Sub; the base version of the changed test executed Add's body (base line 4)
        self.write("go/a.go", "// a comment\n" + GO_SRC.replace("a - b", "b - a"))
        self.write("go/a_test.go", GO_TEST.replace("1 != 1", "1 != 2"))
        v, pr, pg = self.scope()
        self.assertEqual(pg["go/a.go"][0], [[1, 1], [9, 9]])                    # the new comment and the changed Sub
        seen = []

        def run(lang, wt, targets):
            seen.append((lang, targets, os.path.exists(os.path.join(wt, "go", "a.go"))))
            with open(os.path.join(wt, "go", "a.go"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), GO_SRC)                             # the base, not the head
            return {"go/a.go": {4, 8}}                                          # Add's return, and Sub's (changed by the PR)
        notes = diff_gate.measure(self.root, self.base, "HEAD", v["widen"], pr, pg, run=run)
        self.assertEqual(seen, [("go", {"go": ["TestOne"]}, True)])
        self.assertEqual(pg["go/a.go"][0], [[1, 1], [5, 5], [9, 9]])            # base 4 is head 5; base 8 changed, it is in the diff already
        self.assertIn("TestOne", notes[0])
        self.assertNotIn("diff-gate-base-", self.git("worktree", "list"))     # the worktree is gone

    def test_a_measurement_of_nothing_is_an_error(self):
        self.write("go/a_test.go", GO_TEST.replace("1 != 1", "1 != 2"))
        v, pr, pg = self.scope()
        with self.assertRaises(diff_gate.MeasureError):
            diff_gate.measure(self.root, self.base, "HEAD", v["widen"], pr, pg, run=lambda *a: {})

    def test_the_coverage_formats(self):
        prof = "mode: set\nm/go/a.go:3.10,5.2 2 1\nm/go/a.go:7.1,8.2 1 0\nm/go/a_test.go:1.1,2.2 1 1\nother/x.go:1.1,1.9 1 1\n"
        self.assertEqual(diff_gate.parse_go_profile(prof, "m/go"), {"go/a.go": {3, 4, 5}})
        with self.assertRaises(diff_gate.MeasureError):
            diff_gate.parse_go_profile("mode: set\nnonsense\n", "m/go")
        lcov = "SF:/w/rust/src/a.rs\nDA:1,0\nDA:2,3\nend_of_record\nSF:/w/rust/tests/t.rs\nDA:5,1\nend_of_record\n"
        self.assertEqual(diff_gate.parse_lcov(lcov, "/w"), {"rust/src/a.rs": {2}})

    def test_the_line_helpers(self):
        self.assertEqual(diff_gate.carry_lines(["a", "b", "c"], ["x", "a", "B", "c"], {1, 2, 3}), {2, 4})
        self.assertEqual(diff_gate.merge_ranges([[5, 6], [1, 2], [3, 3], [9, 9]]), [[1, 3], [5, 6], [9, 9]])
        self.assertEqual(diff_gate.merge_ranges([[1, 4], [2, 3], [5, 5]]), [[1, 5]])
        self.assertEqual(diff_gate.to_ranges({4, 2, 3, 7}), [[2, 4], [7, 7]])


class InDiffAnswers(unittest.TestCase):
    """`diff_gate.py in-diff` answers 0 (the mutant changes a line of the diff), 1 (it does not) or 2 (it could not tell): a crash is not "outside the diff"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.paths = {}
        for name, text in (("orig", "a\nb\nc\nd\n"), ("in", "a\nB\nc\nd\n"), ("out", "a\nb\nc\nD\n"), ("lines", json.dumps({"go/x.go": [[2, 3]]})), ("bad", "{not json")):
            self.paths[name] = os.path.join(d, name)
            with open(self.paths[name], "w") as fh:
                fh.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, lines, changed, original=None):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diff_gate.py")
        r = subprocess.run([sys.executable, script, "in-diff", "--lines", lines, "--path", "go/x.go", "--original", original or self.paths["orig"], "--changed", changed],
                           capture_output=True, text=True)
        return r.returncode

    def test_in_the_diff_outside_it_and_could_not_tell(self):
        p = self.paths
        self.assertEqual(self.ask(p["lines"], p["in"]), 0)
        self.assertEqual(self.ask(p["lines"], p["out"]), 1)
        self.assertEqual(self.ask(os.path.join(self.tmp.name, "absent"), p["in"]), 2)      # no list of changed lines
        self.assertEqual(self.ask(p["bad"], p["in"]), 2)                                  # a list that is not JSON
        self.assertEqual(self.ask(p["lines"], os.path.join(self.tmp.name, "absent")), 2)  # no mutant file
        self.assertEqual(self.ask(p["lines"], p["in"], original=os.path.join(self.tmp.name, "absent")), 2)


if __name__ == "__main__":
    unittest.main()
