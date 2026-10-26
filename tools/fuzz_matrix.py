#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Which fuzz targets a run needs (docs/DELTA-DIFF-GATE.md, 9.2): the matrix of the `fuzz` job.

    fuzz_matrix.py --mode full|diff|light [--rust-ranges N] [--go-ranges N] [--changed FILE ...] [--github-output FILE]

* full: every target; light: none (a push to main runs the cheap checks only); diff: the targets of a language whose product code the
  diff changes (the ranges of tools/diff_gate.py), whose fuzz targets or seed inputs the diff touches, or when the shared corpus or testdata
  change; nothing else. The unit is one target, one job, so the targets run side by side and a failure names its target.
* A language that is not run is reported `not run`, never as passed (the report goes to stdout and the job summary).
The tables below are checked against the code by tools/test_fuzz_matrix.py: a target that is added to the code and not to the table fails it.
"""
import argparse
import json
import re
import sys

GO_TARGETS = ("Parse", "Validate", "Access", "ExpandCollapse", "ApplyDefaults", "Normalize", "ResolvePolicy", "SourceSpan", "EditScalar", "Diff", "Patch")
RUST_TARGETS = ("parse", "validate", "access", "expand", "defaults", "normalize", "resolve", "source_span", "edit_scalar", "diff", "patch")
GO_OWN = re.compile(r"^go/(fuzz_test\.go$|testdata/fuzz/)")
RUST_OWN = re.compile(r"^rust/fuzz/")
SHARED = re.compile(r"^(corpus|testdata)/")


def matrix(mode, rust_ranges=0, go_ranges=0, changed=()):
    if mode == "light":
        return [], ["go: not run (a push to main runs the cheap checks only)", "rust: not run (a push to main runs the cheap checks only)"]
    shared = any(SHARED.match(c) for c in changed)
    run_go = mode == "full" or go_ranges > 0 or shared or any(GO_OWN.match(c) for c in changed)
    run_rust = mode == "full" or rust_ranges > 0 or shared or any(RUST_OWN.match(c) for c in changed)
    out, report = [], []
    if run_go:
        out += [{"lang": "go", "name": n} for n in GO_TARGETS]
        report.append("go: %d targets" % len(GO_TARGETS))
    else:
        report.append("go: not run (the diff touches no Go product code, no Go fuzz target and no shared corpus)")
    if run_rust:
        out += [{"lang": "rust", "name": n} for n in RUST_TARGETS]
        report.append("rust: %d targets" % len(RUST_TARGETS))
    else:
        report.append("rust: not run (the diff touches no Rust product code, no Rust fuzz target and no shared corpus)")
    return out, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=("full", "diff", "light"))
    ap.add_argument("--rust-ranges", type=int, default=0)
    ap.add_argument("--go-ranges", type=int, default=0)
    ap.add_argument("--changed", nargs="*", default=[])
    ap.add_argument("--github-output")
    a = ap.parse_args()
    out, report = matrix(a.mode, a.rust_ranges, a.go_ranges, a.changed)
    for line in report:
        print("fuzz " + line)
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as fh:
            fh.write("fuzz_matrix=%s\n" % json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
