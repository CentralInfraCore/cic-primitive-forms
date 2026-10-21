"""Tests of .github/actions/cached-tool/install.sh with shims for cargo and go, and the seeded defects that show them able to fail.

The question: a restored cache entry is only used if the binary is the tool that was asked for and starts; anything else is
built again. The shims record what was asked of them, so that "built again" is observable.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", ".github", "actions", "cached-tool", "install.sh")
GO_SPEC = "example.com/org/tool/cmd/tool@v0.0.0-20260101000000-abcdefabcdef"
GOOD_GO = "#!/bin/bash\n# BUILDINFO\n# \tpath\texample.com/org/tool/cmd/tool\n# \tmod\texample.com/org/tool\tv0.0.0-20260101000000-abcdefabcdef\texample\nexit 1\n"

CARGO_SHIM = r"""#!/bin/bash
echo "cargo $*" >> "$SHIM_LOG"
shift   # +stable
case "$1" in
  install) printf '#!/bin/bash\n# GOODBIN\nexit 0\n' > "$BIN"; chmod +x "$BIN" ;;
  *) grep -q GOODBIN "$BIN" 2>/dev/null ;;     # `cargo <subcommand> --help` works only with the good binary
esac
"""
GO_SHIM = r"""#!/bin/bash
echo "go $*" >> "$SHIM_LOG"
case "$1" in
  install) printf '%s' "$GOOD_GO" > "$BIN"; chmod +x "$BIN" ;;
  version) grep -q '^# BUILDINFO' "$3" 2>/dev/null || exit 1; sed -n 's/^# //p' "$3" ;;
