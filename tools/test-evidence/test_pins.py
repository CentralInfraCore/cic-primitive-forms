"""Probes of the action pin scanner: every way a `uses:` can fail to name a commit has to be found."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pins  # noqa: E402

A = "a" * 40
B = "b" * 40


class PinTests(unittest.TestCase):
    def tree(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, text in files.items():
            p = os.path.join(tmp.name, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        return tmp.name

    def scan(self, uses_lines, extra=None):
        body = "jobs:\n  j:\n    steps:\n" + "".join("      - uses: %s\n" % u for u in uses_lines)
        files = {".github/workflows/w.yml": body}
        files.update(extra or {})
        return pins.scan(self.tree(files))

    def test_pinned_and_local_references_are_fine(self):
        actions, problems = self.scan(["actions/checkout@%s # v4.4.0" % A, "./.github/actions/x", "owner/repo/sub/path@%s" % B])
        self.assertEqual(problems, [])
        self.assertEqual(actions, {"actions/checkout": A, "owner/repo/sub/path": B})

    def test_everything_else_is_a_problem(self):
        for label, ref in (("a tag", "actions/checkout@v4"), ("a branch", "actions/checkout@main"), ("a short id", "actions/checkout@" + A[:7]),
                           ("an abbreviated id", "actions/checkout@" + A[:39]), ("an id that is too long", "actions/checkout@" + A + "a"),
                           ("an upper case id", "actions/checkout@" + A.upper()), ("a non-hex id", "actions/checkout@" + "g" * 40),
                           ("no revision", "actions/checkout"), ("an empty revision", "actions/checkout@"),
                           ("an image", "docker://alpine:3.20"), ("an image by digest", "docker://alpine@sha256:" + "0" * 64),
                           ("no owner", "checkout@" + A)):
            with self.subTest(label):
                _, problems = self.scan([ref])
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("w.yml:4", problems[0])
                if label.startswith("an image"):
                    self.assertIn("image", problems[0])  # said as what it is: nothing pins an image by a tag or a digest here

    def test_one_action_at_two_revisions_is_a_conflict(self):
        _, problems = self.scan(["actions/checkout@" + A, "actions/checkout@" + B])
        self.assertEqual(len(problems), 1)
        self.assertIn("elsewhere", problems[0])
        # in two files too
        _, problems = pins.scan(self.tree({".github/workflows/a.yml": "      - uses: x/y@%s\n" % A, ".github/workflows/b.yml": "      - uses: x/y@%s\n" % B}))
        self.assertEqual(len(problems), 1)

    def test_composite_actions_and_other_directories_are_read(self):
        _, problems = self.scan(["actions/checkout@" + A],
                                extra={".github/actions/x/action.yml": "runs:\n  steps:\n    - uses: actions/cache@v4\n"})
        self.assertEqual(len(problems), 1)
        self.assertIn("actions/x/action.yml", problems[0])

    def test_a_reference_in_a_comment_or_a_string_is_not_a_use(self):
        actions, problems = pins.scan(self.tree({".github/workflows/w.yml": "# uses: actions/checkout@v4\nrun: echo 'uses: x@y'\n"}))
        self.assertEqual((actions, problems), ({}, []))

    def test_quoted_references_are_read(self):
        _, problems = self.scan(["'actions/checkout@v4'", '"actions/checkout@v4"'])
        self.assertEqual(len(problems), 2)

    def test_this_repository_is_pinned(self):
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        if not os.path.isdir(os.path.join(root, ".github", "workflows")):
            self.skipTest("not inside the repository (the seeded-defects copy); `evidence.py pins` checks it in CI")
        actions, problems = pins.scan(root)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(actions), 5)


if __name__ == "__main__":
    unittest.main()
