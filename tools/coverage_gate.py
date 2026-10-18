#!/usr/bin/env python3
"""Branch coverage gate (ADR 0009).

Consumes normalized `cic-coverage-report/1` JSON reports (one per language), the scope
manifest and the tool pins, and decides pass or fail. It measures nothing itself: the
numbers come from a pinned tool through an adapter (P1). It fails closed: a missing
report, a disabled or empty measurement, an unselected tool, an incomplete scope or an
inconsistent count is red, and so is exactly 90.00%.

Usage:
    coverage_gate.py check --root DIR --scope F --tools F --commit SHA \\
                           --report rust=PATH --report go=PATH
    coverage_gate.py check ... --only rust --report rust=PATH   diagnostic, never a PASS
    coverage_gate.py selftest          seeded-fault proof that the gate can fail
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from fractions import Fraction

import yaml

REPORT_FORMAT = "cic-coverage-report/1"
DEFINITION = "cic-branch/1"
THRESHOLD = 90  # percent, strictly exceeded
CATEGORIES = ("conditional", "match", "loop", "short_circuit", "error_path")
LANGUAGES = ("rust", "go")

REASONS = {
    "REPORT_MISSING": "report absent or unreadable",
    "REPORT_FORMAT": "report is not a valid cic-coverage-report/1",
    "LANGUAGE_SET_INVALID": "the required languages are not exactly rust and go (empty, partial, unknown or repeated)",
    "LANGUAGE_NOT_REPORTED": "a required language has no report at all",
    "MEASUREMENT_EMPTY": "nothing was measured (no files or no branches)",
    "TOOL_NOT_SELECTED": "no measurement tool is selected for the language",
    "TOOL_MISMATCH": "the report's tool or version is not the selected pin",
    "DEFINITION_MISMATCH": "the report does not use the branch definition cic-branch/1",
    "COMMIT_MISMATCH": "the report is for a different commit than the one under test",
    "SCOPE_INCOMPLETE": "an own source file is missing from the report",
    "SCOPE_EXTRA": "the report lists a file outside the scope",
    "SOURCE_DIGEST": "a file's digest differs from the measured source",
    "COUNT_INCONSISTENT": "counts do not add up",
    "CATEGORY_UNMEASURED": "the tool did not measure a branch category without an approved deviation",
    "BELOW_THRESHOLD": "branch coverage is not strictly above 90%",
}


def glob_re(pat):
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile("^" + out + "$")


def own_sources(root, spec):
    """Own source files of a language: included, minus test and generated files, by manifest globs only."""
    base = spec["root"]
    inc = [glob_re(p) for p in spec["include"]]
    exc = [glob_re(p) for p in spec["exclude_test"] + spec["exclude_generated"]]
    found = []
    for d, _, files in os.walk(os.path.join(root, base)):
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), os.path.join(root, base)).replace(os.sep, "/")
            if any(r.match(rel) for r in inc) and not any(r.match(rel) for r in exc):
                found.append(base + "/" + rel)
    return sorted(found)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def is_count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def check_language(lang, report_path, root, scope, tools, commit):
    """Return (failures, summary). A failure is (reason, detail)."""
    fails = []

    def fail(reason, detail):
        fails.append((reason, detail))

    sel = (tools.get(lang) or {}).get("selected")
    if not sel:
        fail("TOOL_NOT_SELECTED", "%s: tools file has no selected tool (selection needs the P1 sample evidence)" % lang)
    if report_path is None or not os.path.isfile(report_path):
        fail("REPORT_MISSING", "%s: %s" % (lang, report_path))
        return fails, None
    try:
        with open(report_path, encoding="utf-8") as fh:
            rep = json.load(fh, parse_float=lambda s: (_ for _ in ()).throw(ValueError("float")))
    except (OSError, ValueError) as exc:
        fail("REPORT_MISSING", "%s: unreadable: %s" % (lang, exc))
        return fails, None
    keys = {"format", "language", "metric", "branch_definition", "tool", "unmeasured_categories", "source", "totals"}
    ok = (isinstance(rep, dict) and set(rep) == keys and rep["format"] == REPORT_FORMAT
          and rep["language"] == lang and rep["metric"] == "branch"
          and isinstance(rep["tool"], dict) and set(rep["tool"]) == {"name", "version"}
          and all(isinstance(x, str) and x for x in rep["tool"].values())
          and isinstance(rep["unmeasured_categories"], list)
          and isinstance(rep["source"], dict) and set(rep["source"]) == {"commit", "files"}
          and isinstance(rep["source"]["files"], list)
          and isinstance(rep["totals"], dict) and set(rep["totals"]) == {"total", "covered"})
    if not ok:
        fail("REPORT_FORMAT", "%s: keys, format, language or metric wrong" % lang)
        return fails, None
    if rep["branch_definition"] != DEFINITION:
        fail("DEFINITION_MISMATCH", "%s: %r" % (lang, rep["branch_definition"]))
    if sel and (rep["tool"]["name"], rep["tool"]["version"]) != (sel.get("name"), sel.get("version")):
        fail("TOOL_MISMATCH", "%s: report %s %s, selected %s %s" % (
            lang, rep["tool"]["name"], rep["tool"]["version"], sel.get("name"), sel.get("version")))
    if rep["source"]["commit"] != commit:
        fail("COMMIT_MISMATCH", "%s: report %s, under test %s" % (lang, rep["source"]["commit"], commit))
    approved = set((sel or {}).get("approved_deviations", []))
    bad_dev = [c for c in rep["unmeasured_categories"] if c not in approved or c not in CATEGORIES]
    if bad_dev or any(c not in CATEGORIES for c in rep["unmeasured_categories"]):
        fail("CATEGORY_UNMEASURED", "%s: %s" % (lang, bad_dev or rep["unmeasured_categories"]))
    # scope: exactly the own source files, with the measured digests
    expected = own_sources(root, scope[lang])
    listed = []
    for f in rep["source"]["files"]:
        if not (isinstance(f, dict) and set(f) == {"path", "sha256", "total", "covered", "categories"}
                and isinstance(f["path"], str)):
            fail("REPORT_FORMAT", "%s: malformed file entry" % lang)
            return fails, None
        listed.append(f["path"])
    for p in sorted(set(expected) - set(listed)):
        fail("SCOPE_INCOMPLETE", "%s: %s" % (lang, p))
    for p in sorted(set(listed) - set(expected)):
        fail("SCOPE_EXTRA", "%s: %s" % (lang, p))
    if len(set(listed)) != len(listed):
        fail("REPORT_FORMAT", "%s: a file is listed twice" % lang)
    sum_t = sum_c = 0
    for f in rep["source"]["files"]:
        p = f["path"]
        fp = os.path.join(root, p)
        if os.path.isfile(fp) and sha256(fp) != f["sha256"]:
            fail("SOURCE_DIGEST", "%s: %s" % (lang, p))
        cats = f["categories"]
        if not (isinstance(cats, dict) and set(cats) == set(CATEGORIES) and all(
                isinstance(v, dict) and set(v) == {"total", "covered"} and is_count(v["total"])
                and is_count(v["covered"]) for v in cats.values())
                and is_count(f["total"]) and is_count(f["covered"])):
            fail("COUNT_INCONSISTENT", "%s: %s categories malformed" % (lang, p))
            continue
        if (f["covered"] > f["total"] or any(v["covered"] > v["total"] for v in cats.values())
                or sum(v["total"] for v in cats.values()) != f["total"]
                or sum(v["covered"] for v in cats.values()) != f["covered"]):
            fail("COUNT_INCONSISTENT", "%s: %s" % (lang, p))
        sum_t += f["total"]
        sum_c += f["covered"]
    t = rep["totals"]
    if not (is_count(t["total"]) and is_count(t["covered"])) or (t["total"], t["covered"]) != (sum_t, sum_c):
        fail("COUNT_INCONSISTENT", "%s: totals %s differ from the sum of the files (%d/%d)" % (lang, t, sum_c, sum_t))
    if not rep["source"]["files"] or sum_t == 0:
        fail("MEASUREMENT_EMPTY", "%s: %d files, %d branches" % (lang, len(rep["source"]["files"]), sum_t))
    elif sum_c * 100 <= sum_t * THRESHOLD:  # exact integer arithmetic: 90.00% is not enough
        fail("BELOW_THRESHOLD", "%s: %d/%d = %s%%" % (lang, sum_c, sum_t, pct(sum_c, sum_t)))
    return fails, "%d/%d = %s%%" % (sum_c, sum_t, pct(sum_c, sum_t)) if sum_t else None


def pct(c, t):
    q = Fraction(c * 10000, t)
    return "%d.%02d" % (q.numerator // q.denominator // 100, q.numerator // q.denominator % 100)


def evaluate(root, scope, tools, commit, require, only, reports):
    """Return (failures, lines, rc). Compliance needs exactly rust and go; --only is a diagnostic."""
    fails, lines = [], []
    if only is not None:
        langs = [only] if only in LANGUAGES else []
        if not langs:
            fails.append(("-", "LANGUAGE_SET_INVALID", "unknown language %r" % only))
    else:
        langs = [x for x in require.split(",") if x]
        if sorted(langs) != sorted(LANGUAGES):
            fails.append(("-", "LANGUAGE_SET_INVALID",
                          "required languages %r, a conformance result needs exactly %s" % (langs, list(LANGUAGES))))
            langs = []
    for lang in langs:
        if lang not in reports:
            fails.append((lang, "LANGUAGE_NOT_REPORTED", "no report given for %s" % lang))
            continue
        f, summ = check_language(lang, reports[lang], root, scope, tools, commit)
        fails += [(lang, r, d) for r, d in f]
        lines.append("%-5s %s" % (lang, summ or "not measured"))
    if only is not None and not fails:
        lines.append("DIAGNOSTIC %s: no failure found. This is NOT a conformance result (exit status 3)" % only)
        return fails, lines, 3
    lines += ["FAIL %s %s: %s" % f for f in fails]
    lines.append("BRANCH COVERAGE: %s" % ("FAIL (%d)" % len(fails) if fails else "PASS"))
    return fails, lines, 1 if fails else 0


def run_check(args):
    scope = yaml.safe_load(open(args.scope, encoding="utf-8"))["languages"]
    tools = yaml.safe_load(open(args.tools, encoding="utf-8"))
    reports = dict(r.split("=", 1) for r in args.report)
    _, lines, rc = evaluate(args.root, scope, tools, args.commit, args.require, args.only, reports)
    print("\n".join(lines))
    return rc


# --- seeded-fault self-test -------------------------------------------------------

def apply_ops(ops, work):
    for op in ops:
        path = os.path.join(work, op["file"])
        if "delete" in op:
            os.remove(path)
        elif "add" in op:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(op["add"])
        elif "json_set" in op:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            cur = doc
            for k in op["json_set"][:-1]:
                cur = cur[k]
            cur[op["json_set"][-1]] = op["value"]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1)
        else:
            text = open(path, encoding="utf-8").read()
            if text.count(op["old"]) != 1:
                raise ValueError("%r occurs %d times in %s" % (op["old"][:60], text.count(op["old"]), op["file"]))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(op["old"], op["new"]))


def fixture_run(work, drop=None, invocation=None):
    fx = os.path.join(work, "coverage", "fixtures")
    scope = yaml.safe_load(open(os.path.join(fx, "scope.yaml"), encoding="utf-8"))["languages"]
    tools = yaml.safe_load(open(os.path.join(fx, "tools.yaml"), encoding="utf-8"))
    commit = open(os.path.join(fx, "commit.txt")).read().strip()
    inv = {"require": "rust,go", "only": None, "reports": ["rust", "go"]}
    inv.update(invocation or {})
    reports = {l: os.path.join(fx, "%s.report.json" % l) for l in inv["reports"] if l != drop}
    fails, _, rc = evaluate(os.path.join(fx, "tree"), scope, tools, commit, inv["require"], inv["only"], reports)
    return [(l, r) for l, r, _ in fails], rc


def selftest(root):
    spec = yaml.safe_load(open(os.path.join(root, "coverage", "faults.yaml"), encoding="utf-8"))
    tmp = tempfile.mkdtemp(prefix="cov-base-")
    try:
        shutil.copytree(os.path.join(root, "coverage"), os.path.join(tmp, "coverage"))
        base, base_rc = fixture_run(tmp)
        diag, diag_rc = fixture_run(tmp, invocation={"only": "rust", "reports": ["rust"]})
    finally:
        shutil.rmtree(tmp)
    if base or base_rc != 0:
        print("self-test: the unmodified fixtures are not green: %s" % base)
        return 1
    if diag or diag_rc == 0:
        print("self-test: a diagnostic run must never end with status 0 (got %s, %s)" % (diag, diag_rc))
        return 1
    covered, failed = set(), 0
    for fault in spec["faults"]:
        tmp = tempfile.mkdtemp(prefix="cov-fault-")
        try:
            shutil.copytree(os.path.join(root, "coverage"), os.path.join(tmp, "coverage"))
            try:
                apply_ops(fault.get("ops", []), os.path.join(tmp, "coverage", "fixtures"))
            except (ValueError, OSError, KeyError) as exc:
                failed += 1
                print("  STALE %-34s %s" % (fault["id"], exc))
                continue
            got, _ = fixture_run(tmp, fault.get("drop_report"), fault.get("invocation"))
        finally:
            shutil.rmtree(tmp)
        want = (fault["language"], fault["reason"])
        if want in got:
            covered.add(fault["reason"])
            print("  red   %-34s -> %s %s" % (fault["id"], want[0], want[1]))
        else:
            failed += 1
            print("  GREEN %-34s expected %s, got %s" % (fault["id"], want, got or "pass"))
    for r in sorted(set(REASONS) - covered):
        failed += 1
        print("  reason %s has no fault that turns it red" % r)
    # the real manifests must keep the contract: exact 90.00 fails, strictly above passes
    boundary = [(90, 100, False), (901, 1000, True), (91, 100, True), (9, 10, False), (0, 0, False)]
    for c, t, ok in boundary:
        if (t > 0 and c * 100 > t * THRESHOLD) != ok:
            failed += 1
            print("  threshold arithmetic wrong for %d/%d" % (c, t))
    print("self-test: %d faults, %d of %d reasons covered, %s" % (
        len(spec["faults"]), len(covered), len(REASONS), "FAILED" if failed else "the gate can fail for every reason"))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--root", required=True)
    c.add_argument("--scope", required=True)
    c.add_argument("--tools", required=True)
    c.add_argument("--commit", required=True)
    c.add_argument("--require", default="rust,go")
    c.add_argument("--only", default=None, help="diagnostic run for one language; never exits 0")
    c.add_argument("--report", action="append", default=[])
    sub.add_parser("selftest")
    args = ap.parse_args()
    if args.cmd == "check":
        return run_check(args)
    rc = selftest(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if rc == 0:
        print("GATE LOGIC ONLY: no Rust or Go code is measured yet (rust: none, go: none)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
