"""The two defects an external review reproduced, kept as tests (planted inputs, synthetic).

    python3 -m unittest lab/measure/test_review_findings.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_data  # noqa: E402
import trace_data  # noqa: E402


class ATestWithoutANode(unittest.TestCase):
    def test_expect_sound_fails(self):
        with tempfile.TemporaryDirectory() as d:
            graph = {"nodes": [
                {"id": "f", "kind": "func", "file": "a.go", "line": 1, "end_line": 3, "test": False, "src_sha256": "x", "deps": []},
                {"id": "t", "kind": "func", "file": "a_test.go", "line": 1, "end_line": 3, "test": True, "src_sha256": "y",
                 "deps": [], "entry": True}]}
            json.dump(graph, open(os.path.join(d, "g.json"), "w"))
            json.dump({"tests": {"T1": {"node": None, "rc": 0, "tail": "", "ran": ["f"]}}, "init": []},
                      open(os.path.join(d, "runs.json"), "w"))
            r = subprocess.run([sys.executable, os.path.join(HERE, "graph_vs_coverage.py"), "--typed", d + "/g.json",
                                "--runs", d + "/runs.json", "--expect", "sound"], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("without a node", r.stderr)


class APathRelativeToADirectoryFd(unittest.TestCase):
    def parse(self, text):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(text)
        try:
            return trace_data.parse(f.name, "/repo", "/repo", {})[0]
        finally:
            os.unlink(f.name)

    def test_resolved_with_the_fd_path(self):
        deps = self.parse('123 openat(AT_FDCWD</repo>, "/repo/testdata", O_RDONLY|O_DIRECTORY) = 3</repo/testdata>\n'
                          '123 openat(3</repo/testdata>, "config.yaml", O_RDONLY|O_CLOEXEC) = 4</repo/testdata/config.yaml>\n')
        self.assertEqual(deps, {"testdata": "dir", "testdata/config.yaml": "path"})

    def test_unknown_fd_path_is_opaque(self):
        deps = self.parse('123 openat(3, "config.yaml", O_RDONLY|O_CLOEXEC) = 4\n')
        self.assertEqual(deps, {trace_data.UNRESOLVED: "opaque"})

    def test_an_opaque_read_is_never_the_same(self):
        self.assertNotEqual(backtest_data.content(".", "HEAD", trace_data.UNRESOLVED, "opaque"),
                            backtest_data.content(".", "HEAD", trace_data.UNRESOLVED, "opaque"))


if __name__ == "__main__":
    unittest.main()
