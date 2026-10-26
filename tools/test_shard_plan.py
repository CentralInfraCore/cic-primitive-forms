"""Tests of tools/shard_plan.py, and the seeded defects that show them able to fail.

    python3 -m unittest tools/test_shard_plan.py        (the seeded part copies the tool and breaks it, one change at a time)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shard_plan as sp  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def jdump(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def jload(path):
    return json.loads(read(path))


def mutant(f, line, what, col=5):
    return "%s:%d:%d: %s" % (f, line, col, what)


NAMES = [mutant("src/a.rs", i, "replace f%d -> i64 with %d" % (i % 7, i % 3)) for i in range(1, 60)]
NAMES += [mutant("src/b.rs", 10 + i, "replace == with !=") for i in range(6)]  # the same text six times


def table_for(names, slow=()):
    k = sp.keys(names)
    return {k[n]: (120.0 if n in slow else 5.0) for n in names}


class PlanTests(unittest.TestCase):
    def test_every_mutant_goes_to_exactly_one_shard(self):
        for shards in (1, 2, 4, 6, 9, 200):
            owner, loads, _ = sp.deal(NAMES, table_for(NAMES), shards)
            self.assertEqual(sorted(owner), sorted(NAMES))
            self.assertTrue(all(0 <= j < shards for j in owner.values()))
            self.assertEqual(len(loads), shards)

    def test_the_regexes_of_the_shards_select_every_mutant_once(self):
        owner, _, _ = sp.deal(NAMES, table_for(NAMES), 5)
        regexes = [re.compile(sp.regex_of([n for n, j in owner.items() if j == k])) for k in range(5)]
        for n in NAMES:
            self.assertEqual(sum(1 for r in regexes if r.search(n)), 1, n)

    def test_the_regex_matches_the_whole_name_only(self):
        r = re.compile(sp.regex_of([mutant("src/a.rs", 1, "replace f -> i64 with 0")]))
        self.assertTrue(r.search(mutant("src/a.rs", 1, "replace f -> i64 with 0")))
        self.assertFalse(r.search(mutant("src/a.rs", 1, "replace f -> i64 with 0") + "1"))
        self.assertFalse(r.search("x" + mutant("src/a.rs", 1, "replace f -> i64 with 0")))

    def test_names_full_of_regex_characters_are_taken_literally(self):
        odd = [mutant("src/x.rs", 1, "replace a.b -> Result<(), E> with Ok(())"), mutant("src/x.rs", 2, "replace + with * in [T; N] | &x ^ $y {z} ~ #"),
               mutant("src/x.rs", 3, "replace a.b -> Result<(), E> with Err(())")]
        for n in odd:
            r = re.compile(sp.regex_of([n]))
            self.assertTrue(r.search(n))
            self.assertEqual([m for m in odd if r.search(m)], [n])

    def test_the_answer_does_not_depend_on_the_order_of_the_list(self):
        t = table_for(NAMES, slow=NAMES[:9])
        a = sp.deal(NAMES, t, 6)
        b = sp.deal(list(reversed(NAMES)), t, 6)
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])

    def test_the_slow_mutants_are_spread_not_piled_up(self):
        slow = NAMES[:9]
        owner, loads, _ = sp.deal(NAMES, table_for(NAMES, slow=slow), 6)
        per_shard = [sum(1 for n in slow if owner[n] == j) for j in range(6)]
        self.assertLessEqual(max(per_shard), 2)
        total = sum(loads)
        self.assertLessEqual(max(loads), total / 6 + 120.0)       # the bound LPT gives: the mean plus the longest item
        self.assertLess(max(loads), 0.6 * max(sp.deal(NAMES, table_for(NAMES, slow=slow), 1)[1]))

    def test_longest_first_is_optimal_where_the_other_orders_are_not(self):
        costs = [7, 7, 6, 6, 5, 5, 4, 4, 4]                           # 48 seconds on 3 shards: 16 each is possible, and LPT finds it
        names = [mutant("src/c.rs", i + 1, "replace g%d -> i64 with 0" % i) for i in range(len(costs))]
        k = sp.keys(names)
        _, loads, _ = sp.deal(names, {k[n]: float(c) for n, c in zip(names, costs)}, 3)
        self.assertEqual(max(loads), 16.0)

    def test_round_robin_would_have_piled_them_up(self):
        # why this tool exists: nine slow mutants on every sixth position land on one shard
        slow = [n for i, n in enumerate(NAMES) if i % 6 == 1][:9]
        rr = [sum(120.0 if n in slow else 5.0 for i, n in enumerate(NAMES) if i % 6 == j) for j in range(6)]
        _, loads, _ = sp.deal(NAMES, table_for(NAMES, slow=slow), 6)
        self.assertGreater(max(rr), 2 * max(loads))

    def test_a_mutant_without_a_measured_cost_is_counted_and_still_dealt(self):
        t = table_for(NAMES)
        del t[sp.keys(NAMES)[NAMES[3]]]
        owner, _, unknown = sp.deal(NAMES, t, 4)
        self.assertEqual(unknown, 1)
        self.assertIn(NAMES[3], owner)

    def test_costs_of_mutants_that_are_gone_are_ignored(self):
        t = table_for(NAMES)
        t["src/gone.rs: replace x#1"] = 9999.0
        self.assertEqual(sp.deal(NAMES, t, 3)[0], sp.deal(NAMES, table_for(NAMES), 3)[0])

    def test_a_line_that_moves_keeps_the_cost(self):
        k0 = sp.keys(NAMES)
        moved = [re.sub(r":(\d+):", lambda m: ":%d:" % (int(m.group(1)) + 40), n, count=1) for n in NAMES]
        self.assertEqual(sorted(k0.values()), sorted(sp.keys(moved).values()))

    def test_the_same_text_in_one_file_gets_distinct_keys(self):
        k = sp.keys([n for n in NAMES if "src/b.rs" in n])
        self.assertEqual(len(set(k.values())), 6)

    def test_bad_input_is_refused(self):
        with self.assertRaises(sp.PlanError):
            sp.deal([], {}, 3)
        with self.assertRaises(sp.PlanError):
            sp.deal(NAMES, {}, 0)
        with self.assertRaises(sp.PlanError):
            sp.deal(NAMES + NAMES[:1], {}, 2)
        with self.assertRaises(sp.PlanError):
            sp.regex_of([])
        with self.assertRaises(sp.PlanError):
            sp.parse_name("no position here")

    def run_cli(self, *args):
        return sp.main(list(args))

    def test_the_cli_round_trip_and_the_stop_on_a_different_selection(self):
        with tempfile.TemporaryDirectory() as d:
            p = lambda n: os.path.join(d, n)
            jdump(p("list.json"), [{"name": n} for n in NAMES])
            outcomes = {"outcomes": [{"scenario": "Baseline", "phase_results": [{"duration": 3}]}] +
                        [{"scenario": {"Mutant": {"name": n}}, "phase_results": [{"duration": 1.5}, {"duration": 2}]} for n in NAMES]}
            jdump(p("o.json"), outcomes)
            self.assertEqual(self.run_cli("costs", "--outcomes", p("o.json"), "--out", p("costs.json")), 0)
            self.assertEqual(set(jload(p("costs.json"))["costs"].values()), {3.5})
            seen = []
            for k in range(3):
                self.assertEqual(self.run_cli("select", "--list", p("list.json"), "--costs", p("costs.json"), "--shards", "3", "--shard", str(k),
                                              "--regex-out", p("re%d" % k), "--names-out", p("n%d.json" % k)), 0)
                mine = jload(p("n%d.json" % k))
                rx = re.compile(read(p("re%d" % k)))
                jdump(p("sel%d.json" % k), [{"name": n} for n in NAMES if rx.search(n)])
                self.assertEqual(self.run_cli("verify", "--names", p("n%d.json" % k), "--selected", p("sel%d.json" % k)), 0)
                seen += mine
            self.assertEqual(sorted(seen), sorted(NAMES))
            # a selection that lost one, or has one more, stops the shard before it runs
            jdump(p("short.json"), [{"name": n} for n in NAMES[1:]])
            self.assertEqual(self.run_cli("verify", "--names", p("n0.json"), "--selected", p("short.json")), 1)
            jdump(p("all.json"), [{"name": n} for n in NAMES])
            swapped = [n for n in jload(p("n0.json"))]
            swapped[0] = [n for n in NAMES if n not in swapped][0]       # as many, but one of them is another
            jdump(p("swapped.json"), [{"name": n} for n in swapped])
            self.assertEqual(self.run_cli("verify", "--names", p("n0.json"), "--selected", p("swapped.json")), 1)
            self.assertEqual(self.run_cli("verify", "--names", p("n0.json"), "--selected", p("all.json")), 1)
            self.assertEqual(self.run_cli("select", "--list", p("list.json"), "--costs", p("costs.json"), "--shards", "3", "--shard", "3",
                                          "--regex-out", p("x"), "--names-out", p("y")), 1)
            jdump(p("bad.json"), {"schema": "other", "costs": {"a": 1.0}})
            self.assertEqual(self.run_cli("select", "--list", p("list.json"), "--costs", p("bad.json"), "--shards", "3", "--shard", "0",
                                          "--regex-out", p("x"), "--names-out", p("y")), 1)

    def test_the_table_in_the_repository_is_well_formed_and_covers_the_listed_mutants(self):
        t = sp.load_costs(os.path.join(HERE, "mutation-costs.json"))
        self.assertGreater(len(t), 400)
        self.assertTrue(all(v >= 0 for v in t.values()))


class WorkflowTests(unittest.TestCase):
    def test_the_number_of_shards_is_computed_once_and_the_matrix_agrees(self):
        text = read(os.path.join(HERE, "..", ".github", "workflows", "p1.yml"))
        job = text[text.index("\n  quality-mutation-rust:"):text.index("\n  quality-mutation-go:")]
        # the count is the output of the plan job (tools/shard_count.py); the matrix and the dealing read the same value
        self.assertIn("RUST_SHARDS: ${{ needs.plan.outputs.rust_n }}", job)
        self.assertIn("shard: ${{ fromJSON(needs.plan.outputs.rust_shards) }}", job)
        self.assertNotRegex(job, r"cargo \+?\w* ?mutants[^\n]*--shard\b")   # no second way of dealing the mutants
        self.assertIn("--timeout 120", job)               # the timeout is the one the measurement has always had
        self.assertIn("shard_plan.py verify", job)        # the shard stops if its regex selects other mutants than planned
        self.assertLess(job.index("shard_plan.py verify"), job.index("Mutation, Rust (cargo-mutants), one shard"))


class TriggerTests(unittest.TestCase):
    """A pull request that changes only a file p1 uses must still run p1: otherwise the check it is subject to can be skipped. Since #97
    p1 runs on every pull request (its checks are required by the ruleset of main), which holds it for every file at once; a path filter
    would bring the question back."""

    def test_p1_runs_on_every_pull_request(self):
        text = read(os.path.join(HERE, "..", ".github", "workflows", "p1.yml"))
        self.assertRegex(text, r"(?m)^  pull_request:(\s*#.*)?$")
        self.assertNotRegex(text, r"(?m)^    paths: \[")


# (name, old, new): each must occur once in shard_plan.py and make a test of PlanTests fail
DEFECTS = [
    ("shortest first instead of longest first", "key=lambda n: (-cost[n], n)", "key=lambda n: (cost[n], n)"),
    ("always the first shard", "j = min(range(shards), key=lambda i: (loads[i], i))", "j = 0"),
    ("round robin instead of by cost", "j = min(range(shards), key=lambda i: (loads[i], i))", "j = len(owner) % shards"),
    ("the cost of a mutant is ignored", 'cost = {n: float(table.get(k[n], fallback)) for n in names}', "cost = {n: 1.0 for n in names}"),
    ("a mutant is dropped", "owner[n] = j\n", "owner[n] = j if len(owner) < len(names) - 1 else owner.setdefault(n, j)\n        if len(owner) == len(names): del owner[n]\n"),
    ("the regex is not anchored", 'return "^(?:%s)$" %', 'return "(?:%s)" %'),
    ("regex characters are not escaped", 'META.sub(r"\\\\\\1", n)', "n"),
    ("the line numbers are part of the key", 'int(m.group("col")), m.group("what")', 'int(m.group("col")), m.group("line") + m.group("what")'),
    ("the same text gets one key", 'out[n] = "%s#%d" % (base, seen[base])', "out[n] = base"),
    ("verify compares only the sizes", "if sorted(planned) != sorted(chosen):", "if len(planned) != len(chosen):"),
    ("a mutant listed twice is accepted", "if len(set(names)) != len(names):", "if False:"),
    ("a cost table of another schema is accepted", 'doc.get("schema") != SCHEMA or ', ""),
]


class SeededDefects(unittest.TestCase):
    def test_every_defect_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, old, new in DEFECTS:
                src = read(os.path.join(HERE, "shard_plan.py"))
                self.assertEqual(src.count(old), 1, "stale defect %r: %r occurs %d times" % (name, old, src.count(old)))
                shutil.copy(os.path.join(HERE, "test_shard_plan.py"), tmp)
                shutil.copy(os.path.join(HERE, "mutation-costs.json"), tmp)
                with open(os.path.join(tmp, "shard_plan.py"), "w", encoding="utf-8") as fh:
                    fh.write(src.replace(old, new))
                done = subprocess.run([sys.executable, "-m", "unittest", "test_shard_plan.PlanTests"], cwd=tmp, capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0, "NOT CAUGHT: %s" % name)
            with open(os.path.join(tmp, "shard_plan.py"), "w", encoding="utf-8") as fh:   # the unbroken copy passes
                fh.write(read(os.path.join(HERE, "shard_plan.py")))
            done = subprocess.run([sys.executable, "-m", "unittest", "test_shard_plan.PlanTests"], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
