"""The selection from the decisions, fail-closed.

    python3 -m unittest test_select_tests
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_tests as st  # noqa: E402


def plan(trust="main", tooling="main"):
    return {"previous_source": {"trust": trust, "branch": "main"}, "tooling_trust": tooling, "decisions": [
        {"key": "go:TestA", "status": "REUSE_ELIGIBLE"}, {"key": "go:TestB", "status": "RERUN_REQUIRED"},
        {"key": "go:FuzzC", "status": "UNVERIFIABLE"}, {"key": "go:TestD", "status": "NO_EVIDENCE"},
        {"key": "rust:cic_primitive_forms::access::tests::x", "status": "REUSE_ELIGIBLE"},
        {"key": "rust:validate::a::b", "status": "REUSE_ELIGIBLE"}, {"key": "rust:validate::c", "status": "RERUN_REQUIRED"}]}


class Select(unittest.TestCase):
    def test_only_reuse_eligible_is_skipped(self):
        self.assertEqual(st.eligible(plan(), "go"), ["TestA"])
        self.assertEqual(st.eligible(plan(), "rust"), ["cic_primitive_forms::access::tests::x", "validate::a::b"])

    def test_authorization_needs_evidence_and_planner_from_main(self):
        self.assertTrue(st.authorized(plan()))
        self.assertFalse(st.authorized(plan(trust="branch")))
        self.assertFalse(st.authorized(plan(trust="none")))
        self.assertFalse(st.authorized(plan(tooling="branch")))
        self.assertFalse(st.authorized({"decisions": []}))

    def test_go_pattern_is_exact_and_escaped(self):
        rx = re.compile(st.go_skip_regex(["TestA", "Test.B"]))
        self.assertTrue(rx.match("TestA"))
        self.assertTrue(rx.match("Test.B"))
        self.assertFalse(rx.match("TestAB"))   # a prefix is another test
        self.assertFalse(rx.match("XTestA"))
        self.assertFalse(rx.match("TestXB"))   # the dot is a dot
        self.assertIsNone(st.go_skip_regex([]))

    def test_go_command_without_skips_is_the_command_of_p1(self):
        self.assertEqual(st.go_command([]), ["go", "test", "-race", "-count=1", "-rapid.checks=1000", "./..."])

    def test_rust_groups_and_targets(self):
        g = st.rust_groups(["cic_primitive_forms::access::tests::x", "validate::a::b", "validate::c"])
        self.assertEqual(g, {"cic_primitive_forms": ["access::tests::x"], "validate": ["a::b", "c"]})
        cmds = st.rust_commands(["cic_primitive_forms::access::tests::x", "validate::a::b"])
        self.assertIn("--lib", cmds[0])
        self.assertIn(["--test", "validate"], [cmds[1][i:i + 2] for i in range(len(cmds[1]) - 1)])
        self.assertIn("--exact", cmds[0])

    def test_nothing_to_skip_gives_no_rust_command(self):
        self.assertEqual(st.rust_commands([]), [])


if __name__ == "__main__":
    unittest.main()
