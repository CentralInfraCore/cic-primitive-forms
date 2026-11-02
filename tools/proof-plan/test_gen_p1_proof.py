"""p1-proof.yml is what the generator makes from p1.yml, and the job graph is p1's.

    python3 -m unittest test_gen_p1_proof
"""
import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_p1_proof as g  # noqa: E402

P1 = yaml.safe_load(open(g.SRC))
PROOF = yaml.safe_load(open(g.DST))


class Generated(unittest.TestCase):
    def test_the_file_in_the_repository_is_what_the_generator_makes(self):
        self.assertEqual(g.main(["--check"]), 0)

    def test_same_job_graph(self):
        self.assertEqual(list(P1["jobs"]) + ["proof-plan", "proof-finish", "proof-mutplan", "proof-compare"], list(PROOF["jobs"]))
        for k, j in P1["jobs"].items():
            p = PROOF["jobs"][k]
            for field in ("needs", "if", "strategy", "runs-on", "steps", "outputs", "timeout-minutes"):
                if k in ("rust", "go") and field in ("needs", "steps"):
                    continue   # wired to the planner: see the tests below
                if k == "quality-mutation-rust" and field in ("needs", "if", "steps"):
                    continue   # wired to the carry-over: see the tests below
                self.assertEqual(j.get(field), p.get(field), (k, field))

    def test_the_planner_job_is_called(self):
        j = PROOF["jobs"]["proof-plan"]
        self.assertEqual(j["uses"], "./.github/workflows/proof-plan.yml")
        self.assertEqual(j["needs"], "inherit")

    def test_rust_and_go_wait_for_the_planner_and_change_only_their_test_step(self):
        for k, cmd in (("rust", 'select_tests.py" run rust'), ("go", 'select_tests.py" run go')):
            self.assertEqual(PROOF["jobs"][k]["needs"], ["inherit", "proof-plan"])
            old, new = P1["jobs"][k]["steps"], PROOF["jobs"][k]["steps"]
            extra = [s for s in new if "download-artifact" in str(s.get("uses")) or "upload-artifact" in str(s.get("uses"))
                     or str(s.get("name", "")).startswith("The code that runs the tests")]
            self.assertEqual(len(new), len(old) + 3)   # the download of the plan, the choice of the code, the upload of what the job observed
            self.assertEqual(len(extra), 3)
            changed = [(a, b) for a, b in zip([s for s in new if s not in extra], old) if a != b]
            self.assertEqual(len(changed), 1, k)
            self.assertIn(cmd, changed[0][0]["run"])
            self.assertTrue(any("download-artifact" in str(s.get("uses")) and s.get("continue-on-error") for s in new))

    def test_nothing_else_waits_for_the_planner(self):
        for k, j in PROOF["jobs"].items():
            needs = j.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            self.assertEqual("proof-plan" in needs, k in ("rust", "go", "proof-finish", "proof-compare"), k)
            self.assertEqual("proof-mutplan" in needs, k in ("quality-mutation-rust", "proof-compare"), k)

    def test_the_compare_job_waits_for_every_other_job_and_nothing_waits_for_it(self):
        c = PROOF["jobs"]["proof-compare"]
        self.assertEqual(set(c["needs"]), (set(PROOF["jobs"]) - {"proof-compare"}))
        self.assertIn("always()", c["if"])
        for k, j in PROOF["jobs"].items():
            n = j.get("needs", [])
            self.assertNotIn("proof-compare", [n] if isinstance(n, str) else n, k)

    def test_the_mutation_shard_is_wired_to_the_carry_over_and_runs_without_it(self):
        j, o = PROOF["jobs"]["quality-mutation-rust"], P1["jobs"]["quality-mutation-rust"]
        self.assertEqual(j["needs"], o["needs"] + ["proof-mutplan"])
        # a failed or skipped prediction must not skip the shard; the plan and the classic FULL are still required
        for must in ("!cancelled()", "needs.plan.result == 'success'", "mode == 'full'", "engine == 'classic'"):
            self.assertIn(must, j["if"])
        text = "\n".join(str(s.get("run", "")) for s in j["steps"])
        self.assertIn("mutcarry.py merge", text)
        self.assertIn('"${EXARGS[@]}"', text)
        dl = [s for s in j["steps"] if "download-artifact" in str(s.get("uses"))]
        self.assertTrue(dl and all(s.get("continue-on-error") for s in dl))
        # the merge comes before the upload of the report the gate reads
        names = [s.get("name", s.get("uses", "")) for s in j["steps"]]
        merge_at = next(i for i, s in enumerate(j["steps"]) if "mutcarry.py merge" in str(s.get("run")))
        upload_at = next(i for i, s in enumerate(j["steps"]) if "upload-artifact" in str(s.get("uses")) and s["with"]["name"].startswith("rust-mutants-$"))
        self.assertLess(merge_at, upload_at, names)

    def test_the_planner_has_two_phases_around_the_tests_and_does_not_run_them(self):
        a, f = PROOF["jobs"]["proof-plan"], PROOF["jobs"]["proof-finish"]
        self.assertEqual((a["with"]["phase"], f["with"]["phase"]), ("plan", "finish"))
        self.assertEqual(set(f["needs"]), {"inherit", "rust", "go", "proof-plan"})
        self.assertIn("always()", f["if"])
        for k in ("rust", "go"):
            text = "\n".join(str(s.get("run", "")) for s in PROOF["jobs"][k]["steps"])
            self.assertIn("--observed-out", text)
            up = [s for s in PROOF["jobs"][k]["steps"] if "upload-artifact" in str(s.get("uses"))]
            self.assertEqual([s["with"]["name"] for s in up], ["proof-observed-" + k])
            dl = [s for s in PROOF["jobs"][k]["steps"] if "download-artifact" in str(s.get("uses"))]
            self.assertEqual([s["with"]["name"] for s in dl], ["proof-plan-data"])

    def test_the_test_runner_is_not_the_pull_requests_code(self):
        for k in ("rust", "go"):
            steps = PROOF["jobs"][k]["steps"]
            choose = next(i for i, s in enumerate(steps) if str(s.get("name", "")).startswith("The code that runs the tests"))
            run = next(i for i, s in enumerate(steps) if '"$PTOOL/select_tests.py"' in str(s.get("run")) and " run " in str(s.get("run")))
            self.assertLess(choose, run)
            self.assertIn('"$PTOOL/select_tests.py"', steps[run]["run"])
            self.assertNotIn("tools/proof-plan/select_tests.py", steps[run]["run"])   # not the workspace's copy
            script = steps[choose]["run"]
            self.assertIn('[ "$BASE_REF" = main ]', script)        # only a base that is main is trusted
            self.assertIn('git archive "$BASE_SHA"', script)       # the base commit's code, not the merge's
            self.assertIn('PTOOL="$GITHUB_WORKSPACE/tools/proof-plan"', script)   # the fallback is the tree's own: main's on a run of main

    def test_the_planner_jobs_do_not_run_on_an_inherited_tree(self):
        """A push whose tree a pull request already checked runs nothing in p1 (inherit): the planner's jobs would only cost time (3 minutes against 15 seconds)
        and have nothing to decide or to compare. Each of them says so in its condition; the condition keeps a status function, so a skipped inherit (a pull
        request has none) does not skip them."""
        for k in ("proof-plan", "proof-finish", "proof-mutplan"):
            cond = PROOF["jobs"][k]["if"]
            self.assertIn("needs.inherit.outputs.inherited != 'true'", cond, k)
            self.assertTrue("!cancelled()" in cond or "always()" in cond, k)
            n = PROOF["jobs"][k]["needs"]
            self.assertIn("inherit", [n] if isinstance(n, str) else n, k)
        # the compare still runs (it compares the jobs of the two runs, which are both skipped) but does not ask for the planner's comparison
        cmp_ = PROOF["jobs"]["proof-compare"]
        run = next(s["run"] for s in cmp_["steps"] if "compare_runs.py" in str(s.get("run")))
        self.assertIn("--no-planner", run)
        env = next(s["env"] for s in cmp_["steps"] if "compare_runs.py" in str(s.get("run")))
        self.assertEqual(env["INHERITED"], "${{ needs.inherit.outputs.inherited }}")

    def test_the_prediction_job_never_fails_the_pipeline(self):
        j = PROOF["jobs"]["proof-mutplan"]
        self.assertTrue(j["continue-on-error"])
        self.assertEqual(j["needs"], ["scope", "inherit"])

    def test_every_shell_script_of_the_two_workflows_parses(self):
        """A quoting slip in a `run:` block fails only when the step runs, and a job with continue-on-error hides it behind a green mark."""
        import re
        import subprocess
        planner = yaml.safe_load(open(os.path.join(os.path.dirname(g.DST), "proof-plan.yml")))
        bad = []
        for wf in (PROOF, planner):
            for jn, j in wf["jobs"].items():
                for s in j.get("steps", []):
                    r = s.get("run")
                    if r:
                        r = re.sub(r"\$\{\{.*?\}\}", "X", r, flags=re.S)
                        p = subprocess.run(["bash", "-n"], input=r, text=True, capture_output=True)
                        if p.returncode:
                            bad.append((jn, s.get("name", r[:40]), p.stderr[:80]))
        self.assertEqual(bad, [])

    def test_the_trusted_copy_of_the_planner_has_every_sibling_it_imports(self):
        """The planner of the base commit is extracted with `git archive`; a module of tools/ that tools/proof-plan imports (mutcarry takes shard_plan
        and quality_gate from the parent directory) must be in that archive, or the self-test of every pull request on main fails to import."""
        import re
        here = os.path.dirname(os.path.abspath(__file__))
        tools = os.path.dirname(here)
        siblings = {m[:-3] for m in os.listdir(tools) if m.endswith(".py")}
        used = set()
        for f in os.listdir(here):
            if f.endswith(".py"):
                used |= {m for m in re.findall(r"^import (\w+)", open(os.path.join(here, f)).read(), re.M) if m in siblings}
        archive = next(l for l in open(os.path.join(os.path.dirname(g.DST), "proof-plan.yml")) if "git archive" in l)
        self.assertTrue(used)
        for m in used:
            self.assertIn("tools/%s.py" % m, archive, m)

    def test_dual_starts_both_methodologies_and_asks_for_nothing_else(self):
        import re
        import subprocess
        d = yaml.safe_load(open(os.path.join(os.path.dirname(g.DST), "dual.yml")))
        on = d[True] if True in d else d["on"]
        self.assertEqual(set(on), {"workflow_dispatch"})
        self.assertEqual(d["permissions"], {"contents": "read", "actions": "write"})
        run = d["jobs"]["both"]["steps"][0]["run"]
        self.assertIn("gh workflow run p1.yml", run)
        self.assertIn("gh workflow run p1-proof.yml", run)
        p = subprocess.run(["bash", "-n"], input=re.sub(r"\$\{\{.*?\}\}", "X", run, flags=re.S), text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_every_job_is_named_apart_from_the_required_checks(self):
        for k, j in PROOF["jobs"].items():
            self.assertTrue(j["name"].startswith("proof / "), k)
            self.assertNotEqual(j["name"], k)

    def test_matrix_job_name_is_kept_after_the_prefix(self):
        self.assertEqual(PROOF["jobs"]["fuzz-target"]["name"], "proof / " + P1["jobs"]["fuzz-target"]["name"])

    def test_triggers(self):
        on = PROOF[True] if True in PROOF else PROOF["on"]  # PyYAML reads the key `on` as True
        self.assertNotIn("workflow_call", on)
        self.assertEqual(set(on), {"workflow_dispatch", "push", "pull_request"})

    def test_inherit_asks_about_itself(self):
        w = PROOF["jobs"]["inherit"]["with"]
        self.assertEqual((w["caller"], w["workflows"]), ("p1-proof", "p1-proof.yml,corpus.yml"))

    def test_an_unknown_shape_of_p1_is_an_error(self):
        with self.assertRaises(g.Unsupported):
            g.generate("name: other\njobs: {}\n")
        with self.assertRaises(g.Unsupported):
            g.generate(open(g.SRC).read().replace("      caller: p1\n", "      caller: p1\n      caller: p1\n"))


if __name__ == "__main__":
    unittest.main()
