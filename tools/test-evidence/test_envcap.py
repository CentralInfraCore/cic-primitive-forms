"""Probes of the environment capture and its merge: a label is never a version, a gap or a conflict never becomes a record."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import envcap  # noqa: E402

A = "a" * 40
RUNNER = {"ImageOS": "ubuntu24", "ImageVersion": "20261001.1", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64"}


def fake(outputs, code=0):
    """A `run` that answers each command by its text; a command not listed does not exist."""
    def run(argv, **kw):
        key = " ".join(argv)
        if key not in outputs:
            raise FileNotFoundError(argv[0])
        out = outputs[key]
        return subprocess.CompletedProcess(argv, code, out.encode(), b"")
    return run


GOOD = {
    "rustc -vV": "rustc 1.99.0 (abc 2026-10-01)\nbinary: rustc\nhost: x86_64-unknown-linux-gnu\nrelease: 1.99.0\nLLVM version: 22.1.0\n",
    "cargo -V": "cargo 1.99.0 (def 2026-10-01)\n",
    "go version": "go version go1.27.2 linux/amd64\n",
    "cargo llvm-cov --version": "cargo-llvm-cov 0.9.1\n",
    "cargo mutants --version": "cargo-mutants 27.1.0\n",
    "cargo fuzz --version": "cargo-fuzz 0.13.2\n",
    "which go-mutesting": "/home/runner/go/bin/go-mutesting\n",
    "go version -m /home/runner/go/bin/go-mutesting": "/x: go1.27.2\n\tpath\tgithub.com/avito-tech/go-mutesting/cmd/go-mutesting\n\tmod\tgithub.com/avito-tech/go-mutesting\tv0.0.0-20261002081621-9ce5f731e321\t\n",
}


class CaptureTests(unittest.TestCase):
    def test_every_field(self):
        got = envcap.capture(envcap.FIELDS, run=fake(GOOD), environ=RUNNER, py="3.12.3")
        self.assertEqual(got, {
            "rustc": "1.99.0", "cargo": "1.99.0", "go": "go1.27.2", "python": "3.12.3",
            "runner": {"image_os": "ubuntu24", "image_version": "20261001.1", "os": "Linux", "arch": "X64"},
            "tools": {"cargo-llvm-cov": "0.9.1", "cargo-mutants": "27.1.0", "cargo-fuzz": "0.13.2", "go-mutesting": "0.0.0-20261002081621-9ce5f731e321"}})

    def test_the_toolchain_the_job_runs_is_the_one_asked(self):
        out = dict(GOOD, **{"rustc +stable -vV": "release: 1.100.0\n", "cargo +stable -V": "cargo 1.100.0 (x)\n",
                            "cargo +stable llvm-cov --version": "cargo-llvm-cov 0.9.1\n", "cargo +stable mutants --version": "cargo-mutants 27.1.0\n",
                            "cargo +stable fuzz --version": "cargo-fuzz 0.13.2\n"})
        got = envcap.capture(["rustc", "cargo", "tool:cargo-llvm-cov", "tool:cargo-mutants", "tool:cargo-fuzz"], run=fake(out), toolchain="stable")
        self.assertEqual((got["rustc"], got["cargo"]), ("1.100.0", "1.100.0"))
        self.assertEqual(sorted(got["tools"]), ["cargo-fuzz", "cargo-llvm-cov", "cargo-mutants"])
        # and not the default one, which a runner may have at another version
        self.assertEqual(envcap.capture(["rustc"], run=fake(out))["rustc"], "1.99.0")
        with self.assertRaises(envcap.EnvError):
            envcap.capture(["rustc"], run=fake(GOOD), toolchain="stable")  # nothing answers to `rustc +stable`

    def test_only_what_was_asked(self):
        self.assertEqual(envcap.capture(["rustc"], run=fake(GOOD)), {"rustc": "1.99.0"})
        self.assertEqual(envcap.capture(["tool:cargo-fuzz"], run=fake(GOOD)), {"tools": {"cargo-fuzz": "0.13.2"}})

    def test_the_release_of_a_nightly_is_what_it_is(self):
        out = dict(GOOD, **{"rustc -vV": "rustc 1.101.0-nightly (abc 2026-10-17)\nrelease: 1.101.0-nightly\n"})
        self.assertEqual(envcap.capture(["rustc"], run=fake(out)), {"rustc": "1.101.0-nightly"})

    def test_what_cannot_be_resolved_is_an_error_not_a_guess(self):
        for field, broken in (("rustc", {"rustc -vV": "no release line here\n"}), ("rustc", {"rustc -vV": "release: stable\n"}),
                              ("cargo", {"cargo -V": "cargo\n"}), ("go", {"go version": "go version devel\n"}),
                              ("tool:cargo-mutants", {"cargo mutants --version": "cargo-mutants\n"}),
                              ("tool:go-mutesting", {"go version -m /home/runner/go/bin/go-mutesting": "no module line\n"})):
            with self.subTest(field, broken=broken):
                out = dict(GOOD, **broken)
                with self.assertRaises(envcap.EnvError) as cm:
                    envcap.capture([field], run=fake(out))
                self.assertEqual(cm.exception.kind, "UNRESOLVED")
        for field in ("rustc", "tool:cargo-fuzz", "tool:go-mutesting"):
            with self.subTest("missing", field=field), self.assertRaises(envcap.EnvError):
                envcap.capture([field], run=fake({}))

    def test_a_tool_that_fails_is_unresolved(self):
        with self.assertRaises(envcap.EnvError):
            envcap.capture(["rustc"], run=fake(GOOD, code=1))

    def test_the_runner_has_to_say_all_of_it(self):
        for gone in RUNNER:
            env = {k: v for k, v in RUNNER.items() if k != gone}
            with self.subTest(gone), self.assertRaises(envcap.EnvError):
                envcap.capture(["runner"], environ=env)

    def test_an_unknown_field(self):
        with self.assertRaises(envcap.EnvError) as cm:
            envcap.capture(["rust"], run=fake(GOOD))
        self.assertEqual(cm.exception.kind, "UNKNOWN_FIELD")


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, ".github", "workflows"))
        with open(os.path.join(self.tmp.name, ".github", "workflows", "w.yml"), "w") as fh:
            fh.write("      - uses: actions/checkout@%s # v4\n" % A)
        full = envcap.capture(envcap.FIELDS, run=fake(GOOD), environ=RUNNER, py="3.12.3")
        self.parts = [{k: v for k, v in full.items() if k in ("rustc", "cargo", "python", "runner")},
                      {"tools": {"cargo-llvm-cov": "0.9.1", "cargo-mutants": "27.1.0"}, "runner": full["runner"]},
                      {"go": "go1.27.2", "tools": {"go-mutesting": full["tools"]["go-mutesting"], "cargo-fuzz": "0.13.2"}, "python": "3.12.3"}]

    def test_the_parts_make_the_environment(self):
        env = envcap.merge(self.parts, self.tmp.name)
        self.assertEqual(env["actions"], {"actions/checkout": A})
        self.assertEqual(env["tools"]["cargo-mutants"], "27.1.0")
        self.assertEqual(env["runner"]["image_version"], "20261001.1")

    def test_a_conflict_is_not_resolved(self):
        for label, edit in (("the runner image", lambda p: p[1]["runner"].update(image_version="20261002.1")),
                            ("python", lambda p: p[2].update(python="3.12.4")),
                            ("a tool", lambda p: p[0].setdefault("tools", {}).update({"cargo-mutants": "27.2.0"}))):
            with self.subTest(label):
                parts = [dict(p) for p in self.parts]
                parts[1] = dict(parts[1], runner=dict(parts[1]["runner"]))
                edit(parts)
                with self.assertRaises(envcap.EnvError) as cm:
                    envcap.merge(parts, self.tmp.name)
                self.assertEqual(cm.exception.kind, "CONFLICT")

    def test_a_gap_is_not_filled(self):
        for drop in range(len(self.parts)):
            with self.subTest(drop=drop), self.assertRaises(envcap.EnvError) as cm:
                envcap.merge([p for i, p in enumerate(self.parts) if i != drop], self.tmp.name)
            self.assertEqual(cm.exception.kind, "INCOMPLETE")

    def test_an_unpinned_action_stops_it(self):
        with open(os.path.join(self.tmp.name, ".github", "workflows", "w.yml"), "a") as fh:
            fh.write("      - uses: actions/setup-go@v5\n")
        with self.assertRaises(envcap.EnvError) as cm:
            envcap.merge(self.parts, self.tmp.name)
        self.assertEqual(cm.exception.kind, "UNPINNED")

    def test_no_actions_at_all_stops_it(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(envcap.EnvError) as cm:
                envcap.merge(self.parts, empty)
            self.assertEqual(cm.exception.kind, "UNPINNED")


if __name__ == "__main__":
    unittest.main()
