#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The three go-mutesting executors give a mutant the verdict its test run earns (#94): a mutant that does not compile is
`uncompilable`, not `killed` (`go test` exits 1 for a build failure as for a failing test), one that only vet catches is `killed`
as before, a failing test is `killed`, an unchanged behaviour `survived`. Runs `go test` on a throwaway module; skipped without Go
(the `go` job of p1 runs it)."""
import os
import shutil
import subprocess
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
SRC = "package x\n\nimport \"fmt\"\n\nvar _ = fmt.Sprint\n\nfunc A() int {\n\treturn 1\n}\n"
TEST = "package x\n\nimport \"testing\"\n\nfunc TestA(t *testing.T) {\n\tif A() != 1 {\n\t\tt.Fatal(A())\n\t}\n}\n"
MUTANTS = {
    "killed": SRC.replace("return 1", "return 2"),
    "survived": SRC.replace("return 1", "return 1 + 0"),
    "uncompilable": SRC.replace("return 1", 'return "one"'),
    "vet": SRC.replace("return 1", 'fmt.Printf("%d", "one")\n\treturn 1'),   # builds; only vet (part of go test) fails
}
EXIT = {"killed": 0, "survived": 1, "uncompilable": 2}


@unittest.skipIf(shutil.which("go") is None, "no Go toolchain")
class Executors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mod = os.path.join(self.tmp.name, "go")
        os.makedirs(self.mod)
        for name, text in (("go.mod", "module x\n\ngo 1.25\n"), ("a.go", SRC), ("a_test.go", TEST)):
            with open(os.path.join(self.mod, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.ledger = os.path.join(self.tmp.name, "ledger")
        with open(os.path.join(self.tmp.name, "lines.json"), "w", encoding="utf-8") as fh:
            fh.write('{"go/a.go": [[1, 20]]}')

    def tearDown(self):
        self.tmp.cleanup()

    def run_exec(self, script, mutant, extra):
        changed = os.path.join(self.tmp.name, "a.go.mutant")
        with open(changed, "w", encoding="utf-8") as fh:
            fh.write(MUTANTS[mutant])
        open(self.ledger, "w").close()
        env = dict(os.environ, MUTATE_CHANGED=changed, MUTATE_ORIGINAL=os.path.join(self.mod, "a.go"), MUTATE_PACKAGE="x",
                   GOFLAGS="-count=1", **extra)
        r = subprocess.run(["bash", os.path.join(TOOLS, script)], cwd=self.mod, env=env, capture_output=True, text=True)
        with open(os.path.join(self.mod, "a.go"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), SRC, "the original is put back")
        with open(self.ledger, encoding="utf-8") as fh:
            rows = [line.split("\t") for line in fh.read().splitlines()]
        return r.returncode, [row[3] for row in rows]

    def check(self, script, extra, ledger):
        for mutant, verdict in (("killed", "killed"), ("survived", "survived"), ("uncompilable", "uncompilable"), ("vet", "killed")):
            with self.subTest(script=script, mutant=mutant):
                code, verdicts = self.run_exec(script, mutant, extra)
                self.assertEqual(code, EXIT[verdict])
                self.assertEqual(verdicts, [verdict] if ledger else [])

    def test_the_shard_executor(self):
        self.check("mutation_shard_exec.sh", {"CIC_MUTATION_SHARD": "0/1", "CIC_MUTATION_LEDGER": self.ledger}, True)

    def test_the_diff_executor(self):
        self.check("mutation_diff_exec.sh", {"CIC_MUTATION_SHARD": "0/1", "CIC_MUTATION_LEDGER": self.ledger,
                                             "CIC_DIFF_LINES": os.path.join(self.tmp.name, "lines.json"), "CIC_REPO_ROOT": self.tmp.name}, True)

    def test_the_reduced_executor(self):
        self.check("mutation_reduced_exec.sh", {"CIC_MUTATION_RUN": "^TestA$"}, False)


if __name__ == "__main__":
    unittest.main()
