import hashlib
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import receipt_cases as rc  # noqa: E402


class Cases(unittest.TestCase):
    def test_the_committed_file_is_what_the_script_writes(self):
        self.assertEqual(rc.OUT.read_text(), json.dumps(rc.build(), indent=1, ensure_ascii=False) + "\n")

    def test_one_case_per_accepted_materialize_case(self):
        src = json.loads(rc.SRC.read_text())
        accepted = [c["id"] for c in src["cases"] if "refused" not in c["expect"]]
        self.assertEqual([c["id"] for c in rc.build()["cases"]], accepted)
        self.assertEqual(len(accepted), 7)

    def test_the_binding_is_exactly_the_applied_defaults_and_they_are_in_byte_order(self):
        for c in rc.build()["cases"]:
            body = json.loads(c["document"])["materialization"]
            paths = [d["path"] for d in body["applied_defaults"]]
            self.assertEqual(paths, sorted(paths, key=lambda p: p.encode()))
            self.assertEqual(sorted(c["binding"]), paths)
            self.assertEqual(body["derived_values"], [])
            self.assertEqual(len(body), 15)

    def test_the_release_data_is_fixture_data_with_the_right_form(self):
        for k in ("primitive_build_hash", "grammar_sha256", "grammar_schema_sha256"):
            self.assertRegex(rc.RELEASE[k], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(rc.RELEASE["primitive_release"].startswith("fixture/"))

    def test_the_schema_digest_is_the_digest_of_the_raw_text(self):
        src = json.loads(rc.SRC.read_text())
        for c in rc.build()["cases"]:
            case = next(x for x in src["cases"] if x["id"] == c["id"])
            want = "sha256:" + hashlib.sha256(src["schemas"][case["schema"]].encode()).hexdigest()
            self.assertEqual(json.loads(c["document"])["materialization"]["schema_digest"], want)


if __name__ == "__main__":
    unittest.main()
