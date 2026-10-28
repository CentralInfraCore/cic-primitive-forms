#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The scope of the diff gate of a pull request (docs/DELTA-DIFF-GATE.md).

    diff_gate.py scope --base REV --head REV --out DIR [--root DIR] [--measure]    classify the change and write the files the mutation jobs read
    diff_gate.py in-diff --lines FILE --path REL --original F --changed F    exit 0 if the mutant changes a line of the diff, 1 if not, 2 if it could not tell

`scope` compares two commits (for a pull request: the first parent of the merge candidate and the merge candidate itself) and decides
whether the mutation run may be limited to the code the change touches (`mode` = `diff`) or has to be the whole one (`mode` = `full`).
It is conservative: whatever it cannot show to be safe is `full`, with the reason.

  * product code (rust/src, go/*.go without the tests): its added and changed lines are the diff; the neighbours of a pure deletion
    count as changed. Only the mutants on those lines are run.
  * a test or a test data file that is only ADDED to (a new file, or new top-level tests, or lines appended to a data file) can only add
    kills: nothing else has to run.
  * a REMOVED or MODIFIED line inside an existing TEST FUNCTION (#92) can only weaken the protection of the code that test executed: the
    test is named (Go `func TestX`/`FuzzX`, Rust `#[test] fn x`, also in a `#[cfg(test)]` module), and with `--measure` the old version of
    every such test runs on the base with line coverage; the product lines it executed, carried over to the head, join the diff. A mutant
    outside them was not killed by a changed test before, so its status cannot have changed. Without `--measure`, or when a test cannot
    be named or measured, it is `full`.
  * a removed or modified line outside a test function (a helper, a fixture, a shared table), a test data line, a deleted test file, or
    anything that changes how the gate measures (Cargo.toml/lock, go.mod/sum, the mutation and gate tools, the actions, the workflow p1)
    can weaken what the unchanged code is protected by: `full`.
  * documentation and the other tools do not touch a mutant.

Files written to --out: scope.json (mode, reasons, counts), rust.patch (a unified diff of the changed lines of rust/src, for
`cargo mutants --in-diff`), diff-lines.json (the changed line ranges of the product Go files, for tools/mutation_diff_exec.sh).
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

BUILD_CONFIG = (
    "rust/Cargo.toml", "rust/Cargo.lock", "go/go.mod", "go/go.sum", "tools/quality_gate.py", "tools/shard_plan.py",
    "tools/mutation-costs.json", "tools/diff_gate.py",
    # the tools that decide what runs in diff mode (#94): a change of them, measured only by the diff mode it changes, would prove nothing
    "tools/shard_count.py", "tools/shard_budget.json", "tools/fuzz_matrix.py", "tools/seeded_defects.py",
)
# The workflow is not a reason for `full` on the pull request: a FULL is started in the background when it is merged (nightly.yml, trigger push on this
# path), and by hand at any time (workflow_dispatch, force). The pull request still runs the gate self-tests.
GATE_WORKFLOW = ".github/workflows/p1.yml"
BUILD_CONFIG_PREFIX = ("rust/.cargo/", ".github/actions/", "tools/mutation_", "coverage/")
DATA_PREFIX = ("testdata/", "corpus/")
STARTS_GO = ("func ", "type ", "var ", "const ", "//", "import ", "package ")
# A Go test file with one of these changes how EVERY test of its package runs (TestMain can end the process before a single test, init can set the world they
# run in), so it is not a mere addition of tests that can only add kills.
RUNS_ALL_TESTS = re.compile(r"^func\s+(TestMain|init)\s*\(")
STARTS_RUST = ("#[test]", "#[cfg", "#[derive", "#[allow", "fn ", "pub fn ", "use ", "//", "struct ", "impl ", "proptest!", "mod ", "const ", "static ", "type ")


def kind(path):
    """product-rust, product-go, test-rust, test-go, data, build, fuzz or other."""
    if path in BUILD_CONFIG or path.startswith(BUILD_CONFIG_PREFIX):
        return "build"
    if path.startswith(DATA_PREFIX):
        return "data"
    if path.startswith("rust/tests/"):
        return "test-rust"
    if path.startswith("rust/fuzz/"):
        return "fuzz"
    if path.startswith("rust/") and path.endswith(".rs"):
        return "product-rust"
    if path.startswith("go/") and path.endswith("_test.go"):
        return "test-go"
    if path.startswith("go/") and path.endswith(".go"):
        return "product-go"
    return "other"


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, check=True).stdout


def parse_diff(text):
    """{path: {"status": new|deleted|modified, "hunks": [(old_start, old_len, new_start, new_len, removed_lines, added_lines)]}}"""
    files, cur, hunk = {}, None, None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            cur = {"status": "modified", "hunks": []}
            files[m.group(2)] = cur
            hunk = None
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur["status"] = "new"
        elif line.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif line.startswith("@@"):
            m = HUNK.match(line)
            a, b, c, d = m.group(1), m.group(2), m.group(3), m.group(4)
            hunk = [int(a), 1 if b is None else int(b), int(c), 1 if d is None else int(d), [], []]
            cur["hunks"].append(hunk)
        elif hunk is not None and line.startswith("-") and not line.startswith("---"):
            hunk[4].append(line[1:])
        elif hunk is not None and line.startswith("+") and not line.startswith("+++"):
            hunk[5].append(line[1:])
    return files


def balanced(lines):
    depth = 0
    for ln in lines:
        code = re.sub(r'"(?:\\.|[^"\\])*"|`[^`]*`|\'(?:\\.|[^\'\\])\'', "", ln.split("//")[0])
        depth += code.count("{") - code.count("}") + code.count("(") - code.count(")") + code.count("[") - code.count("]")
    return depth == 0


def top_level_addition(path, head_lines, hunk, starts, indented=False):
    """True when the added lines of a pure-addition hunk are whole new items between existing ones, not lines inside an existing body."""
    _, _, new_start, new_len, removed, added = hunk
    if removed or new_len == 0 or not added:
        return False
    before = head_lines[new_start - 2] if new_start >= 2 else ""
    after = head_lines[new_start - 1 + new_len] if new_start - 1 + new_len < len(head_lines) else ""
    first = next((x for x in added if x.strip()), "")
    stripped = first.strip()
    if not stripped.startswith(starts):
        return False
    if not indented and first[:1] in (" ", "\t"):
        return False
    b = before.strip()
    if b and not (b.startswith("}") or b.startswith(")") or b.startswith("//") or b.startswith("#[") or b.startswith("use ") or b.startswith("import")
                  or b.startswith("package") or (indented and b.endswith("{") and b.startswith("mod ")) or (indented and b.endswith(";"))):
        return False
    a = after.strip()
    if a and not (not indented and after[:1] not in (" ", "\t") or indented and (a.startswith("}") or a.startswith("#[") or a.startswith("fn ") or a.startswith("//") or a.startswith("proptest!"))):
        return False
    return balanced(added)


GO_IMPORT_LINE = re.compile(r'^\s*(?:[\w.]+\s+)?"[^"]+"\s*$')       # a Go import: a quoted path, no trailing comma, as in `import (...)`
IMPORT_LIST_LINE = re.compile(r"^\s*(?:use\s+)?[\w:,\s{}]*;?\s*$")   # the inside of a Rust `use ...::{a, b}` list


def only_imports_grow(kind_, removed, added):
    """An edit of an import list that only adds names: it cannot weaken a test."""
    if kind_ == "test-go":
        return not removed and bool(added) and all(GO_IMPORT_LINE.match(x) for x in added)
    if not all(IMPORT_LIST_LINE.match(x) for x in removed + added):
        return False
    tok = lambda lines: re.findall(r"\w+", " ".join(lines))
    from collections import Counter
    return not (Counter(tok(removed)) - Counter(tok(added)))


def read_head(root, head, path):
    try:
        return git(root, "show", "%s:%s" % (head, path)).splitlines()
    except subprocess.CalledProcessError:
        return []


# ---- narrowing a changed test to the code it executed (#92) -----------------------------------------------------------------------

TEST_GO = re.compile(r"^func ((?:Test|Fuzz)\w*)\(")
FN_RUST = re.compile(r"^(\s*)(?:pub(?:\(crate\))?\s+)?fn\s+(\w+)")
PENDING = "the coverage of the changed tests is not measured (diff_gate.py scope --measure): without it a changed test counts as a weakening"


def enclosing_test(lang, lines, n):
    """The test function that holds line n (1-based) of a test file as the base has it, or None: a helper, a fixture, a shared table, a
    line between tests. Go: a top-level `func TestX`/`FuzzX` up to its closing `}`. Rust: a `fn` with `#[test]` among the attributes
    right above it, up to the `}` at its own indentation (top-level in rust/tests, indented in a test module or a proptest! block)."""
    if not 1 <= n <= len(lines):
        return None
    for i in range(n - 1, -1, -1):
        if lang == "go":
            if not lines[i].startswith("func "):
                continue
            m = TEST_GO.match(lines[i])
            close = "}"
        else:
            m = FN_RUST.match(lines[i])
            if not m:
                continue
            j, attrs = i - 1, []
            while j >= 0 and lines[j].strip().startswith(("#[", "//")):
                attrs.append(lines[j].strip())
                j -= 1
            if "#[test]" not in attrs:
                return None
            close = m.group(1) + "}"
        if not m:
            return None
        end = next((k for k in range(i + 1, len(lines)) if lines[k] == close), None)
        return m.group(m.lastindex) if end is not None and n - 1 <= end else None
    return None


def hunk_tests(lang, base_lines, hunk):
    """The tests a hunk of a test file changes, by its lines on the base; None when any of them is outside a test."""
    old_start, old_len = hunk[0], hunk[1]
    lines = range(old_start, old_start + old_len) if old_len else (old_start, old_start + 1)   # an insertion lands between two lines
    names = {enclosing_test(lang, base_lines, n) for n in lines}
    return None if None in names else names


def rust_target(path):
    """The cargo test target of a Rust file: `lib` for the crate's own test modules, the file stem for rust/tests/<stem>.rs."""
    return "lib" if path.startswith("rust/src/") else os.path.splitext(os.path.basename(path))[0]


class MeasureError(Exception):
    pass


def parse_go_profile(text, module):
    """go test -coverprofile: {repo path: {executed line}} of the product files (not the tests)."""
    out = {}
    for line in text.splitlines()[1:]:
        m = re.match(r"^(.+):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$", line)
        if not m:
            raise MeasureError("an unreadable Go coverage line %r" % line)
        path, a, b, count = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if not path.startswith(module + "/") or path.endswith("_test.go") or count == 0:
            continue
        out.setdefault("go/" + path[len(module) + 1:], set()).update(range(a, b + 1))
    return out


def parse_lcov(text, root):
    """cargo llvm-cov --lcov: {repo path: {executed line}} of the files of rust/src."""
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("SF:"):
            rel = os.path.relpath(line[3:], root)
            cur = rel if rel.startswith("rust/src/") else None
        elif line.startswith("DA:") and cur is not None:
            n, count = line[3:].split(",")[:2]
            if int(count) > 0:
                out.setdefault(cur, set()).add(int(n))
    return out


def carry_lines(base_lines, head_lines, lines):
    """The head line numbers of base lines that the change left as they were; a line it changed is in the diff already."""
    out = set()
    for tag, i1, i2, j1, _ in difflib.SequenceMatcher(None, base_lines, head_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            out.update(j1 + (n - 1 - i1) + 1 for n in lines if i1 <= n - 1 < i2)
    return out


def merge_ranges(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def to_ranges(lines):
    return merge_ranges([[n, n] for n in lines])


def run_coverage(lang, wt, targets):
    """Runs the named tests of the base worktree wt with line coverage; {repo path: {line}}. Every named test must have run."""
    env = dict(os.environ, CIC_FORMS_ROOT=wt)
    if lang == "go":
        names = sorted(targets["go"])
        prof = os.path.join(wt, "go-changed-tests.cover")
        r = subprocess.run(["go", "test", "-count=1", "-v", "-run", "^(%s)$" % "|".join(names), "-coverprofile=" + prof, "."],
                           cwd=os.path.join(wt, "go"), capture_output=True, text=True, env=env)
        ran = set(re.findall(r"^=== RUN\s+(\w+)$", r.stdout, re.M))
        if r.returncode != 0 or set(names) - ran:
            raise MeasureError("the changed Go tests did not all run and pass on the base (missing: %s)\n%s" % (
                sorted(set(names) - ran), (r.stdout + r.stderr)[-2000:]))
        module = next(l.split()[1] for l in open(os.path.join(wt, "go", "go.mod")) if l.startswith("module "))
        with open(prof, encoding="utf-8") as fh:
            return parse_go_profile(fh.read(), module)
    out = {}
    cargo = os.environ.get("DIFF_GATE_CARGO", "cargo +" + os.environ.get("RUST_TOOLCHAIN", "stable")).split()
    for target, names in sorted(targets.items()):
        lcov = os.path.join(wt, "rust-changed-tests-%s.lcov" % target)
        sel = ["--lib"] if target == "lib" else ["--test", target]
        r = subprocess.run(cargo + ["llvm-cov", "--locked", "--lcov", "--output-path", lcov] + sel + ["--"] + sorted(names)
                           + ([] if target == "lib" else ["--exact"]), cwd=os.path.join(wt, "rust"), capture_output=True, text=True, env=env)
        ran = set(re.findall(r"^test (?:\S+::)?(\w+) \.\.\. ok$", r.stdout, re.M))
        if r.returncode != 0 or set(names) - ran:
            raise MeasureError("the changed Rust tests of %s did not all run and pass on the base (missing: %s)\n%s" % (
                target, sorted(set(names) - ran), (r.stdout + r.stderr)[-2000:]))
        with open(lcov, encoding="utf-8") as fh:
            for path, lines in parse_lcov(fh.read(), wt).items():
                out.setdefault(path, set()).update(lines)
    return out


def measure(root, base, head, widen, product_rust, product_go, run=run_coverage):
    """Adds to the diff the head lines the base versions of the changed tests executed; the notes say how much."""
    notes = []
    wt = tempfile.mkdtemp(prefix="diff-gate-base-")
    try:
        git(root, "worktree", "add", "--detach", wt, base)
        for lang, product in (("go", product_go), ("rust", product_rust)):
            if not widen.get(lang):
                continue
            covered = run(lang, wt, widen[lang])
            if not covered:
                raise MeasureError("the changed %s tests executed no product line on the base" % lang)
            added = 0
            for path, lines in sorted(covered.items()):
                head_lines = read_head(root, head, path)
                carried = carry_lines(read_head(root, base, path), head_lines, lines)
                if not carried:
                    continue
                ranges, _ = product.get(path, ([], head_lines))
                product[path] = (merge_ranges(ranges + to_ranges(carried)), head_lines)
                added += len(carried)
            notes.append("%s: the changed tests %s executed %d product lines on the base; they join the diff" % (
                lang, ", ".join(sorted(n for ns in widen[lang].values() for n in ns)), added))
    finally:
        subprocess.run(["git", "-C", root, "worktree", "remove", "--force", wt], capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
    return notes


def classify(root, base, head):
    diff = parse_diff(git(root, "diff", "--unified=0", "--no-renames", base, head))
    reasons, product_rust, product_go, notes = [], {}, {}, []
    widen = {}   # {"go": {"go": {test}}, "rust": {target: {test}}}: the changed tests whose coverage joins the diff (#92)

    def full(why):
        reasons.append(why)

    def narrow(path, k, h, why):
        """A changed hunk of a test: the tests it changes are measured (`--measure`), or, outside a test, it is `full` as before."""
        lang = "go" if k == "test-go" else "rust"
        names = hunk_tests(lang, read_head(root, base, path), h)
        if names is None:
            full(why)
            return
        target = "go" if lang == "go" else rust_target(path)
        widen.setdefault(lang, {}).setdefault(target, set()).update(names)

    for path, f in sorted(diff.items()):
        if path == GATE_WORKFLOW:
            notes.append("%s changes the gate itself: a FULL runs in the background after the merge (nightly.yml, on push to main) and can be started by hand" % path)
            continue
        k = kind(path)
        if k in ("other", "fuzz"):
            continue
        if k == "build":
            full("%s changes how the gate measures" % path)
            continue
        if k in ("test-go", "test-rust", "data"):
            if f["status"] == "deleted":
                full("%s (a test or its data) is deleted" % path)
                continue
            if k == "test-go" and any(RUNS_ALL_TESTS.match(line) for h in f["hunks"] for line in h[5]):
                full("%s adds TestMain or init: it changes how every test of the package runs" % path)
                continue
            if f["status"] == "new":
                notes.append("%s: a new file, an addition" % path)
                continue
            head_lines = read_head(root, head, path)
            for h in f["hunks"]:
                where = "%s:%d" % (path, h[2])
                if k != "data" and only_imports_grow(k, h[4], h[5]):
                    continue
                if k == "data":
                    if h[4]:
                        full("%s removes or modifies a line of an existing test or test data" % where)
                    elif h[2] - 1 + h[3] < len(head_lines):
                        full("%s adds lines inside an existing data file (only appending is an addition)" % where)
                elif h[4]:
                    narrow(path, k, h, "%s removes or modifies a line outside a test function (a helper, a fixture, a shared table)" % where)
                elif not top_level_addition(path, head_lines, h, STARTS_GO if k == "test-go" else STARTS_RUST):
                    narrow(path, k, h, "%s adds lines that are not whole new top-level tests and not inside a test function" % where)
            continue
        # product code
        head_lines = read_head(root, head, path)
        test_from = None
        if k == "product-rust":
            for i, ln in enumerate(head_lines):
                if ln.strip() == "#[cfg(test)]":
                    test_from = i + 1
                    break
        ranges = []
        for h in f["hunks"]:
            old_start, old_len, new_start, new_len, removed, added = h
            in_tests = test_from is not None and new_start >= test_from
            if in_tests:
                if removed or not top_level_addition(path, head_lines, h, STARTS_RUST, indented=True):
                    narrow(path, "test-rust", h, "%s:%d changes the test module of the source file outside a test function" % (path, new_start))
                continue
            if new_len == 0:   # a pure deletion: the neighbours of the cut are the changed code
                ranges.append([max(new_start, 1), min(new_start + 1, max(len(head_lines), 1))])
            else:
                ranges.append([new_start, new_start + new_len - 1])
        if ranges:
            (product_rust if k == "product-rust" else product_go)[path] = (ranges, head_lines)
    if widen:
        reasons.append(PENDING)
    widen = {lang: {target: sorted(names) for target, names in targets.items()} for lang, targets in widen.items()}
    return {"mode": "full" if reasons else "diff", "reasons": reasons, "notes": notes, "widen": widen}, product_rust, product_go


def narrowed(verdict, run_measure):
    """The verdict after the measurement: the pending reason is dropped when it worked, and replaced by why when it did not (fail closed)."""
    reasons = [r for r in verdict["reasons"] if r != PENDING]
    notes = list(verdict["notes"])
    try:
        notes += run_measure()
    except (MeasureError, subprocess.CalledProcessError, OSError) as e:
        reasons.append("the coverage of the changed tests could not be measured: %s" % str(e).splitlines()[0])
    return dict(verdict, reasons=reasons, notes=notes, mode="full" if reasons else "diff")


def write_outputs(out, verdict, product_rust, product_go):
    os.makedirs(out, exist_ok=True)
    patch = []
    for path, (ranges, lines) in sorted(product_rust.items()):
        path = path[len("rust/"):]   # cargo-mutants matches the paths of the diff against the crate root (rust/), not the repository root
        patch.append("diff --git a/%s b/%s\n--- a/%s\n+++ b/%s" % (path, path, path, path))
        for a, b in ranges:
            body = lines[a - 1:b]
            patch.append("@@ -%d,0 +%d,%d @@" % (a - 1, a, len(body)))
            patch += ["+" + x for x in body]
    with open(os.path.join(out, "rust.patch"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(patch) + ("\n" if patch else ""))
    with open(os.path.join(out, "diff-lines.json"), "w", encoding="utf-8") as fh:
        json.dump({p: r for p, (r, _) in sorted(product_go.items())}, fh, indent=1, sort_keys=True)
    verdict = dict(verdict, rust_files=sorted(product_rust), go_files=sorted(product_go),
                   rust_ranges=sum(len(r) for r, _ in product_rust.values()), go_ranges=sum(len(r) for r, _ in product_go.values()))
    with open(os.path.join(out, "scope.json"), "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=1, sort_keys=True)
    return verdict


def changed_original_lines(original, changed):
    """The line numbers (1-based, in the original) that a mutant changes."""
    with open(original, encoding="utf-8") as fh:
        a = fh.read().splitlines()
    with open(changed, encoding="utf-8") as fh:
        b = fh.read().splitlines()
    out = set()
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        out.update(range(i1 + 1, max(i2, i1 + 1) + 1))   # a pure insertion touches the line it lands on
    return out


def in_diff(lines_file, rel, original, changed):
    with open(lines_file, encoding="utf-8") as fh:
        ranges = json.load(fh).get(rel, [])
    touched = changed_original_lines(original, changed)
    return any(a <= n <= b for n in touched for a, b in ranges)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scope")
    s.add_argument("--base", required=True)
    s.add_argument("--head", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--root", default=".")
    s.add_argument("--measure", action="store_true", help="run the base versions of the changed tests with coverage and narrow to it")
    i = sub.add_parser("in-diff")
    i.add_argument("--lines", required=True)
    i.add_argument("--path", required=True)
    i.add_argument("--original", required=True)
    i.add_argument("--changed", required=True)
    a = ap.parse_args()
    if a.cmd == "in-diff":
        # three answers, never two: 0 the mutant changes a line of the diff, 1 it does not, 2 the question could not be answered (a missing or unreadable file, a
        # bug). The caller must not take 2 for 1: a mutant that could not be placed is not a mutant outside the diff.
        try:
            return 0 if in_diff(a.lines, a.path, a.original, a.changed) else 1
        except Exception as e:  # noqa: BLE001 - whatever went wrong, the answer is "could not tell"
            print("in-diff could not tell: %s" % e, file=sys.stderr)
            return 2
    verdict, pr, pg = classify(a.root, a.base, a.head)
    if a.measure and verdict["widen"]:
        verdict = narrowed(verdict, lambda: measure(a.root, a.base, a.head, verdict["widen"], pr, pg))
    verdict = write_outputs(a.out, verdict, pr, pg)
    print("mode=%s  rust ranges=%d  go ranges=%d" % (verdict["mode"], verdict["rust_ranges"], verdict["go_ranges"]))
    for r in verdict["reasons"]:
        print("  full because: " + r)
    for n in verdict["notes"]:
        print("  " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
