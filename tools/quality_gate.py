#!/usr/bin/env python3
"""Quality gate of the P1 product code (docs/PHASES.md, "Quality gates").

Line coverage and mutation score must each be strictly above 90%, in both languages, on the
own source files of rust/ and go/. The numbers come from the measurement tools; this script only
decides. It fails closed: a missing or unreadable report, a report of an unexpected shape, a
measurement of nothing, or exactly 90.00% is red.

    quality_gate.py check --rust-lines F --go-lines F --rust-mutants F --go-mutants F
    quality_gate.py selftest          seeded-fault proof that the gate can fail for every reason

Reports:
  --rust-lines    cargo llvm-cov --json --summary-only   (line coverage)
  --go-lines      go test -coverprofile output           (statement coverage; Go has no line metric)
  --rust-mutants  cargo-mutants outcomes.json            (score = (caught + timeout) / (caught + timeout + missed))
  --go-mutants    go-mutesting stdout                    (score = passed / (passed + failed))
Unviable mutants (Rust) and skipped or duplicated ones (Go) are not counted either way.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from fractions import Fraction

THRESHOLD = 90  # percent, strictly exceeded

REASONS = {
    "REPORT_MISSING": "report absent or unreadable",
    "REPORT_FORMAT": "report is not in the expected shape",
    "MEASUREMENT_EMPTY": "nothing was measured (no lines or no mutants)",
    "BELOW_THRESHOLD": "not strictly above 90%",
}


class Refused(Exception):
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise Refused("REPORT_MISSING", "%s: %s" % (path, e))


def rust_lines(path):
    try:
        data = json.loads(read(path))
        files = data["data"][0]["files"]
        covered = total = 0
        for f in files:
            if "/src/" not in f["filename"]:  # own sources only; tests are not measured code
                continue
            covered += f["summary"]["lines"]["covered"]
            total += f["summary"]["lines"]["count"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise Refused("REPORT_FORMAT", "%s: not a cargo llvm-cov summary" % path)
    return covered, total


def go_lines(path):
    covered = total = 0
    seen_mode = False
    for line in read(path).splitlines():
        if line.startswith("mode:"):
            seen_mode = True
            continue
        m = re.fullmatch(r"(\S+):\d+\.\d+,\d+\.\d+ (\d+) (\d+)", line.strip())
        if not m:
            raise Refused("REPORT_FORMAT", "%s: unreadable line %r" % (path, line))
        if m.group(1).endswith("_test.go"):
            continue
        total += int(m.group(2))
        covered += int(m.group(2)) if int(m.group(3)) > 0 else 0
    if not seen_mode:
        raise Refused("REPORT_FORMAT", "%s: no coverage profile header" % path)
    return covered, total


def rust_mutants(path):
    try:
        d = json.loads(read(path))
        killed = d["caught"] + d["timeout"]
        survived = d["missed"]
        if not all(isinstance(x, int) for x in (killed, survived)):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise Refused("REPORT_FORMAT", "%s: not a cargo-mutants outcomes.json" % path)
    return killed, killed + survived


def go_mutants(path):
    m = re.search(r"The mutation score is [0-9.]+ \((\d+) passed, (\d+) failed, (\d+) duplicated, (\d+) skipped, total is (\d+)\)",
                  read(path))
    if not m:
        raise Refused("REPORT_FORMAT", "%s: no go-mutesting summary line" % path)
    killed, survived = int(m.group(1)), int(m.group(2))
    return killed, killed + survived


def judge(name, measure, path):
    covered, total = measure(path)
    if total == 0:
        raise Refused("MEASUREMENT_EMPTY", "%s: %s" % (name, path))
    pct = Fraction(100 * covered, total)
    ok = pct > THRESHOLD
    return ok, "%-22s %d/%d = %.2f%%" % (name, covered, total, float(pct))


def check(args):
    gates = [("rust line coverage", rust_lines, args.rust_lines), ("go statement coverage", go_lines, args.go_lines),
             ("rust mutation score", rust_mutants, args.rust_mutants), ("go mutation score", go_mutants, args.go_mutants)]
    failed = 0
    for name, measure, path in gates:
        try:
            ok, line = judge(name, measure, path)
        except Refused as r:
            print("FAIL %-22s %s: %s" % (name, r.reason, r.detail))
            failed += 1
            continue
        print(("ok   " if ok else "FAIL ") + line + ("" if ok else "  BELOW_THRESHOLD"))
        failed += 0 if ok else 1
    print("QUALITY GATE: %s" % ("PASS" if failed == 0 else "FAIL (%d)" % failed))
    return 0 if failed == 0 else 1


# ---- seeded-fault proof -------------------------------------------------------------------------

def _w(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def _rust_cov(covered, count, name="/r/rust/src/a.rs"):
    return json.dumps({"data": [{"files": [{"filename": name, "summary": {"lines": {"covered": covered, "count": count}}}]}]})


def _go_cov(covered, count, name="x/a.go"):
    return "mode: set\n%s:1.1,2.2 %d %d\n%s:3.1,4.2 %d 0\n" % (name, covered, 1, name, count - covered)


def _mut(caught, missed, timeout=0):
    return json.dumps({"caught": caught, "missed": missed, "timeout": timeout, "unviable": 5, "total_mutants": caught + missed + 5})


def _gomut(passed, failed):
    return "...\nThe mutation score is 0.0 (%d passed, %d failed, 0 duplicated, 3 skipped, total is %d)\n" % (passed, failed, passed + failed + 3)


def selftest():
    good = dict(rl=_rust_cov(95, 100), gl=_go_cov(95, 100), rm=_mut(95, 5), gm=_gomut(95, 5))
    cases = [  # (name, overrides, expected reason or None)
        ("all good", {}, None),
        ("exactly 90% line coverage is red", dict(rl=_rust_cov(90, 100)), "BELOW_THRESHOLD"),
        ("90.01% is green", dict(rl=_rust_cov(9001, 10000)), None),
        ("go coverage below", dict(gl=_go_cov(50, 100)), "BELOW_THRESHOLD"),
        ("go profile counts statements, not blocks", dict(gl=_go_cov(95, 100)), None),
        ("a test file is not measured code", dict(gl=_go_cov(95, 100) + "x/a_test.go:9.1,9.2 50 0\n"), None),
        ("rust files outside src are not counted", dict(rl=_rust_cov(95, 100).replace("]}]}", ',{"filename":"/r/rust/tests/t.rs","summary":{"lines":{"covered":0,"count":900}}}]}]}')), None),
        ("rust mutation score exactly 90 is red", dict(rm=_mut(90, 10)), "BELOW_THRESHOLD"),
        ("timeouts count as caught", dict(rm=_mut(80, 10, timeout=10)), "BELOW_THRESHOLD"),
        ("go mutation below", dict(gm=_gomut(10, 90)), "BELOW_THRESHOLD"),
        ("rust mutants: nothing tested", dict(rm=_mut(0, 0)), "MEASUREMENT_EMPTY"),
        ("go mutants: nothing tested", dict(gm=_gomut(0, 0)), "MEASUREMENT_EMPTY"),
        ("rust coverage: no source file", dict(rl=_rust_cov(0, 0)), "MEASUREMENT_EMPTY"),
        ("go coverage: empty profile", dict(gl="mode: set\n"), "MEASUREMENT_EMPTY"),
        ("rust coverage is not JSON", dict(rl="oops"), "REPORT_FORMAT"),
        ("go coverage without header", dict(gl="x/a.go:1.1,2.2 1 1\n"), "REPORT_FORMAT"),
        ("go coverage garbage line", dict(gl="mode: set\nnonsense\n"), "REPORT_FORMAT"),
        ("mutants json without the counters", dict(rm="{}"), "REPORT_FORMAT"),
        ("mutants json with a string counter", dict(rm=json.dumps({"caught": "9", "missed": 1, "timeout": 0})), "REPORT_FORMAT"),
        ("go-mutesting without a summary", dict(gm="no summary here"), "REPORT_FORMAT"),
    ]
    names = {"rl": "rust-lines", "gl": "go-lines", "rm": "rust-mutants", "gm": "go-mutants"}
    bad = 0
    seen = set()
    for title, over, want in cases:
        with tempfile.TemporaryDirectory() as d:
            files = {k: _w(d, names[k], v) for k, v in dict(good, **over).items()}
            ns = argparse.Namespace(rust_lines=files["rl"], go_lines=files["gl"], rust_mutants=files["rm"], go_mutants=files["gm"])
            out = _capture(check, ns)
            rc, text = out
        got = None if rc == 0 else next((r for r in REASONS if r in text), "?")
        seen.add(got)
        ok = got == want
        bad += 0 if ok else 1
        print("  %s %-48s -> %s" % ("ok " if ok else "BAD", title, got or "pass"))
    # a missing report is its own reason
    with tempfile.TemporaryDirectory() as d:
        files = {k: _w(d, names[k], v) for k, v in good.items()}
        ns = argparse.Namespace(rust_lines=files["rl"], go_lines=os.path.join(d, "absent"), rust_mutants=files["rm"], go_mutants=files["gm"])
        rc, text = _capture(check, ns)
        ok = rc == 1 and "REPORT_MISSING" in text
        seen.add("REPORT_MISSING" if ok else "?")
        bad += 0 if ok else 1
        print("  %s %-48s -> %s" % ("ok " if ok else "BAD", "a report is absent", "REPORT_MISSING" if ok else "?"))
    missing = set(REASONS) - seen
    if missing:
        print("  BAD reasons without a fault: %s" % sorted(missing))
        bad += 1
    print("self-test: %d faults, %d of %d reasons covered, the gate can fail for every reason" % (len(cases) + 1, len(set(REASONS) & seen), len(REASONS))
          if bad == 0 else "self-test FAILED (%d)" % bad)
    return 0 if bad == 0 else 1


def _capture(fn, *a):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a)
    return rc, buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    for flag in ("rust-lines", "go-lines", "rust-mutants", "go-mutants"):
        c.add_argument("--" + flag, required=True)
    sub.add_parser("selftest")
    a = ap.parse_args()
    return check(a) if a.cmd == "check" else selftest()


if __name__ == "__main__":
    sys.exit(main())
