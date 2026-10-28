#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The number of Go mutants in the diff, from the output of go-mutesting in list mode (CIC_MUTATION_LIST_ONLY=1, tools/mutation_diff_exec.sh).

    go_list_count.py FILE      prints the number; exit 1 and nothing on stdout when the output cannot be believed

In list mode every mutant of the diff answers PASS (exit 0) and every other one SKIP (exit 2); a mutant the executor could not place answers exit 3, which
go-mutesting prints as `UNKOWN exit code for`. A count of zero is believed only from a run that ended with the summary line of go-mutesting and has no such line:
a crash prints no summary and counts nothing, and that is not "no mutants in the diff" (the old `grep -c ... || true` could not tell them apart).
"""
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*m")
RESULT = re.compile(r'^(PASS|FAIL|SKIP|UNKOWN exit code for) ".*" with checksum [0-9a-f]+$', re.M)
SUMMARY = re.compile(r"The mutation score is [0-9.]+ \((\d+) passed, (\d+) failed, (\d+) duplicated, (\d+) skipped, total is (\d+)\)")


def count(text):
    text = ANSI.sub("", text)
    m = SUMMARY.search(text)
    if not m:
        raise ValueError("no summary line of go-mutesting: the run did not finish")
    passed, failed, _, skipped, total = (int(x) for x in m.groups())
    lines = [r.group(1) for r in RESULT.finditer(text)]
    if "UNKOWN exit code for" in lines:
        raise ValueError("%d mutants could not be placed (the executor answered with an error)" % lines.count("UNKOWN exit code for"))
    if failed:
        raise ValueError("%d mutants failed in a mode that runs no test" % failed)
    if (passed, skipped, total) != (lines.count("PASS"), lines.count("SKIP"), len(lines)):
        raise ValueError("the summary (%d passed, %d skipped, total %d) disagrees with the %d result lines" % (passed, skipped, total, len(lines)))
    return passed


def main():
    try:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
            print(count(fh.read()))
    except (IndexError, OSError, ValueError) as e:
        print("go_list_count: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
