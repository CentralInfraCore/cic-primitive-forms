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

    # ---- the full cases ---------------------------------------------------------------------------------------------------------
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


if __name__ == "__main__":
    unittest.main()
