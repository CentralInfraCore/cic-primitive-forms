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
  --go-expected   the number of Go mutants the plan counted (#94): the ledgers must give exactly that many a verdict
  --gamma-reports cargo-gamma gamma-report.json, one per shard   (the pull request's full run; instead of --rust-mutants)
  --gamma-listed  `cargo gamma list mutants --json`               the mutants there are; every one must be measured by exactly one shard
  --gomutants-reports  go-mutants mutation.json, one per shard    (the pull request's full run; instead of --go-mutants and --go-ledgers)
                  Both write a mutation-testing-elements report. Killed is killed; Survived, NoCoverage and Timeout (gamma writes its timeouts and
                  memory-limit stops as Survived) are not; Ignored, a mutant nobody could judge (flaky, inconclusive), is not killed either and stays
                  in the denominator, so it can only lower the score; CompileError (unviable) is left out and printed. A go-mutants shard report names
                  the other shards' mutants too, as Ignored "another shard ..."; those are the catalogue, not a verdict.
Unviable mutants (Rust) and skipped or duplicated ones (Go) are not counted either way. Their share of the mutants the tool listed is printed under each
mutation score ("not in the denominator: N of M mutants"): information only, no threshold, so that a growing share cannot go unseen. For Go it is everything in
the total of go-mutesting that is neither killed nor escaped (skipped and errors); the duplicates are not in that total and are named next to it.

A mutation report carries its result twice, as totals and as one verdict per mutant. The score is read from the
verdicts, and totals that disagree with them are REPORT_INCONSISTENT, red: a report that contradicts itself
measures nothing.

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
    "REPORT_INCONSISTENT": "a report's totals disagree with its own per-mutant results",
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
            if "/rust/src/" not in f["filename"]:  # the sources of rust/ only: tests are not measured code, nor is a /src/ of anything else
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


# cargo-mutants 27.1.0 (src/outcome.rs, LabOutcome::add): each mutant outcome raises exactly one of these totals,
# and total_mutants once. A mutant summarised as Success or Failure has no verdict.
RUST_VERDICTS = {"CaughtMutant": "caught", "MissedMutant": "missed", "Timeout": "timeout", "Unviable": "unviable"}

# What the mutation score leaves out of its denominator, per language: [mutants left out, mutants the tool listed]. Rust: the unviable ones (they do not
# compile); Go: the skipped ones (they do not compile) and, in a ledger, the uncompilable ones. The score does not count them either way, so a
# growing share of them would shrink the population the score speaks about without any number turning red. This is information, printed under the
# score; it is not a threshold and decides nothing.
EXCLUDED = {}


def note_excluded(language, parts, listed, duplicated=0):
    """`parts` is {what: count} of the mutants the tool listed that are not in the denominator; `listed` is the number the tool listed (the total of its own
    summary); `duplicated` are mutants the tool dropped as repeats: they are not in `listed` and are reported next to it, not added to it."""
    got = EXCLUDED.setdefault(language, [{}, 0, 0])
    for k, n in parts.items():
        got[0][k] = got[0].get(k, 0) + n
    got[1] += listed
    got[2] += duplicated


def excluded_line(language):
    parts, listed, duplicated = EXCLUDED.get(language, [{}, 0, 0])
    if listed == 0:
        return None
    left_out = sum(parts.values())
    detail = ", ".join("%s %d" % (k, n) for k, n in parts.items() if n)
    return "     not in the denominator: %d of %d mutants (%.2f%%)%s%s" % (
        left_out, listed, 100.0 * left_out / listed, " (" + detail + ")" if detail else "",
        "; %d more dropped as duplicates, not in that total" % duplicated if duplicated else "")


def _rust_outcome(path, need_names):
    try:
        d = json.loads(read(path))
        totals = {k: d[k] for k in ("caught", "missed", "timeout", "unviable", "total_mutants")}
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in totals.values()):
            raise TypeError
        mutants = [o for o in d["outcomes"] if isinstance(o.get("scenario"), dict)]
        names = [o["scenario"]["Mutant"]["name"] for o in mutants]
        summaries = [o["summary"] for o in mutants]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Refused("REPORT_FORMAT", "%s: not a cargo-mutants outcomes.json" % path)
    counted = dict.fromkeys(RUST_VERDICTS.values(), 0)
    for name, s in zip(names, summaries):
        if s not in RUST_VERDICTS:
            raise Refused("REPORT_INCONSISTENT", "%s: mutant %s has no verdict (%r)" % (path, name, s))
        counted[RUST_VERDICTS[s]] += 1
    counted["total_mutants"] = len(mutants)
    note_excluded("rust", {"unviable": counted["unviable"]}, counted["total_mutants"])
    wrong = ["%s %d, counted %d" % (k, totals[k], counted[k]) for k in totals if totals[k] != counted[k]]
    if wrong:
        raise Refused("REPORT_INCONSISTENT", "%s: the totals disagree with the mutant outcomes (%s)" % (path, "; ".join(wrong)))
    return counted["caught"] + counted["timeout"], counted["missed"], names if need_names else []


def _rust_listed(path, allow_empty=False):
    try:
        listed = [m["name"] for m in json.loads(read(path))]
        if (not listed and not allow_empty) or not all(isinstance(n, str) for n in listed):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise Refused("REPORT_FORMAT", "%s: not a `cargo mutants --list --json` list" % path)
    return listed


def rust_mutants(paths, listed=None, allow_empty=False):
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
        want = _rust_listed(listed, allow_empty)
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


LEDGER_VERDICTS = {"killed", "survived", "uncompilable", "offshard", "offdiff"}


def go_ledgers(paths, allow_empty=False, expected=None):
    """One ledger per shard, lines `<mutant>\t<shard>\t<shards>\t<verdict>`: every shard sees every mutant and tests
    the ones it owns; each mutant must be owned by exactly one shard. `expected`, when given, is the number of mutants the plan
    counted (#94): the shards agreeing with each other does not show that none of them stopped early at the same place."""
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
            if allow_empty and len(paths) == 1 and not expected:   # a diff with no Go product code: no mutant was listed, nothing to judge
                return 0, 0
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
    killed = survived = tested = uncompilable = 0
    for mid, vs in verdicts.items():
        owned = [v for v in vs if v != "offshard"]
        if len(owned) != 1:
            raise Refused("INCOMPLETE_SHARDS", "mutant %s was tested by %d shards" % (mid, len(owned)))
        killed += owned[0] == "killed"
        survived += owned[0] == "survived"
        uncompilable += owned[0] == "uncompilable"
        tested += owned[0] in ("killed", "survived", "uncompilable")
    note_excluded("go", {"uncompilable": uncompilable}, tested)
    if expected is not None and tested != expected:
        raise Refused("INCOMPLETE_SHARDS", "%d Go mutants have a verdict, the plan counted %d" % (tested, expected))
    return killed, killed + survived


# go-mutesting (cmd/go-mutesting/main.go at the pinned commit) prints one line per tested mutant, `PASS` (killed),
# `FAIL` (escaped), `SKIP` (did not compile) or `UNKOWN exit code for` (sic; an error), followed by the quoted
# mutant file and its checksum; its total is killed + escaped + skipped + errors. Duplicates have no line and are not
# in the total. The exec script writes to the same stdout, so only the exact result-line shape is counted.
GO_RESULT_LINE = re.compile(r'^(PASS|FAIL|SKIP|UNKOWN exit code for) ".*" with checksum [0-9a-f]+$', re.M)
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def go_mutants(path):
    text = ANSI.sub("", read(path))
    m = re.search(r"The mutation score is [0-9.]+ \((\d+) passed, (\d+) failed, (\d+) duplicated, (\d+) skipped, total is (\d+)\)",
                  text)
    if not m:
        raise Refused("REPORT_FORMAT", "%s: no go-mutesting summary line" % path)
    lines = [r.group(1) for r in GO_RESULT_LINE.finditer(text)]
    summary = {"passed": int(m.group(1)), "failed": int(m.group(2)), "skipped": int(m.group(4)), "total": int(m.group(5))}
    duplicated = int(m.group(3))
    counted = {"passed": lines.count("PASS"), "failed": lines.count("FAIL"), "skipped": lines.count("SKIP"), "total": len(lines)}
    wrong = ["%s %d, counted %d" % (k, summary[k], counted[k]) for k in summary if summary[k] != counted[k]]
    if wrong:
        raise Refused("REPORT_INCONSISTENT", "%s: the summary line disagrees with the result lines (%s)" % (path, "; ".join(wrong)))
    # the total of go-mutesting is killed + escaped + skipped + errors: what is neither killed nor escaped is out of the denominator
    note_excluded("go", {"skipped": counted["skipped"], "errors": counted["total"] - counted["passed"] - counted["failed"] - counted["skipped"]},
                  counted["total"], duplicated)
    return counted["passed"], counted["passed"] + counted["failed"]


STRYKER_VERDICTS = {"Killed": "killed", "Survived": "survived", "NoCoverage": "survived", "Timeout": "survived",
                    "Ignored": "unresolved", "CompileError": "unviable"}
OTHER_SHARD = "another shard of this run measured this"
AGAINST = {}   # per language: mutants without a verdict that the score still counts against (information, printed under the score)


def _stryker(path):
    """(measured {id: verdict}, catalogue {id}) of one mutation-testing-elements report. A mutant that another shard measured is in the catalogue only."""
    try:
        files = json.loads(read(path))["files"]
        measured, catalogue = {}, set()
        for f in files.values():
            for m in f["mutants"]:
                mid, status = m["id"], m["status"]
                if not isinstance(mid, str) or not isinstance(status, str):
                    raise TypeError
                if mid in catalogue:
                    raise Refused("INCOMPLETE_SHARDS", "%s: mutant %s twice in one report" % (path, mid))
                catalogue.add(mid)
                if status == "Ignored" and str(m.get("statusReason", "")).startswith(OTHER_SHARD):
                    continue
                if status not in STRYKER_VERDICTS:
                    raise Refused("REPORT_INCONSISTENT", "%s: mutant %s has no known verdict (%r)" % (path, mid, status))
                measured[mid] = STRYKER_VERDICTS[status]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Refused("REPORT_FORMAT", "%s: not a mutation-testing-elements report" % path)
    return measured, catalogue


def _gamma_listed(path):
    try:
        ids = [m["id"] for m in json.loads(read(path))]
        if not ids or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise Refused("REPORT_FORMAT", "%s: not a `cargo gamma list mutants --json` list" % path)
    return set(ids)


def stryker_mutants(paths, language, listed=None, allow_empty=False):
    """The score of a sharded cargo-gamma or go-mutants run. The mutants there are come from `listed` (gamma, whose shard reports hold only their own
    mutants) or from the catalogue of the reports (go-mutants, whose every shard names every mutant): each must be measured by exactly one shard.
    A mutant nobody could judge counts against the score; an unviable one is left out."""
    reports = [_stryker(p) for p in paths]
    if listed is None and len(paths) > 1 and language == "rust":
        raise Refused("INCOMPLETE_SHARDS", "%d shard reports and no list of the mutants to check them against" % len(paths))
    want = _gamma_listed(listed) if listed else reports[0][1]
    if not listed and any(cat != want for _, cat in reports):
        raise Refused("INCOMPLETE_SHARDS", "the shards disagree on which mutants there are")
    verdicts = {}
    for measured, _ in reports:
        for mid, v in measured.items():
            if mid in verdicts:
                raise Refused("INCOMPLETE_SHARDS", "mutant %s was measured by two shards" % mid)
            verdicts[mid] = v
    missing, extra = want - set(verdicts), set(verdicts) - want
    if (missing or extra) and not (allow_empty and not want and not verdicts):
        raise Refused("INCOMPLETE_SHARDS", "%d not measured, %d not in the list (first: %s)" % (len(missing), len(extra), sorted(missing | extra)[0]))
    counts = {k: sum(1 for v in verdicts.values() if v == k) for k in ("killed", "survived", "unresolved", "unviable")}
    note_excluded(language, {"unviable": counts["unviable"]}, len(verdicts))
    if counts["unresolved"]:
        AGAINST[language] = AGAINST.get(language, 0) + counts["unresolved"]
    return counts["killed"], counts["killed"] + counts["survived"] + counts["unresolved"]


def judge(name, measure, path):
    covered, total = measure()
    if total == 0:
        raise Refused("MEASUREMENT_EMPTY", "%s: %s" % (name, path))
    pct = Fraction(100 * covered, total)
    ok = pct > THRESHOLD
    return ok, "%-22s %d/%d = %.2f%%" % (name, covered, total, float(pct))


DIFF_SMALL = 10  # a diff with fewer mutants than this has to have every one of them killed (docs/DELTA-DIFF-GATE.md, section 2)


def judge_diff(name, measure, path):
    """The mutation gate of a pull request that was measured on the mutants of its diff only: more than 90% killed, all of them when
    there are fewer than DIFF_SMALL, and no mutants in the diff is green and says so. The threshold is the same one."""
    killed, total = measure()
    if total == 0:
        return True, "%-22s no mutants in the diff" % name
    if total < DIFF_SMALL:
        ok = killed == total
        return ok, "%-22s %d/%d (fewer than %d in the diff: all must be killed)" % (name, killed, total, DIFF_SMALL)
    pct = Fraction(100 * killed, total)
    return pct > THRESHOLD, "%-22s %d/%d = %.2f%% (in the diff)" % (name, killed, total, float(pct))


def check(args):
    diff = getattr(args, "diff", False)
    gamma, gom = getattr(args, "gamma_reports", None), getattr(args, "gomutants_reports", None)
    if gamma:
        rust_gate = (lambda: stryker_mutants(gamma, "rust", getattr(args, "gamma_listed", None), allow_empty=diff), " ".join(gamma))
    else:
        rust_m = args.rust_mutants if isinstance(args.rust_mutants, list) else [args.rust_mutants]
        rust_gate = (lambda: rust_mutants(rust_m, args.rust_listed, allow_empty=diff), " ".join(rust_m))
    if gom:
        go_gate = (lambda: stryker_mutants(gom, "go", allow_empty=diff), " ".join(gom))
    elif args.go_ledgers:
        go_gate = (lambda: go_ledgers(args.go_ledgers, allow_empty=diff, expected=getattr(args, "go_expected", None)), " ".join(args.go_ledgers))
    else:
        go_gate = (lambda: go_mutants(args.go_mutants), args.go_mutants)
    gates = [("rust line coverage", lambda: rust_lines(args.rust_lines), args.rust_lines),
             ("go statement coverage", lambda: go_lines(args.go_lines), args.go_lines),
             ("rust mutation score", *rust_gate),
             ("go mutation score", *go_gate)]
    failed = 0
    EXCLUDED.clear()
    AGAINST.clear()
    for name, measure, path in gates:
        try:
            ok, line = (judge_diff if diff and "mutation" in name else judge)(name, measure, path)
        except Refused as r:
            print("FAIL %-22s %s: %s" % (name, r.reason, r.detail))
            failed += 1
            continue
        print(("ok   " if ok else "FAIL ") + line + ("" if ok else "  BELOW_THRESHOLD"))
        failed += 0 if ok else 1
        if "mutation" in name:
            lang = "rust" if name.startswith("rust") else "go"
            note = excluded_line(lang)
            if note:
                print(note)
            if AGAINST.get(lang):
                print("     counted against the score, no verdict (flaky or inconclusive): %d mutants" % AGAINST[lang])
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


def _mut(caught, missed, timeout=0, unviable=5, claim=None, extra=()):
    """A cargo-mutants outcomes.json: one outcome per mutant and the totals that go with them, unless `claim` overrides some;
    `extra` adds outcomes with the given summaries that no total counts."""
    kinds = ["CaughtMutant"] * caught + ["MissedMutant"] * missed + ["Timeout"] * timeout + ["Unviable"] * unviable + list(extra)
    outs = [{"scenario": "Baseline", "summary": "Success"}] + [
        {"scenario": {"Mutant": {"name": "src/a.rs:%d" % i}}, "summary": k} for i, k in enumerate(kinds)]
    d = {"caught": caught, "missed": missed, "timeout": timeout, "unviable": unviable, "success": 0, "total_mutants": len(kinds),
         "outcomes": outs}
    d.update(claim or {})
    return json.dumps(d)


def _gomut(passed, failed, skipped=3, claim=None, errors=0, duplicated=0):
    """go-mutesting stdout: a result line per mutant, exec-script noise, and the summary line, unless `claim` overrides it."""
    rows = ['%s "/tmp/go-mutesting-1/a.go.%d" with checksum %032x' % (k, i, i)
            for i, k in enumerate(["PASS"] * passed + ["FAIL"] * failed + ["SKIP"] * skipped + ["UNKOWN exit code for"] * errors)]
    s = dict(passed=passed, failed=failed, skipped=skipped, total=len(rows))
    s.update(claim or {})
    return "PASS\nok  \tx\t0.1s\n%s\nThe mutation score is 0.0 (%d passed, %d failed, %d duplicated, %d skipped, total is %d)\n" % (
        "\n".join(rows), s["passed"], s["failed"], duplicated, s["skipped"], s["total"])


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
        ("a foreign /src/ directory is not measured code", dict(rl=_rust_cov(0, 100).replace("]}]}", ',{"filename":"/x/dep/src/lib.rs","summary":{"lines":{"covered":2000,"count":2000}}}]}]}')), "BELOW_THRESHOLD"),
        ("a rust/src path of a nested crate is measured code", dict(rl=_rust_cov(95, 100, name="/home/runner/work/r/r/rust/src/a.rs")), None),
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
        ("mutants json without the outcomes", dict(rm=json.dumps({"caught": 95, "missed": 5, "timeout": 0, "unviable": 0, "total_mutants": 100})),
         "REPORT_FORMAT"),
        ("mutants json with a boolean counter", dict(rm=_mut(95, 1, claim={"missed": True})), "REPORT_FORMAT"),
        ("rust totals claim 500 caught of one mutant", dict(rm=_mut(0, 1, unviable=0, claim={"caught": 500})), "REPORT_INCONSISTENT"),
        ("rust totals swap a missed for a caught", dict(rm=_mut(94, 6, claim={"caught": 95, "missed": 5})), "REPORT_INCONSISTENT"),
        ("rust total_mutants is not the outcome count", dict(rm=_mut(95, 5, claim={"total_mutants": 99})), "REPORT_INCONSISTENT"),
        ("rust unviable total disagrees", dict(rm=_mut(95, 5, claim={"unviable": 0})), "REPORT_INCONSISTENT"),
        ("rust mutant without a verdict (Failure)", dict(rm=_mut(95, 5, extra=["Failure"], claim={"total_mutants": 106})),
         "REPORT_INCONSISTENT"),
        ("rust mutant without a verdict (Success)", dict(rm=_mut(95, 5, extra=["Success"], claim={"total_mutants": 106})),
         "REPORT_INCONSISTENT"),
        ("go summary claims passes it has no lines for", dict(gm=_gomut(10, 1, claim={"passed": 500, "total": 504})), "REPORT_INCONSISTENT"),
        ("go summary swaps a failed for a passed", dict(gm=_gomut(94, 6, claim={"passed": 95, "failed": 5})), "REPORT_INCONSISTENT"),
        ("go total is not the number of result lines", dict(gm=_gomut(95, 5, claim={"total": 90})), "REPORT_INCONSISTENT"),
        ("go colour codes around the result lines", dict(gm=_gomut(95, 5).replace("PASS \"", "\x1b[97;42mPASS\x1b[0m \"")), None),
        ("go an error line counts in the total", dict(gm=_gomut(95, 5, claim={"total": 104}) + "UNKOWN exit code for \"/t/a.go.99\" with checksum ab\n"),
         None),
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
    # the share of mutants the score leaves out of its denominator is printed under the score, per language, and decides nothing; for Go it counts what is
    # neither killed nor escaped in the total of go-mutesting (skipped AND errors), and names the duplicates next to the total, not inside it
    for title, over, want in (
            ("the share left out of the denominator is printed", {},
             ["not in the denominator: 5 of 105 mutants (4.76%) (unviable 5)", "not in the denominator: 3 of 103 mutants (2.91%) (skipped 3)"]),
            ("Go errors and duplicates are not lost from it", dict(gm=_gomut(95, 5, skipped=3, errors=1, duplicated=2)),
             ["not in the denominator: 4 of 104 mutants (3.85%) (skipped 3, errors 1); 2 more dropped as duplicates, not in that total"])):
        with tempfile.TemporaryDirectory() as d:
            files = {k: _w(d, names[k], v) for k, v in dict(good, **over).items()}
            ns = argparse.Namespace(rust_lines=files["rl"], go_lines=files["gl"], rust_mutants=[files["rm"]], rust_listed=None,
                                    go_mutants=files["gm"], go_ledgers=None)
            rc, text = _capture(check, ns)
        ok = rc == 0 and all(w in text for w in want)
        bad += 0 if ok else 1
        print("  %s %-48s -> %s" % ("ok " if ok else "BAD", title, "printed" if ok else "missing or wrong"))
    # sharded measurements: the merged score, and every way a shard set can be incomplete
    def rshard(names, missed=()):
        outs = [{"scenario": "Baseline", "summary": "Success"}] + [
            {"scenario": {"Mutant": {"name": n}}, "summary": "MissedMutant" if n in missed else "CaughtMutant"} for n in names]
        return json.dumps({"caught": len(names) - len(missed), "missed": len(missed), "timeout": 0, "unviable": 0,
                           "total_mutants": len(names), "outcomes": outs})

    def listing(names):
        return json.dumps([{"name": n, "file": "src/a.rs"} for n in names])

    def ledger(k, n, owned, others, verdict="killed"):
        rows = ["%s\t%d\t%d\t%s" % (m, k, n, verdict if m in owned else "offshard") for m in others]
        return "\n".join(rows) + "\n"

    allr = ["m%02d" % i for i in range(20)]
    a, b = allr[:10], allr[10:]
    gm = ["g%02d" % i for i in range(30)]
    parts = [gm[0:10], gm[10:20], gm[20:30]]

    def sharded(title, want, rust=None, listed=None, ledgers=None, go_expected=None):
        nonlocal bad
        with tempfile.TemporaryDirectory() as d:
            rfiles = [_w(d, "r%d" % i, t) for i, t in enumerate(rust if rust is not None else [rshard(a), rshard(b)])]
            lf = _w(d, "list", listed if listed is not None else listing(allr))
            gfiles = [_w(d, "g%d" % i, t) for i, t in enumerate(ledgers if ledgers is not None else
                      [ledger(k, 3, parts[k], gm) for k in range(3)])]
            ns = argparse.Namespace(rust_lines=_w(d, "rl", _rust_cov(95, 100)), go_lines=_w(d, "gl", _go_cov(95, 100)),
                                    rust_mutants=rfiles, rust_listed=lf, go_mutants=None, go_ledgers=gfiles, go_expected=go_expected)
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
    sharded("sharded: a rust shard's totals claim more than its outcomes", "REPORT_INCONSISTENT",
            rust=[json.dumps(dict(json.loads(rshard(a, missed=a[:3])), caught=500)), rshard(b)])
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
    sharded("sharded: the go ledgers give the planned count a verdict", None, go_expected=30)
    sharded("sharded: every go shard stopped at the same place", "INCOMPLETE_SHARDS",
            ledgers=[ledger(k, 3, [m for m in parts[k] if m < "g25"], [m for m in gm if m < "g25"]) for k in range(3)], go_expected=30)
    sharded("sharded: the merged go score is below 90%", "BELOW_THRESHOLD",
            ledgers=[ledger(k, 3, parts[k], gm, verdict="survived") for k in range(3)])
    # the diff gate (docs/DELTA-DIFF-GATE.md): the same threshold on the mutants of the diff, a rule for few of them, and green for none
    def diffcase(title, want, rm=None, gm=None, rl=None, ledgers=None, rust_listed=None, go_expected=None):
        nonlocal bad
        with tempfile.TemporaryDirectory() as d:
            gfiles = [_w(d, "g%d" % i, t) for i, t in enumerate(ledgers)] if ledgers is not None else None
            ns = argparse.Namespace(rust_lines=_w(d, "rl", rl or _rust_cov(95, 100)), go_lines=_w(d, "gl", _go_cov(95, 100)),
                                    rust_mutants=[_w(d, "rm", rm or _mut(0, 0))], rust_listed=_w(d, "list", rust_listed) if rust_listed else None,
                                    go_mutants=None if gfiles else _w(d, "gm", gm or _gomut(0, 0)), go_ledgers=gfiles, diff=True,
                                    go_expected=go_expected)
            rc, text = _capture(check, ns)
        got = None if rc == 0 else next((r for r in REASONS if r in text), "?")
        seen.add(got)
        ok = got == want
        bad += 0 if ok else 1
        print("  %s %-48s -> %s" % ("ok " if ok else "BAD", title, got or "pass"))

    def dledger(killed, survived, off):
        rows = ["k%02d\t0\t1\tkilled" % i for i in range(killed)] + ["s%02d\t0\t1\tsurvived" % i for i in range(survived)]
        rows += ["o%02d\t0\t1\toffdiff" % i for i in range(off)]
        return ["\n".join(rows) + "\n"]

    diffcase("diff: no mutants in the diff is green", None)
    diffcase("diff: fewer than 10, all killed", None, rm=_mut(7, 0), gm=_gomut(3, 0))
    diffcase("diff: fewer than 10, one survives (rust)", "BELOW_THRESHOLD", rm=_mut(7, 1))
    diffcase("diff: fewer than 10, one survives (go)", "BELOW_THRESHOLD", gm=_gomut(8, 1))
    diffcase("diff: 11 mutants, 10 killed is 90.9%", None, rm=_mut(10, 1))
    diffcase("diff: 20 mutants, 18 killed is exactly 90%", "BELOW_THRESHOLD", rm=_mut(18, 2))
    diffcase("diff: the line coverage is not relaxed", "BELOW_THRESHOLD", rl=_rust_cov(50, 100))
    diffcase("diff: go ledger, 5 in the diff all killed, 25 outside", None, ledgers=dledger(5, 0, 25))
    diffcase("diff: go ledger, one of 5 in the diff survives", "BELOW_THRESHOLD", ledgers=dledger(4, 1, 25))
    diffcase("diff: go ledger, every mutant outside the diff", None, ledgers=dledger(0, 0, 30))
    diffcase("diff: go ledger is empty (no Go product code in the diff)", None, ledgers=[""])
    diffcase("diff: go ledger is empty but the plan counted mutants", "MEASUREMENT_EMPTY", ledgers=[""], go_expected=3)
    diffcase("diff: go ledger, a mutant twice", "INCOMPLETE_SHARDS", ledgers=["a\t0\t1\tkilled\na\t0\t1\toffdiff\n"])
    diffcase("diff: rust list is empty and nothing ran", None, rm=rshard([]), rust_listed="[]")
    diffcase("diff: rust ran a mutant that is not listed", "INCOMPLETE_SHARDS",
             rm=rshard(["m00"]), rust_listed="[]")
    # the reports of cargo-gamma and go-mutants (the pull request's full run): every way they can mislead, and the sharded completeness
    fast_cases = []

    def sreport(statuses, start=0, others=(), other_reason=OTHER_SHARD + " (shard 0 of 2)"):
        mutants = [{"id": "x%03d" % (start + i), "status": st} for i, st in enumerate(statuses)]
        mutants += [{"id": "x%03d" % i, "status": "Ignored", "statusReason": other_reason} for i in others]
        return json.dumps({"schemaVersion": "2", "files": {"a.rs": {"mutants": mutants}}})

    def slisting(n):
        return json.dumps([{"id": "x%03d" % i} for i in range(n)])

    K, S, I = "Killed", "Survived", "Ignored"
    ok95 = sreport([K] * 95 + [S] * 5)

    def fast(title, want, gamma=None, listed=None, gom=None, expect=()):
        nonlocal bad
        with tempfile.TemporaryDirectory() as d:
            ns = argparse.Namespace(rust_lines=_w(d, "rl", _rust_cov(95, 100)), go_lines=_w(d, "gl", _go_cov(95, 100)),
                                    gamma_reports=[_w(d, "g%d" % i, t) for i, t in enumerate(gamma or [ok95])],
                                    gamma_listed=_w(d, "gl2", listed) if listed else None,
                                    gomutants_reports=[_w(d, "o%d" % i, t) for i, t in enumerate(gom or [ok95])],
                                    rust_mutants=None, go_mutants=None, go_ledgers=None)
            rc, text = _capture(check, ns)
        got = None if rc == 0 else next((r for r in REASONS if r in text), "?")
        seen.add(got)
        good_ = got == want and all(e in text for e in expect)
        bad += 0 if good_ else 1
        fast_cases.append(title)
        print("  %s %-48s -> %s" % ("ok " if good_ else "BAD", title, got or "pass"))

    half = lambda lo, hi, **kw: sreport([K] * (hi - lo), start=lo, others=[i for i in range(100) if not lo <= i < hi], **kw)
    fast("gamma: one complete report", None)
    fast("gamma: exactly 90% is red", "BELOW_THRESHOLD", gamma=[sreport([K] * 90 + [S] * 10)])
    fast("gamma: flaky mutants count against the score", "BELOW_THRESHOLD", gamma=[sreport([K] * 85 + [I] * 15)])
    fast("gamma: flaky mutants are named under a passing score", None, gamma=[sreport([K] * 95 + [I] * 5)],
         expect=["counted against the score, no verdict (flaky or inconclusive): 5 mutants"])
    fast("gamma: unviable mutants are left out and named", None, gamma=[sreport([K] * 95 + [S] * 5 + ["CompileError"] * 40)],
         expect=["not in the denominator: 40 of 140 mutants (28.57%) (unviable 40)"])
    fast("gamma: a verdict nobody knows (Pending)", "REPORT_INCONSISTENT", gamma=[sreport([K] * 95 + ["Pending"] * 5)])
    fast("gamma: the report is not JSON", "REPORT_FORMAT", gamma=["oops"])
    fast("gamma: the report has no mutants key", "REPORT_FORMAT", gamma=[json.dumps({"files": {"a.rs": {}}})])
    fast("gamma: a measurement of nothing", "MEASUREMENT_EMPTY", gamma=[sreport([])])
    fast("gamma: a mutant twice in one report", "INCOMPLETE_SHARDS", gamma=[sreport([K] * 5 + [K]).replace("x005", "x004")])
    fast("gamma shards: complete", None, gamma=[sreport([K] * 48 + [S] * 2), sreport([K] * 48 + [S] * 2, start=50)], listed=slisting(100))
    fast("gamma shards: a shard did not run", "INCOMPLETE_SHARDS", gamma=[sreport([K] * 50)], listed=slisting(100))
    fast("gamma shards: a mutant measured by two shards", "INCOMPLETE_SHARDS",
         gamma=[sreport([K] * 51), sreport([K] * 50, start=50)], listed=slisting(100))
    fast("gamma shards: a mutant that is not listed", "INCOMPLETE_SHARDS",
         gamma=[sreport([K] * 50), sreport([K] * 50, start=50), sreport([K], start=500)], listed=slisting(100))
    fast("gamma shards: several reports and no list", "INCOMPLETE_SHARDS", gamma=[sreport([K] * 50), sreport([K] * 50, start=50)])
    fast("gamma shards: the list is not a list of mutants", "REPORT_FORMAT", gamma=[ok95], listed="{}")
    fast("go-mutants shards: complete", None, gom=[half(0, 50), half(50, 100)])
    fast("go-mutants shards: a shard did not run", "INCOMPLETE_SHARDS", gom=[half(0, 50)])
    fast("go-mutants shards: a mutant measured by none", "INCOMPLETE_SHARDS", gom=[half(0, 50), half(50, 99)])
    fast("go-mutants shards: the shards disagree on the mutants", "INCOMPLETE_SHARDS",
         gom=[half(0, 50), half(50, 100).replace('{"id": "x099", "status": "Killed"}', '{"id": "x777", "status": "Killed"}')])
    fast("go-mutants shards: a mutant measured by two shards", "INCOMPLETE_SHARDS", gom=[half(0, 51), half(50, 100)])
    fast("go-mutants: timeouts are not kills", "BELOW_THRESHOLD", gom=[sreport([K] * 80 + ["Timeout"] * 20)])
    fast("go-mutants: an inconclusive mutant counts against", "BELOW_THRESHOLD",
         gom=[sreport([K] * 85 + [I] * 15).replace(OTHER_SHARD, "x")])
    fast("go-mutants: the other shards' mutants are not verdicts", None, gom=[half(0, 100)])
    missing = set(REASONS) - seen
    if missing:
        print("  BAD reasons without a fault: %s" % sorted(missing))
        bad += 1
    print("self-test: %d faults, %d of %d reasons covered, the gate can fail for every reason" % (len(cases) + 1 + 2 + 17 + 15 + len(fast_cases), len(set(REASONS) & seen), len(REASONS))
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
    c.add_argument("--rust-mutants", nargs="+")
    c.add_argument("--gamma-reports", nargs="+")
    c.add_argument("--gamma-listed")
    c.add_argument("--gomutants-reports", nargs="+")
    c.add_argument("--rust-listed")
    c.add_argument("--diff", action="store_true", help="the mutation scores are those of the mutants in the diff of a pull request")
    group = c.add_mutually_exclusive_group()
    group.add_argument("--go-mutants")
    group.add_argument("--go-ledgers", nargs="+")
    c.add_argument("--go-expected", type=int, help="with --go-ledgers: the number of Go mutants the plan counted")
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "check":
        if bool(a.rust_mutants) == bool(a.gamma_reports):
            ap.error("exactly one of --rust-mutants and --gamma-reports")
        if sum(bool(x) for x in (a.go_mutants, a.go_ledgers, a.gomutants_reports)) != 1:
            ap.error("exactly one of --go-mutants, --go-ledgers and --gomutants-reports")
    return check(a) if a.cmd == "check" else selftest()


if __name__ == "__main__":
    sys.exit(main())
