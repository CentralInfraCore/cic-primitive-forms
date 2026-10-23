# SPDX-License-Identifier: Apache-2.0
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seeded_defects as sd  # noqa: E402


class Select(unittest.TestCase):
    def test_only_the_defects_of_changed_files(self):
        got = sd.in_diff(sd.DEFECTS, {"go/sourcespan.go"})
        self.assertTrue(got)
        self.assertTrue(all(d[0] == "go" and d[2] == "sourcespan.go" for d in got))

    def test_a_rust_file_is_keyed_under_rust(self):
        got = sd.in_diff(sd.DEFECTS, {"rust/src/access.rs"})
        self.assertTrue(got and all(d[0] == "rust" and d[2] == "src/access.rs" for d in got))

    def test_nothing_changed_selects_nothing(self):
        self.assertEqual(sd.in_diff(sd.DEFECTS, set()), [])
        self.assertEqual(sd.in_diff(sd.DEFECTS, {"docs/a.md"}), [])

    def test_every_defect_file_is_a_real_product_file(self):
        # a defect keyed on a file that does not exist could never be selected by a diff
        import os
        for lang, name, rel, _ in sd.DEFECTS:
            self.assertTrue(os.path.exists(os.path.join(sd.ROOT, lang, rel)), "%s: %s/%s" % (name, lang, rel))


if __name__ == "__main__":
    unittest.main()
