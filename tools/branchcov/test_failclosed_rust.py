#!/usr/bin/env python3
"""The Rust measurement fails closed (ADR 0009, requirement 4). Needs `cargo` on PATH; builds rustbranch once."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ADAPTER = os.path.join(HERE, "adapter.py")
SCOPE = """format: cic-coverage-scope/1
languages:
  rust: {root: c, include: ["src/**/*.rs"], exclude_test: [], exclude_generated: []}
  go: {root: g, include: ["**/*.go"], exclude_test: ["**/*_test.go"], exclude_generated: []}
"""
OK = {
    "Cargo.toml": '[package]\nname = "c"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n',
    "src/lib.rs": "pub fn f(x: i32) -> i32 {\n    if x > 0 {\n        1\n    } else {\n        0\n    }\n}\n",
    "tests/t.rs": "#[test]\nfn t() {\n    assert_eq!(c::f(1), 1);\n}\n",
}


class FailClosedRust(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rustbranch-test-")
        env = dict(os.environ, CARGO_TARGET_DIR=os.path.join(cls.tmp, "target"))
        subprocess.run(["cargo", "build", "--release", "--locked"], cwd=os.path.join(HERE, "rust"), check=True, env=env,
                       capture_output=True)
        cls.bin = os.path.join(cls.tmp, "target", "release", "rustbranch")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def crate(self, files):
        root = tempfile.mkdtemp(dir=self.tmp)
        for name, text in files.items():
            p = os.path.join(root, "c", name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        with open(os.path.join(root, "scope.yaml"), "w") as fh:
            fh.write(SCOPE)
        return root

    def measure(self, root):
        out = os.path.join(root, "run.json")
        env = dict(os.environ, CARGO_TARGET_DIR=os.path.join(self.tmp, "t2"))
        r = subprocess.run([self.bin, "run", "--dir", os.path.join(root, "c"), "--out", out], capture_output=True, text=True, env=env)
        return r, out

    def adapt(self, root, run):
        return subprocess.run([sys.executable, ADAPTER, "rust", "--run", run, "--root", root, "--module-dir", "c",
                               "--scope", os.path.join(root, "scope.yaml"), "--commit", "0" * 40,
                               "--out", os.path.join(root, "report.json")], capture_output=True, text=True)

    def test_baseline_measures_and_adapts(self):
        root = self.crate(OK)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        a = self.adapt(root, out)
        self.assertEqual(a.returncode, 0, a.stderr)
        with open(os.path.join(root, "report.json")) as fh:
            rep = json.load(fh)
        self.assertEqual((rep["totals"]["total"], rep["totals"]["covered"]), (2, 1))

    def refused(self, files, needle):
        root = self.crate(files)
        r, _ = self.measure(root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(needle, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "run.json")))

    def test_failing_test(self):
        f = dict(OK); f["tests/t.rs"] = "#[test]\nfn t() {\n    assert_eq!(c::f(1), 2);\n}\n"
        self.refused(f, "failing run")

    def test_condition_inside_a_macro(self):
        f = dict(OK); f["src/lib.rs"] += "pub fn g(a: bool, b: bool) {\n    assert!(a && b);\n}\n"
        self.refused(f, "macro")

    def test_conditional_compilation(self):
        f = dict(OK); f["src/lib.rs"] += "#[cfg(unix)]\npub fn g(a: bool) -> bool {\n    if a { true } else { false }\n}\n"
        self.refused(f, "conditional compilation")

    def test_let_else(self):
        f = dict(OK); f["src/lib.rs"] += "pub fn g(o: Option<i32>) -> i32 {\n    let Some(v) = o else { return 0 };\n    v\n}\n"
        self.refused(f, "let-else")

    def test_build_script(self):
        f = dict(OK); f["build.rs"] = "fn main() {}\n"
        self.refused(f, "build scripts")

    def test_workspace(self):
        f = dict(OK); f["Cargo.toml"] += "\n[workspace]\n"
        self.refused(f, "workspaces")

    def test_file_changed_after_the_measurement_is_refused_by_the_adapter(self):
        root = self.crate(OK)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        with open(os.path.join(root, "c", "src", "lib.rs"), "a") as fh:
            fh.write("\n// edited after the run\n")
        a = self.adapt(root, out)
        self.assertNotEqual(a.returncode, 0)
        self.assertIn("changed", a.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "report.json")))

    def test_a_source_file_no_module_declares_is_still_counted_as_uncovered(self):
        f = dict(OK); f["src/orphan.rs"] = "pub fn o(a: bool) -> bool {\n    if a { true } else { false }\n}\n"
        root = self.crate(f)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(self.adapt(root, out).returncode, 0)
        with open(os.path.join(root, "report.json")) as fh:
            rep = json.load(fh)
        self.assertEqual((rep["totals"]["total"], rep["totals"]["covered"]), (4, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
