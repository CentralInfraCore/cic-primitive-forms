import hashlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import materialize_cases as mc  # noqa: E402


class Cases(unittest.TestCase):
    def test_the_committed_file_is_what_the_script_writes(self):
        self.assertEqual(mc.OUT.read_text(), json.dumps(mc.build(), indent=1, ensure_ascii=False) + "\n")

    def test_every_digest_is_the_sha256_of_its_projection_and_keys_are_sorted(self):
        n = 0
        for c in mc.build()["cases"]:
            e = c["expect"]
            if "refused" in e:
                continue
            n += 1
            self.assertEqual(e["output_digest"], "sha256:" + hashlib.sha256(e["projection"].encode()).hexdigest())
            keys = [f["key"] for f in json.loads(e["projection"])["fields"]]
            self.assertEqual(keys, sorted(keys))
            self.assertEqual(keys, e["expected_keys"])
        self.assertEqual(n, 7)

    def test_the_five_characters_and_controls_are_escaped_as_the_engine_writes_them(self):
        case = next(c for c in mc.build()["cases"] if c["id"] == "flat.escaping")
        self.assertIn("a\\u003cb\\u003e\\u0026c\\u2028\\td", case["expect"]["projection"])
        self.assertNotIn("\u2028", case["expect"]["projection"])

    def test_a_changed_case_is_seen(self):
        mc.CASES.append(mc.ok("x", "flat", "type: t\n", mc.caps(*mc.FLAT5), []))
        try:
            self.assertNotEqual(mc.OUT.read_text(), json.dumps(mc.build(), indent=1, ensure_ascii=False) + "\n")
        finally:
            mc.CASES.pop()


if __name__ == "__main__":
    unittest.main()
