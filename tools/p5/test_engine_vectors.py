"""The check of the canonical-bytes table can fail: a tampered table, an unclassified engine vector, a wrong reference line are each refused."""
import copy
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_vectors as ev  # noqa: E402


def fake_engine(vectors, commit_file_text="x"):
    d = pathlib.Path(tempfile.mkdtemp())
    for name, (inp, exp) in vectors.items():
        v = d / "conformance/canonicalize" / name
        v.mkdir(parents=True)
        (v / "input.yaml").write_text(inp, encoding="utf-8")
        (v / "expected.yaml").write_text(exp, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    return d


class TableChecks(unittest.TestCase):
    def setUp(self):
        self.rows = ev.read_table()

    def test_the_committed_own_lines_agree_with_the_reference(self):
        self.assertEqual(ev.check_own(self.rows), [])

    def test_a_wrong_own_line_is_refused(self):
        rows = copy.deepcopy(self.rows)
        r = next(r for r in rows if r["src"] == "own")
        r["canon"] += " "
        self.assertTrue(ev.check_own(rows))

    def test_a_missing_own_line_is_refused(self):
        rows = [r for r in self.rows if r["name"] != "own_scalars"]
        self.assertTrue(ev.check_own(rows))

    def test_the_reference_escapes_the_five_go_characters(self):
        self.assertEqual(ev.reference("<>&  "), '"\\u003c\\u003e\\u0026\\u2028\\u2029"')

    def test_the_reader_of_expected_yaml_takes_a_colon_in_the_prose(self):
        e = ev.read_expected("outcome: accepted\ncanonical: '{\"v\":1}'\nwhy: A4: a colon\n")
        self.assertEqual((e["outcome"], e["canonical"]), ("accepted", '{"v":1}'))

    def test_an_engine_that_is_not_the_pinned_commit_is_refused(self):
        d = fake_engine({"object_key_ordering": ("v: 1\n", "outcome: accepted\ncanonical: '{}'\nwhy: w\n")})
        self.assertTrue(any("pin" in b for b in ev.check_engine(d, self.rows)))

    def test_an_unclassified_engine_vector_is_refused(self):
        d = fake_engine({"brand_new": ("v: 1\n", "outcome: accepted\ncanonical: '{\"v\":1}'\nwhy: w\n")})
        self.assertTrue(any("not classified" in b for b in ev.check_engine(d, self.rows)))


if __name__ == "__main__":
    unittest.main()
