#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The scope of the diff gate of a pull request (docs/DELTA-DIFF-GATE.md).

    diff_gate.py scope --base REV --head REV --out DIR [--root DIR]    classify the change and write the files the mutation jobs read
    diff_gate.py in-diff --lines FILE --path REL --original F --changed F    exit 0 if the mutant changes a line of the diff, 1 if not

`scope` compares two commits (for a pull request: the first parent of the merge candidate and the merge candidate itself) and decides
whether the mutation run may be limited to the code the change touches (`mode` = `diff`) or has to be the whole one (`mode` = `full`).
It is conservative: whatever it cannot show to be safe is `full`, with the reason.

  * product code (rust/src, go/*.go without the tests): its added and changed lines are the diff; the neighbours of a pure deletion
    count as changed. Only the mutants on those lines are run.
  * a test or a test data file that is only ADDED to (a new file, or new top-level tests, or lines appended to a data file) can only add
    kills: nothing else has to run.
  * a REMOVED or MODIFIED line of an existing test or test data file, a deleted test file, or anything that changes how the gate
    measures (Cargo.toml/lock, go.mod/sum, the mutation and gate tools, the actions, the workflow p1) can weaken what the unchanged
    code is protected by: `full`.
  * documentation and the other tools do not touch a mutant.

Files written to --out: scope.json (mode, reasons, counts), rust.patch (a unified diff of the changed lines of rust/src, for
`cargo mutants --in-diff`), diff-lines.json (the changed line ranges of the product Go files, for tools/mutation_diff_exec.sh).
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys

BUILD_CONFIG = (
    "rust/Cargo.toml", "rust/Cargo.lock", "go/go.mod", "go/go.sum", "tools/quality_gate.py", "tools/shard_plan.py",
    "tools/mutation-costs.json", "tools/diff_gate.py",
)
# The workflow is not a reason for `full` on the pull request: a FULL is started in the background when it is merged (nightly.yml, trigger push on this
# path), and by hand at any time (workflow_dispatch, force). The pull request still runs the gate self-tests.
GATE_WORKFLOW = ".github/workflows/p1.yml"
BUILD_CONFIG_PREFIX = ("rust/.cargo/", ".github/actions/", "tools/mutation_", "coverage/")
DATA_PREFIX = ("testdata/", "corpus/")
STARTS_GO = ("func ", "type ", "var ", "const ", "//", "import ", "package ")
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


def classify(root, base, head):
    diff = parse_diff(git(root, "diff", "--unified=0", "--no-renames", base, head))
    reasons, product_rust, product_go, notes = [], {}, {}, []

    def full(why):
        reasons.append(why)

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
            if f["status"] == "new":
                notes.append("%s: a new file, an addition" % path)
                continue
            head_lines = read_head(root, head, path)
            for h in f["hunks"]:
                where = "%s:%d" % (path, h[2])
                if k != "data" and only_imports_grow(k, h[4], h[5]):
                    continue
                if h[4]:
                    full("%s removes or modifies a line of an existing test or test data" % where)
                elif k == "data":
                    if h[2] - 1 + h[3] < len(head_lines):
                        full("%s adds lines inside an existing data file (only appending is an addition)" % where)
                elif k == "test-go":
                    if not top_level_addition(path, head_lines, h, STARTS_GO):
                        full("%s adds lines that are not whole new top-level tests" % where)
                else:
                    if not top_level_addition(path, head_lines, h, STARTS_RUST):
                        full("%s adds lines that are not whole new top-level tests" % where)
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
                    full("%s:%d changes an existing unit test inside the source file" % (path, new_start))
                continue
            if new_len == 0:   # a pure deletion: the neighbours of the cut are the changed code
                ranges.append([max(new_start, 1), min(new_start + 1, max(len(head_lines), 1))])
            else:
                ranges.append([new_start, new_start + new_len - 1])
        if ranges:
            (product_rust if k == "product-rust" else product_go)[path] = (ranges, head_lines)
    return {"mode": "full" if reasons else "diff", "reasons": reasons, "notes": notes}, product_rust, product_go


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
    i = sub.add_parser("in-diff")
    i.add_argument("--lines", required=True)
    i.add_argument("--path", required=True)
    i.add_argument("--original", required=True)
    i.add_argument("--changed", required=True)
    a = ap.parse_args()
    if a.cmd == "in-diff":
        return 0 if in_diff(a.lines, a.path, a.original, a.changed) else 1
    verdict, pr, pg = classify(a.root, a.base, a.head)
    verdict = write_outputs(a.out, verdict, pr, pg)
    print("mode=%s  rust ranges=%d  go ranges=%d" % (verdict["mode"], verdict["rust_ranges"], verdict["go_ranges"]))
    for r in verdict["reasons"]:
        print("  full because: " + r)
    for n in verdict["notes"]:
        print("  " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
