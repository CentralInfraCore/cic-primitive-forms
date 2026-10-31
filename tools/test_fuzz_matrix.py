# SPDX-License-Identifier: Apache-2.0
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fuzz_matrix as fm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def langs(out):
    return sorted({t["lang"] for t in out})


class Matrix(unittest.TestCase):
    def test_full_runs_every_target(self):
        out, _ = fm.matrix("full")
        self.assertEqual(len(out), len(fm.GO_TARGETS) + len(fm.RUST_TARGETS))

    def test_light_runs_none(self):
        self.assertEqual(fm.matrix("light", 5, 5, ["corpus/x.yaml"])[0], [])

    def test_a_go_only_change_runs_only_go(self):
        out, report = fm.matrix("diff", go_ranges=3, changed=["go/sourcespan.go"])
        self.assertEqual(langs(out), ["go"])
        self.assertTrue(any(r.startswith("rust: not run") for r in report))

    def test_a_rust_only_change_runs_only_rust(self):
        self.assertEqual(langs(fm.matrix("diff", rust_ranges=1, changed=["rust/src/a.rs"])[0]), ["rust"])

    def test_docs_and_tools_only_run_nothing(self):
        self.assertEqual(fm.matrix("diff", changed=["docs/a.md", "tools/x.py", "go/a_test.go"])[0], [])

    def test_a_fuzz_target_or_seed_is_itself_run(self):
        self.assertEqual(langs(fm.matrix("diff", changed=["go/fuzz_test.go"])[0]), ["go"])
        self.assertEqual(langs(fm.matrix("diff", changed=["rust/fuzz/fuzz_targets/parse.rs"])[0]), ["rust"])
        self.assertEqual(langs(fm.matrix("diff", changed=["go/testdata/fuzz/FuzzParse/seed1"])[0]), ["go"])

    def test_the_shared_corpus_runs_both(self):
        self.assertEqual(langs(fm.matrix("diff", changed=["corpus/edit/vectors.yaml"])[0]), ["go", "rust"])


class TheTablesAreTheCode(unittest.TestCase):
    """A target added to the code and not to the table (or the reverse) would silently never run in diff mode."""

    def test_go_targets(self):
        found = []
        for name in ("fuzz_test.go", "receipt_fuzz_test.go"):   # the receipt target (P5-3) has its own file, with its property
            with open(os.path.join(ROOT, "go", name), encoding="utf-8") as fh:
                found += re.findall(r"^func Fuzz(\w+)\(", fh.read(), re.M)
        self.assertEqual(sorted(found), sorted(fm.GO_TARGETS))

    def test_rust_targets(self):
        found = [f[:-3] for f in os.listdir(os.path.join(ROOT, "rust", "fuzz", "fuzz_targets")) if f.endswith(".rs")]
        self.assertEqual(sorted(found), sorted(fm.RUST_TARGETS))


if __name__ == "__main__":
    unittest.main()