esac
"""


def write_exec(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class Run:
    def __init__(self, script):
        self.script = script
        self.tmp = tempfile.mkdtemp()
        self.shims = os.path.join(self.tmp, "shims")
        os.makedirs(self.shims)
        write_exec(os.path.join(self.shims, "cargo"), CARGO_SHIM)
        write_exec(os.path.join(self.shims, "go"), GO_SHIM)
        self.bin = os.path.join(self.tmp, "tool")
        self.log = os.path.join(self.tmp, "log")
        open(self.log, "w").close()

    def close(self):
        shutil.rmtree(self.tmp)

    def put(self, text):
        if text is not None:
            write_exec(self.bin, text)

    def run(self, kind, name, spec, hit):
        env = {"PATH": self.shims + os.pathsep + os.environ["PATH"], "KIND": kind, "NAME": name, "SPEC": spec, "BIN": self.bin,
               "HIT": hit, "SHIM_LOG": self.log, "GOOD_GO": GOOD_GO}
        done = subprocess.run(["bash", self.script], env=env, capture_output=True, text=True)
        with open(self.log, encoding="utf-8") as fh:
            calls = fh.read()
        return done, ("install" in calls)


class CachedToolTests(unittest.TestCase):
    script = SCRIPT

    def case(self, kind, name, spec, hit, binary):
        r = Run(self.script)
        self.addCleanup(r.close)
        r.put(binary)
        done, installed = r.run(kind, name, spec, hit)
        return done, installed, r

    def test_a_good_cached_cargo_tool_is_used(self):
        done, installed, _ = self.case("cargo", "cargo-foo", "cargo-foo@1.2.3", "true", "#!/bin/bash\n# GOODBIN\nexit 0\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(installed)
        self.assertIn("from the cache", done.stdout)

    def test_a_cached_cargo_tool_that_does_not_run_is_built_again(self):
        done, installed, r = self.case("cargo", "cargo-foo", "cargo-foo@1.2.3", "true", "#!/bin/bash\n# truncated\nexit 0\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(installed)
        self.assertIn("failed its check", done.stdout)
        with open(r.bin) as fh:
            self.assertIn("GOODBIN", fh.read())

    def test_a_miss_builds(self):
        done, installed, _ = self.case("cargo", "cargo-foo", "cargo-foo@1.2.3", "false", None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(installed)

    def test_a_hit_without_a_file_builds(self):
        done, installed, _ = self.case("cargo", "cargo-foo", "cargo-foo@1.2.3", "true", None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(installed)

    def test_a_good_cached_go_tool_is_used(self):
        done, installed, _ = self.case("go", "tool", GO_SPEC, "true", GOOD_GO)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(installed)
        self.assertIn("from the cache", done.stdout)

    def test_an_executable_file_that_is_not_the_go_tool_is_built_again(self):
        # the old check: -x only
        done, installed, r = self.case("go", "tool", GO_SPEC, "true", "#!/bin/bash\nexit 0\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(installed)
        with open(r.bin) as fh:
            self.assertIn("BUILDINFO", fh.read())

    def test_a_go_tool_of_another_version_is_built_again(self):
        other = GOOD_GO.replace("abcdefabcdef", "000000000000")
        done, installed, _ = self.case("go", "tool", GO_SPEC, "true", other)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(installed)

    def test_a_go_tool_of_another_package_is_built_again(self):
        other = GOOD_GO.replace("example.com/org/tool/cmd/tool", "example.com/org/tool/cmd/other")
        done, installed, _ = self.case("go", "tool", GO_SPEC, "true", other)
        self.assertTrue(installed)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_go_tool_of_another_module_with_the_same_version_is_built_again(self):
        other = GOOD_GO.replace("mod\texample.com/org/tool\t", "mod\texample.com/evil/tool\t")
        done, installed, _ = self.case("go", "tool", GO_SPEC, "true", other)
        self.assertTrue(installed)

    def test_a_go_tool_that_cannot_start_is_built_again(self):
        broken = GOOD_GO.replace("exit 1\n", "exit 127\n")
        done, installed, _ = self.case("go", "tool", GO_SPEC, "true", broken)
        self.assertTrue(installed)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_tool_that_prints_its_usage_and_exits_1_is_accepted(self):
        # go-mutesting does exactly that on --help; the identity check is the build information, not the exit status
        done, installed, _ = self.case("go", "tool", GO_SPEC, "true", GOOD_GO)
        self.assertFalse(installed)

    def test_a_build_that_does_not_pass_its_own_check_fails_the_step(self):
        r = Run(self.script)
        self.addCleanup(r.close)
        write_exec(os.path.join(r.shims, "go"), "#!/bin/bash\necho \"go $*\" >> \"$SHIM_LOG\"\ncase \"$1\" in install) printf '#!/bin/bash\\nexit 0\\n' > \"$BIN\"; chmod +x \"$BIN\" ;; version) exit 1 ;; esac\n")
        done, installed = r.run("go", "tool", GO_SPEC, "false")
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(installed)

    def test_an_unknown_kind_is_refused(self):
        done, _, _ = self.case("npm", "tool", "tool@1", "true", GOOD_GO)
        self.assertNotEqual(done.returncode, 0)


# (name, old, new): each must occur once in install.sh and make a test of CachedToolTests fail
DEFECTS = [
    ("a hit is trusted without a check", 'if [ "$HIT" = true ] && works; then', 'if [ "$HIT" = true ]; then'),
    ("only the executable bit is checked for a go tool", 'info=$(go version -m "$BIN" 2>/dev/null) || return 1', "return 0"),
    ("the package of the go tool is not compared", 'grep -Eq "^[[:space:]]+path[[:space:]]+${want_path}\\$" <<< "$info" || return 1', "true"),
    ("the module of the go tool is not compared", 'grep -Eq "^[[:space:]]+mod[[:space:]]+${want_mod}[[:space:]]+${SPEC#*@}([[:space:]]|\\$)" <<< "$info" || return 1', "true"),
    ("the version of the go tool is not compared", '${want_mod}[[:space:]]+${SPEC#*@}([[:space:]]|\\$)', '${want_mod}[[:space:]]+[^[:space:]]+([[:space:]]|\\$)'),
    ("a go tool that cannot start is accepted", '[ "$rc" -lt 126 ]', "true"),
    ("a cargo tool is checked by its file name, not as a subcommand", 'cargo +stable "${NAME#cargo-}" --help >/dev/null 2>&1', '"$BIN" --nonsense >/dev/null 2>&1 || true'),
    ("a bad build passes", 'works || { echo "$NAME: the binary just built does not pass its own check" >&2; exit 1; }', "true"),
    ("a miss does not build", 'rm -f "$BIN"\nif [ "$KIND" = cargo ]; then', 'rm -f "$BIN"\nif false; then'),
    ("an unknown kind is accepted", "    *) return 1 ;;", "    *) return 0 ;;"),
]


class SeededDefects(unittest.TestCase):
    def test_every_defect_is_caught(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        with tempfile.TemporaryDirectory() as tmp:
            for name, old, new in DEFECTS:
                self.assertEqual(src.count(old), 1, "stale defect %r: %r occurs %d times" % (name, old, src.count(old)))
                broken = os.path.join(tmp, "install.sh")
                with open(broken, "w", encoding="utf-8") as fh:
                    fh.write(src.replace(old, new))
                done = subprocess.run([sys.executable, "-m", "unittest", "test_cached_tool.CachedToolTests"], cwd=HERE, capture_output=True, text=True,
                                      env=dict(os.environ, CACHED_TOOL_SCRIPT=broken))
                self.assertNotEqual(done.returncode, 0, "NOT CAUGHT: %s" % name)


if os.environ.get("CACHED_TOOL_SCRIPT"):
    CachedToolTests.script = os.environ["CACHED_TOOL_SCRIPT"]

if __name__ == "__main__":
    unittest.main()
