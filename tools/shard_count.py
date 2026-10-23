#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""How many shards a mutation run is cut into: a shard has at most `max_mutants_per_shard` mutants and takes at most `budget_seconds`,
whichever bound is reached first (docs/DELTA-DIFF-GATE.md, 9.2).

    shard_count.py --lang rust|go --mutants N [--budget tools/shard_budget.json] [--github-output FILE]

    shards = max(ceil(N / max_mutants), ceil(N * seconds_per_mutant / (budget - fixed)))   at least 1, at most max_shards

It prints the count and the matrix list ([0, 1, ...]); with --github-output it writes `<lang>_n` and `<lang>_shards`. It decides only how the
work is cut. The gate still requires every mutant in exactly one shard's report, whatever the count is.
"""
import argparse
import json
import math
import sys


def shard_count(budget, lang, mutants):
    if mutants < 0:
        raise ValueError("a negative number of mutants")
    usable = budget["budget_seconds"] - budget["fixed_seconds"]
    if usable <= 0:
        raise ValueError("the fixed cost is not smaller than the budget")
    by_size = math.ceil(mutants / budget["max_mutants_per_shard"])
    by_time = math.ceil(mutants * budget[lang]["seconds_per_mutant"] / usable)
    return max(1, min(max(by_size, by_time), budget["max_shards"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", required=True, choices=("rust", "go"))
    ap.add_argument("--mutants", required=True, type=int)
    ap.add_argument("--budget", default="tools/shard_budget.json")
    ap.add_argument("--github-output")
    a = ap.parse_args()
    with open(a.budget, encoding="utf-8") as fh:
        budget = json.load(fh)
    n = shard_count(budget, a.lang, a.mutants)
    shards = list(range(n))
    print("%s: %d mutants -> %d shards of about %d" % (a.lang, a.mutants, n, math.ceil(a.mutants / n) if n else 0))
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as fh:
            fh.write("%s_n=%d\n%s_shards=%s\n" % (a.lang, n, a.lang, json.dumps(shards)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
