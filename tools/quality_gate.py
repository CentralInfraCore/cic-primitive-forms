#!/usr/bin/env python3
"""Quality gate of the P1 product code (docs/PHASES.md, "Quality gates").

Line coverage and mutation score must each be strictly above 90%, in both languages, on the
own source files of rust/ and go/. The numbers come from the measurement tools; this script only
decides. It fails closed: a missing or unreadable report, a report of an unexpected shape, a
measurement of nothing, or exactly 90.00% is red.

    quality_gate.py check --rust-lines F --go-lines F --rust-mutants F... --go-mutants F
    quality_gate.py check --rust-lines F --go-lines F --rust-mutants F... --rust-listed F --go-ledgers F...
    quality_gate.py selftest          seeded-fault proof that the gate can fail for every reason

Reports:
  --rust-lines    cargo llvm-cov --json --summary-only   (line coverage)
  --go-lines      go test -coverprofile output           (statement coverage; Go has no line metric)
  --rust-mutants  cargo-mutants outcomes.json            (score = (caught + timeout) / (caught + timeout + missed))
                  One file, or one per shard (`cargo mutants --shard k/N`); with several, --rust-listed is required.
  --rust-listed   `cargo mutants --list --json`          the mutants there are; every one must be in exactly one shard
  --go-mutants    go-mutesting stdout                    (score = passed / (passed + failed))
  --go-ledgers    one tools/mutation_shard_exec.sh ledger per shard of a sharded go-mutesting run
Unviable mutants (Rust) and skipped or duplicated ones (Go) are not counted either way.

A sharded measurement is only as good as its completeness: a shard that did not run, a mutant that two shards
tested or that none tested, shards that disagree on which mutants there are, would raise the score without
anyone noticing. Each of those is INCOMPLETE_SHARDS, red.
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
    "INCOMPLETE_SHARDS": "a shard is missing, or a mutant was tested twice or not at all",
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


def _rust_outcome(path, need_names):
    try:
        d = json.loads(read(path))
        killed = d["caught"] + d["timeout"]
        survived = d["missed"]
        if not all(isinstance(x, int) for x in (killed, survived)):
            raise TypeError
        names = [o["scenario"]["Mutant"]["name"] for o in d["outcomes"] if isinstance(o.get("scenario"), dict)] if need_names else []
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Refused("REPORT_FORMAT", "%s: not a cargo-mutants outcomes.json" % path)
    return killed, survived, names


def _rust_listed(path):
    try:
        listed = [m["name"] for m in json.loads(read(path))]
        if not listed or not all(isinstance(n, str) for n in listed):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise Refused("REPORT_FORMAT", "%s: not a `cargo mutants --list --json` list" % path)
    return listed


def rust_mutants(paths, listed=None):
    killed = survived = 0
    names = []
    for path in paths:
        k, m, n = _rust_outcome(path, bool(listed))
        killed += k
        survived += m
        names += n
    if len(paths) > 1 and not listed:
        raise Refused("INCOMPLETE_SHARDS", "%d shard reports and no list of the mutants to check them against" % len(paths))
    if listed:
        want = _rust_listed(listed)
        seen = {}
        for n in names:
            seen[n] = seen.get(n, 0) + 1
        twice = sorted(n for n, c in seen.items() if c > 1)
        missing = sorted(set(want) - set(seen))
        extra = sorted(set(seen) - set(want))
        if twice or missing or extra:
            raise Refused("INCOMPLETE_SHARDS", "%d tested twice, %d not tested, %d not in the list (first: %s)" % (
                len(twice), len(missing), len(extra), (twice + missing + extra)[0]))
    return killed, killed + survived


LEDGER_VERDICTS = {"killed", "survived", "uncompilable", "offshard"}


def go_ledgers(paths):
    """One ledger per shard, lines `<mutant>\t<shard>\t<shards>\t<verdict>`: every shard sees every mutant and tests
    the ones it owns; each mutant must be owned by exactly one shard."""
    shard_of, ids_of, count = {}, {}, None
    verdicts = {}
    for path in paths:
        mine = {}
        for line in read(path).splitlines():
            f = line.split("\t")
            if len(f) != 4 or not f[1].isdigit() or not f[2].isdigit() or f[3] not in LEDGER_VERDICTS:
                raise Refused("REPORT_FORMAT", "%s: unreadable ledger line %r" % (path, line))
            k, n = int(f[1]), int(f[2])
            if mine.setdefault("shard", (k, n)) != (k, n):
                raise Refused("INCOMPLETE_SHARDS", "%s: lines of more than one shard" % path)
            if f[0] in mine.get("ids", {}):
                raise Refused("INCOMPLETE_SHARDS", "%s: mutant %s twice in one ledger" % (path, f[0]))
            mine.setdefault("ids", {})[f[0]] = f[3]
        if "shard" not in mine:
            raise Refused("MEASUREMENT_EMPTY", "%s: empty ledger" % path)
        k, n = mine["shard"]
        if count not in (None, n) or k >= n or k in shard_of:
            raise Refused("INCOMPLETE_SHARDS", "%s: shard %d/%d does not fit the others" % (path, k, n))
        count = n
        shard_of[k] = path
        ids_of[k] = set(mine["ids"])
        for mid, v in mine["ids"].items():
            verdicts.setdefault(mid, []).append(v)
    if sorted(shard_of) != list(range(count)):
        raise Refused("INCOMPLETE_SHARDS", "shards %s of %d" % (sorted(shard_of), count))
    first = ids_of[0]
    if any(ids != first for ids in ids_of.values()):
        raise Refused("INCOMPLETE_SHARDS", "the shards disagree on which mutants there are")
    killed = survived = 0
    for mid, vs in verdicts.items():
        owned = [v for v in vs if v != "offshard"]
        if len(owned) != 1:
            raise Refused("INCOMPLETE_SHARDS", "mutant %s was tested by %d shards" % (mid, len(owned)))
        killed += owned[0] == "killed"
        survived += owned[0] == "survived"
    return killed, killed + survived


def go_mutants(path):
    m = re.search(r"The mutation score is [0-9.]+ \((\d+) passed, (\d+) failed, (\d+) duplicated, (\d+) skipped, total is (\d+)\)",
                  read(path))
    if not m:
        raise Refused("REPORT_FORMAT", "%s: no go-mutesting summary line" % path)
    killed, survived = int(m.group(1)), int(m.group(2))
    return killed, killed + survived


def judge(name, measure, path):
    covered, total = measure()
    if total == 0:
        raise Refused("MEASUREMENT_EMPTY", "%s: %s" % (name, path))
    pct = Fraction(100 * covered, total)
    ok = pct > THRESHOLD
    return ok, "%-22s %d/%d = %.2f%%" % (name, covered, total, float(pct))


def check(args):
    rust_m = args.rust_mutants if isinstance(args.rust_mutants, list) else [args.rust_mutants]
    go_m = (lambda: go_ledgers(args.go_ledgers)) if args.go_ledgers else (lambda: go_mutants(args.go_mutants))
    gates = [("rust line coverage", lambda: rust_lines(args.rust_lines), args.rust_lines),
             ("go statement coverage", lambda: go_lines(args.go_lines), args.go_lines),
             ("rust mutation score", lambda: rust_mutants(rust_m, args.rust_listed), " ".join(rust_m)),
             ("go mutation score", go_m, " ".join(args.go_ledgers) if args.go_ledgers else args.go_mutants)]
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
            ns = argparse.Namespace(rust_lines=files["rl"], go_lines=files["gl"], rust_mutants=[files["rm"]], rust_listed=None,
                                    go_mutants=files["gm"], go_ledgers=None)
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
        ns = argparse.Namespace(rust_lines=files["rl"], go_lines=os.path.join(d, "absent"), rust_mutants=[files["rm"]], rust_listed=None,
                                go_mutants=files["gm"], go_ledgers=None)
        rc, text = _capture(check, ns)
        ok = rc == 1 and "REPORT_MISSING" in text
        seen.add("REPORT_MISSING" if ok else "?")
        bad += 0 if ok else 1
        print("  %s %-48s -> %s" % ("ok " if ok else "BAD", "a report is absent", "REPORT_MISSING" if ok else "?"))
    # sharded measurements: the merged score, and every way a shard set can be incomplete
    def rshard(names, missed=()):
        outs = [{"scenario": "Baseline", "summary": "Success"}] + [
            {"scenario": {"Mutant": {"name": n}}, "summary": "MissedMutant" if n in missed else "CaughtMutant"} for n in names]
        return json.dumps({"caught": len(names) - len(missed), "missed": len(missed), "timeout": 0, "unviable": 2,
                           "total_mutants": len(names) + 2, "outcomes": outs})

    def listing(names):
        return json.dumps([{"name": n, "file": "src/a.rs"} for n in names])

    def ledger(k, n, owned, others, verdict="killed"):
        rows = ["%s\t%d\t%d\t%s" % (m, k, n, verdict if m in owned else "offshard") for m in others]
        return "\n".join(rows) + "\n"

    allr = ["m%02d" % i for i in range(20)]
    a, b = allr[:10], allr[10:]
    gm = ["g%02d" % i for i in range(30)]
    parts = [gm[0:10], gm[10:20], gm[20:30]]

    def sharded(title, want, rust=None, listed=None, ledgers=None):
        nonlocal bad
        with tempfile.TemporaryDirectory() as d:
            rfiles = [_w(d, "r%d" % i, t) for i, t in enumerate(rust if rust is not None else [rshard(a), rshard(b)])]
            lf = _w(d, "list", listed if listed is not None else listing(allr))
            gfiles = [_w(d, "g%d" % i, t) for i, t in enumerate(ledgers if ledgers is not None else
                      [ledger(k, 3, parts[k], gm) for k in range(3)])]
            ns = argparse.Namespace(rust_lines=_w(d, "rl", _rust_cov(95, 100)), go_lines=_w(d, "gl", _go_cov(95, 100)),
                                    rust_mutants=rfiles, rust_listed=lf, go_mutants=None, go_ledgers=gfiles)
            rc, text = _capture(check, ns)
        got = None if rc == 0 else next((r for r in REASONS if r in text), "?")
        seen.add(got)
        ok = got == want
        bad += 0 if ok else 1
        print("  %s %-48s -> %s" % ("ok " if ok else "BAD", title, got or "pass"))

    sharded("sharded, complete", None)
    sharded("sharded: the merged rust score is below 90%", "BELOW_THRESHOLD", rust=[rshard(a, missed=a[:3]), rshard(b, missed=b[:3])])
    sharded("sharded: a rust shard did not run", "INCOMPLETE_SHARDS", rust=[rshard(a)])
    sharded("sharded: a rust mutant was tested by two shards", "INCOMPLETE_SHARDS", rust=[rshard(a + b[:1]), rshard(b)])
    sharded("sharded: a rust shard reports a mutant that is not listed", "INCOMPLETE_SHARDS", rust=[rshard(a + ["zz"]), rshard(b)])
    sharded("sharded: the list is not a list of mutants", "REPORT_FORMAT", listed="{}")
    sharded("sharded: a go shard did not run", "INCOMPLETE_SHARDS", ledgers=[ledger(k, 3, parts[k], gm) for k in (0, 1)])
    sharded("sharded: a go mutant was tested by two shards", "INCOMPLETE_SHARDS",
            ledgers=[ledger(0, 3, parts[0] + parts[1][:1], gm), ledger(1, 3, parts[1], gm), ledger(2, 3, parts[2], gm)])
    sharded("sharded: a go mutant was tested by none", "INCOMPLETE_SHARDS",
            ledgers=[ledger(0, 3, parts[0][1:], gm), ledger(1, 3, parts[1], gm), ledger(2, 3, parts[2], gm)])
    sharded("sharded: the go shards disagree on the mutants", "INCOMPLETE_SHARDS",
            ledgers=[ledger(0, 3, parts[0], gm), ledger(1, 3, parts[1], gm), ledger(2, 3, parts[2], gm[:-1] + ["gxx"])])
    sharded("sharded: two go ledgers of the same shard", "INCOMPLETE_SHARDS",
            ledgers=[ledger(0, 3, parts[0], gm), ledger(0, 3, parts[1], gm), ledger(2, 3, parts[2], gm)])
    sharded("sharded: a go ledger line is garbage", "REPORT_FORMAT", ledgers=["nonsense\n"] + [ledger(k, 3, parts[k], gm) for k in (1, 2)])
    sharded("sharded: an empty go ledger", "MEASUREMENT_EMPTY", ledgers=[""] + [ledger(k, 3, parts[k], gm) for k in (1, 2)])
    sharded("sharded: the merged go score is below 90%", "BELOW_THRESHOLD",
            ledgers=[ledger(k, 3, parts[k], gm, verdict="survived") for k in range(3)])
    missing = set(REASONS) - seen
    if missing:
        print("  BAD reasons without a fault: %s" % sorted(missing))
        bad += 1
    print("self-test: %d faults, %d of %d reasons covered, the gate can fail for every reason" % (len(cases) + 1 + 14, len(set(REASONS) & seen), len(REASONS))
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
    for flag in ("rust-lines", "go-lines"):
        c.add_argument("--" + flag, required=True)
    c.add_argument("--rust-mutants", required=True, nargs="+")
    c.add_argument("--rust-listed")
    group = c.add_mutually_exclusive_group(required=True)
    group.add_argument("--go-mutants")
    group.add_argument("--go-ledgers", nargs="+")
    sub.add_parser("selftest")
    a = ap.parse_args()
    return check(a) if a.cmd == "check" else selftest()


if __name__ == "__main__":
    sys.exit(main())
