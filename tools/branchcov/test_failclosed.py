#!/usr/bin/env python3
"""The Go measurement fails closed (ADR 0009, requirement 4): a case it cannot measure gives no report.

Needs `go` on PATH. Builds gobranch once, then makes tiny throwaway modules.
"""
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
  rust: {root: r, include: ["src/**/*.rs"], exclude_test: [], exclude_generated: []}
  go: {root: m, include: ["**/*.go"], exclude_test: ["**/*_test.go"], exclude_generated: []}
"""
OK = {"a.go": "package m\n\nfunc F(x int) int {\n\tif x > 0 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n",
      "a_test.go": "package m\n\nimport \"testing\"\n\nfunc TestF(t *testing.T) { _ = F(1) }\n",
      "go.mod": "module m\n\ngo 1.21\n"}


class FailClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gobranch-test-")
        cls.bin = os.path.join(cls.tmp, "gobranch")
        subprocess.run(["go", "build", "-o", cls.bin, "./cmd/gobranch"], cwd=os.path.join(HERE, "go"), check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def module(self, files):
        root = tempfile.mkdtemp(dir=self.tmp)
        for name, text in files.items():
            p = os.path.join(root, "m", name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(text)
        with open(os.path.join(root, "scope.yaml"), "w") as fh:
            fh.write(SCOPE)
        return root

    def measure(self, root):
        out = os.path.join(root, "run.json")
        r = subprocess.run([self.bin, "run", "-C", os.path.join(root, "m"), "-o", out], capture_output=True, text=True)
        return r, out

    def adapt(self, root, run):
        return subprocess.run([sys.executable, ADAPTER, "go", "--run", run, "--root", root, "--module-dir", "m",
                               "--scope", os.path.join(root, "scope.yaml"), "--commit", "0" * 40,
                               "--out", os.path.join(root, "report.json")], capture_output=True, text=True)

    def test_a_clause_entered_only_by_fallthrough_is_not_selected(self):
        f = dict(OK)
        f["a.go"] = ("package m\n\nfunc Fall(n int) int {\n\tr := 0\n\tswitch n {\n\tcase 1:\n\t\tr++\n\t\tfallthrough\n"
                     "\tcase 2:\n\t\tr += 2\n\tdefault:\n\t\tr = -1\n\t}\n\treturn r\n}\n")
        f["a_test.go"] = ("package m\n\nimport \"testing\"\n\nfunc TestF(t *testing.T) {\n\tif Fall(1) != 3 || Fall(9) != -1 {\n"
                          "\t\tt.Fatal()\n\t}\n}\n")
        root = self.module(f)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out) as fh:
            edges = json.load(fh)["packages"][0]["files"][0]
        got = {e["label"] + str(e["line"]): h for e, h in zip(edges["edges"], edges["hits"])}
        # case 1 and default were selected; case 2 only ran its body through the fallthrough
        self.assertEqual(sorted(got.values()), [0, 1, 1], got)
        a = self.adapt(root, out)
        self.assertEqual(a.returncode, 0, a.stderr)
        with open(os.path.join(root, "report.json")) as fh:
            rep = json.load(fh)
        self.assertEqual((rep["totals"]["total"], rep["totals"]["covered"]), (3, 2))
        # the real gate: 2 of 3 is below the threshold, where counting the fallthrough as a selection would give 3 of 3
        tools = os.path.join(root, "tools.yaml")
        with open(tools, "w") as fh:
            fh.write("rust: {selected: null}\ngo: {selected: {name: gobranch, version: \"0.1.0\", approved_deviations: []}}\n")
        g = subprocess.run([sys.executable, os.path.join(HERE, "..", "coverage_gate.py"), "check", "--root", root,
                            "--scope", os.path.join(root, "scope.yaml"), "--tools", tools, "--commit", "0" * 40, "--only", "go",
                            "--report", "go=" + os.path.join(root, "report.json")], capture_output=True, text=True)
        self.assertIn("BELOW_THRESHOLD", g.stdout)
        self.assertIn("2/3", g.stdout)

    def test_baseline_measures_and_adapts(self):
        root = self.module(OK)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.adapt(root, out)
        self.assertEqual(a.returncode, 0, a.stderr)
        with open(os.path.join(root, "report.json")) as fh:
            rep = json.load(fh)
        self.assertEqual((rep["totals"]["total"], rep["totals"]["covered"]), (2, 1))

    def refused_by_tool(self, files, needle):
        root = self.module(files)
        r, _ = self.measure(root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(needle, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "run.json")))

    def test_custom_testmain(self):
        f = dict(OK); f["a_test.go"] += "\nfunc TestMain(m *testing.M) { m.Run() }\n"
        self.refused_by_tool(f, "TestMain")

    def test_failing_test(self):
        f = dict(OK); f["a_test.go"] = "package m\n\nimport \"testing\"\n\nfunc TestF(t *testing.T) { t.Fatal() }\n"
        self.refused_by_tool(f, "go test failed")

    def test_old_go_directive(self):
        f = dict(OK); f["go.mod"] = "module m\n\ngo 1.17\n"
        self.refused_by_tool(f, "1.18")

    def test_goto(self):
        f = dict(OK); f["a.go"] += "\nfunc G() {\n\tgoto L\nL:\n}\n"
        self.refused_by_tool(f, "goto")

    def test_fallthrough_into_the_no_match_edge(self):
        f = dict(OK); f["a.go"] += "\nfunc S(x int) {\n\tswitch x {\n\tcase 1:\n\t\tfallthrough\n\t}\n}\n"
        self.refused_by_tool(f, "fallthrough")

    def test_embed(self):
        f = dict(OK); f["a.go"] += "\n//go:embed x.txt\nvar s string\n"; f["x.txt"] = "x"
        root = self.module(f)
        r, _ = self.measure(root)
        self.assertNotEqual(r.returncode, 0)

    def test_file_excluded_by_a_build_constraint_is_refused_by_the_adapter(self):
        f = dict(OK); f["b.go"] = "//go:build neverbuilt\n\npackage m\n\nfunc B(x int) int {\n\tif x > 1 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n"
        root = self.module(f)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out) as fh:
            self.assertEqual(json.load(fh)["ignored_files"], ["b.go"])
        a = self.adapt(root, out)
        self.assertNotEqual(a.returncode, 0)
        self.assertIn("build constraints", a.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "report.json")))

    def test_scope_file_nobody_measured_is_refused(self):
        f = dict(OK); f["_hidden/h.go"] = "package h\n\nfunc H(x int) int {\n\tif x > 0 {\n\t\treturn 1\n\t}\n\treturn 0\n}\n"
        root = self.module(f)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.adapt(root, out)
        self.assertNotEqual(a.returncode, 0)
        self.assertIn("not measured", a.stderr)

    def test_file_changed_after_the_measurement_is_refused(self):
        root = self.module(OK)
        r, out = self.measure(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(root, "m", "a.go"), "a") as fh:
            fh.write("\n// edited after the run\n")
        a = self.adapt(root, out)
        self.assertNotEqual(a.returncode, 0)
        self.assertIn("changed", a.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
