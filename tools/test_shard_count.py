# SPDX-License-Identifier: Apache-2.0
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shard_count  # noqa: E402

B = {"max_mutants_per_shard": 100, "budget_seconds": 600, "fixed_seconds": 180, "max_shards": 24,
     "rust": {"seconds_per_mutant": 6}, "go": {"seconds_per_mutant": 2}}


class ShardCount(unittest.TestCase):
    def test_the_measured_state_of_72(self):
        self.assertEqual(shard_count.shard_count(B, "rust", 1059), 16)   # the time bound (70 mutants in 420 s) is stricter than 100
        self.assertEqual(shard_count.shard_count(B, "go", 2045), 21)     # the size bound is stricter

    def test_each_bound_alone(self):
        self.assertEqual(shard_count.shard_count(B, "rust", 100), 2)      # 600 s of work does not fit 420 s
        self.assertEqual(shard_count.shard_count(B, "go", 100), 1)
        self.assertEqual(shard_count.shard_count(B, "go", 101), 2)        # one over the size bound

    def test_the_count_is_at_least_one_and_at_most_the_cap(self):
        self.assertEqual(shard_count.shard_count(B, "go", 0), 1)
        self.assertEqual(shard_count.shard_count(B, "rust", 100000), 24)

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            shard_count.shard_count(B, "go", -1)
        with self.assertRaises(ValueError):
            shard_count.shard_count(dict(B, fixed_seconds=600), "go", 10)

    def test_the_committed_budget_is_complete(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "shard_budget.json"), encoding="utf-8") as fh:
            b = json.load(fh)
        for k in ("max_mutants_per_shard", "budget_seconds", "fixed_seconds", "max_shards"):
            self.assertIsInstance(b[k], int, k)
        self.assertGreaterEqual(shard_count.shard_count(b, "rust", 1059), 11)


if __name__ == "__main__":
    unittest.main()
