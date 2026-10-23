#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# The cheap checks while writing; CI is the authority for the rest (docs/DELTA-DIFF-GATE.md, section 9).
#
#   [TESTS=go-regex] [RUST_TESTS=name] [JOBS=n] [SKIP_MUTATION=1] tools/quick-check.sh [BASE]
#
#   1. the texts of the seeded defects still match the code (seconds; a stale text would fail CI's seeded-defects step ten minutes in)
#   2. the mutants of your working tree (staged, unstaged, new files), tools/diff-mutation-local.sh WORKTREE=1; with TESTS / RUST_TESTS
#      only the tests that match run, so a survivor must be rechecked without them before it is believed (a killed mutant stays killed)
# It does not run the test suites, the coverage gate, the seeded defects or the fuzzing: those are CI's.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 2
python3 tools/seeded_defects.py all --stale-only || exit 1
[ -n "${SKIP_MUTATION:-}" ] && exit 0
FORCE_DIFF=1 WORKTREE=1 exec tools/diff-mutation-local.sh "${1:-origin/main}"
