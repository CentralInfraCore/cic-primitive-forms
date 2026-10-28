"""The properties of the evidence workflows that the security of the evidence rests on, held as tests: who may trigger them,
what they may do, where the App's credentials may be used, and that nothing an outsider controls reaches a shell."""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify  # noqa: E402

try:
    import yaml
except ImportError:  # the job installs it; a bare checkout may not have it
    yaml = None

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
WF = os.path.join(ROOT, ".github", "workflows")


def load(name):
    with open(os.path.join(WF, name), encoding="utf-8") as fh:
        text = fh.read()
    doc = yaml.safe_load(text)
    doc["on"] = doc.pop(True, doc.get("on"))  # YAML 1.1 reads the key `on` as true
    return doc, text


@unittest.skipIf(yaml is None, "no PyYAML")
class EvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(os.path.join(WF, "evidence.yml")):
            self.skipTest("not inside the repository")
        self.ev, self.ev_text = load("evidence.yml")

    def steps(self, job):
        return self.ev["jobs"][job]["steps"]

    def test_who_can_start_it(self):
        triggers = self.ev["on"]
        self.assertEqual(sorted(triggers), ["push", "schedule", "workflow_dispatch"])
        self.assertEqual(triggers["push"], {"branches": ["main"]})
        for forbidden in ("pull_request", "pull_request_target", "workflow_run", "issue_comment", "repository_dispatch", "workflow_call"):
            self.assertNotIn(forbidden, triggers)
        self.assertEqual([s["cron"] for s in triggers["schedule"]], ["17 3 * * *", "47 7 * * *"])

    def test_the_crons_are_the_ones_the_jobs_wait_for(self):
        daily, watchdog = (s["cron"] for s in self.ev["on"]["schedule"])
        self.assertIn("github.event.schedule == '%s'" % daily, self.ev["jobs"]["record"]["if"])
        self.assertIn("github.event.schedule == '%s'" % watchdog, self.ev["jobs"]["watch"]["if"])
        for job in ("record", "watch"):
            self.assertIn("vars.EVIDENCE_ENABLED == 'true'", self.ev["jobs"][job]["if"])   # off until the owner turns it on

    def test_least_privilege(self):
        self.assertEqual(self.ev["permissions"], {})
        self.assertEqual(self.ev["jobs"]["record"]["permissions"],
                         {"contents": "read", "actions": "write", "id-token": "write", "attestations": "write"})
        self.assertEqual(self.ev["jobs"]["watch"]["permissions"], {"contents": "read", "actions": "write"})
        for job in self.ev["jobs"].values():
            self.assertNotIn("contents: write", json.dumps(job["permissions"]).replace('"', "").replace(": ", ": "))
            self.assertNotEqual(job["permissions"].get("contents"), "write")

    def test_the_credentials_of_the_app_are_used_once_and_only_where_main_may_use_them(self):
        self.assertEqual(self.ev["jobs"]["record"]["environment"], "evidence")
        self.assertNotIn("environment", self.ev["jobs"]["watch"])
        self.assertEqual(self.ev_text.count("secrets.EVIDENCE_APP_PRIVATE_KEY"), 1)
        # the token is made after the attestation, and only the publish step holds it
        names = [s.get("uses", s.get("name", "")) for s in self.steps("record")]
        app = next(i for i, s in enumerate(self.steps("record")) if s.get("id") == "app")
        att = next(i for i, s in enumerate(self.steps("record")) if "attest-build-provenance" in s.get("uses", ""))
        pub = next(i for i, s in enumerate(self.steps("record")) if s.get("name") == "Publish to ci-evidence")
        self.assertLess(att, app)
        self.assertLess(app, pub)
        self.assertEqual(self.ev_text.count("steps.app.outputs.token"), 1)
        holders = [s for s in self.steps("record") if "steps.app.outputs.token" in json.dumps(s)]
        self.assertEqual([s["name"] for s in holders], ["Publish to ci-evidence"])
        self.assertTrue(names)

    def test_it_is_the_signer_the_verifier_expects(self):
        trust = verify.Trust()
        self.assertTrue(os.path.exists(os.path.join(ROOT, trust.signer_workflow)))
        self.assertEqual(trust.signer_workflow, ".github/workflows/evidence.yml")
        self.assertEqual(trust.source_ref, "refs/heads/main")
        self.assertEqual(trust.repository, "CentralInfraCore/cic-primitive-forms")
        attest = [s for s in self.steps("record") if "attest-build-provenance" in s.get("uses", "")]
        self.assertEqual(len(attest), 1)
        self.assertEqual(attest[0]["with"], {"subject-path": "${{ steps.rec.outputs.path }}"})

    def test_nothing_an_outsider_controls_reaches_a_shell(self):
        for job, body in self.ev["jobs"].items():
            for step in body["steps"]:
                run = step.get("run")
                if run is not None:
                    self.assertNotIn("${{", run, "%s / %s interpolates an expression into a shell" % (job, step.get("name")))

    def test_the_watchdog_asks_for_a_run_only_for_a_slot_that_has_no_record(self):
        step = next(s for s in self.steps("watch") if s.get("name", "").startswith("The slot of the day"))
        run = step["run"]
        self.assertEqual(run.count("gh workflow run evidence.yml"), 1)
        before = run[:run.index("gh workflow run evidence.yml")]
        self.assertIn("-eq 3", before)                                               # only the exit status "no record"
        self.assertIn("has no record$/", before)                                     # and only from the line that says so
        self.assertIn("[0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}", before)             # for a date, not for anything
        self.assertIn('[ -n "$slot" ] ||', before)                                   # and not for an empty slot
        after = run[run.index("gh workflow run evidence.yml"):]
        self.assertIn("elif [ $rc -ne 0 ]", after)                                   # every other failure fails the job
        self.assertRegex(after, r"\n\s+exit \$rc\n")                                 # with its own status, not 0

    def test_the_run_is_for_main_and_the_checkout_keeps_no_credentials(self):
        for job, body in self.ev["jobs"].items():
            for step in body["steps"]:
                if "actions/checkout" in step.get("uses", ""):
                    self.assertFalse(step["with"].get("persist-credentials", True))

    def test_the_credentials_of_the_app_are_in_no_other_workflow_but_the_probes(self):
        for name in sorted(os.listdir(WF)):
            if name in ("evidence.yml", "evidence-probes.yml") or not name.endswith(".yml"):
                continue
            with open(os.path.join(WF, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("EVIDENCE_APP", text, name)
            self.assertNotIn("environment: evidence", text, name)

    def test_the_probes_are_run_by_hand_on_the_branch_they_may_touch(self):
        probes, text = load("evidence-probes.yml")
        self.assertEqual(sorted(probes["on"]), ["workflow_dispatch"])
        self.assertEqual(probes["permissions"], {})
        self.assertNotIn("refs/heads/ci-evidence ", text.replace("ci-evidence-probe", ""))   # never names the real branch to push to
        # the probes are the condition for EVIDENCE_ENABLED, so they must not wait for it
        self.assertNotIn("EVIDENCE_ENABLED", text.replace("NOT gated by EVIDENCE_ENABLED", ""))
        for job in probes["jobs"].values():
            self.assertNotIn("if", job)
        self.assertEqual(probes["jobs"]["branch"]["environment"], "evidence")
        self.assertNotIn("environment", probes["jobs"]["attestation"])

    def test_the_ruleset_covers_the_branch_and_names_one_actor(self):
        with open(os.path.join(HERE, "setup", "ruleset-ci-evidence.json")) as fh:
            rs = json.load(fh)
        self.assertEqual(rs["conditions"]["ref_name"]["include"], ["refs/heads/ci-evidence", "refs/heads/ci-evidence-probe"])
        self.assertEqual(sorted(r["type"] for r in rs["rules"]), ["creation", "deletion", "non_fast_forward", "update"])
        self.assertEqual(len(rs["bypass_actors"]), 1)
        self.assertEqual(rs["bypass_actors"][0]["actor_type"], "Integration")
        self.assertEqual(rs["enforcement"], "active")


class SetupScriptTests(unittest.TestCase):
    def test_apply_refuses_an_unprotected_main_unless_told_otherwise(self):
        with open(os.path.join(HERE, "setup", "apply.sh")) as fh:
            text = fh.read()
        self.assertIn("rules/branches/main", text)
        self.assertIn("ALLOW_UNPROTECTED_MAIN", text)

    def test_the_isolation_probe_exists_and_cleans_up(self):
        with open(os.path.join(HERE, "setup", "probe-isolation.sh")) as fh:
            text = fh.read()
        self.assertIn("trap", text)
        self.assertIn("environment: evidence", text)


    def test_it_changes_nothing_unless_told_and_never_turns_the_evidence_on(self):
        with open(os.path.join(HERE, "setup", "apply.sh")) as fh:
            text = fh.read()
        self.assertIn('if [ "$APPLY" = 1 ]', text)
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if code.lstrip().startswith("echo"):
                continue
            self.assertNotRegex(code, r"variable set EVIDENCE_ENABLED")


@unittest.skipIf(yaml is None, "no PyYAML")
class NightlyWorkflowTests(unittest.TestCase):
    """nightly.yml (docs/DELTA-DIFF-GATE.md, section 4): the whole gate on main every night, a pull request when it fails."""

    def setUp(self):
        if not os.path.exists(os.path.join(WF, "nightly.yml")):
            self.skipTest("not inside the repository")
        self.nt, self.text = load("nightly.yml")

    def test_who_can_start_it(self):
        triggers = self.nt["on"]
        # also a push to main that changes the gate workflow (docs/DELTA-DIFF-GATE.md, 4.2): the FULL in the background, only on that path
        self.assertEqual(sorted(triggers), ["push", "schedule", "workflow_dispatch"])
        self.assertEqual(triggers["push"]["paths"], [".github/workflows/p1.yml"])
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertEqual([s["cron"] for s in triggers["schedule"]], ["0 1 * * *"])   # 02:00 CET; one change at summer time

    def test_it_runs_the_same_gate_as_a_pull_request(self):
        jobs = self.nt["jobs"]
        self.assertEqual(jobs["p1"]["uses"], "./.github/workflows/p1.yml")
        self.assertEqual(jobs["corpus"]["uses"], "./.github/workflows/corpus.yml")
        for name in ("p1", "corpus"):
            self.assertIn("workflow_call", load(name + ".yml")[0]["on"])
            self.assertIn("needs.changed.outputs.run == 'true'", jobs[name]["if"])   # only after development

    def test_the_default_is_no_permission_and_only_the_report_writes(self):
        self.assertEqual(self.nt["permissions"], {})
        for name, job in self.nt["jobs"].items():
            wrote = {k for k, v in job.get("permissions", {}).items() if v == "write"}
            self.assertEqual(wrote, {"contents", "pull-requests", "issues"} if name == "report" else set(), name)

    def test_the_report_runs_when_the_gate_failed_and_carries_the_run_id(self):
        report = self.nt["jobs"]["report"]
        self.assertIn("always()", report["if"])
        self.assertIn("needs.p1.result == 'failure'", report["if"])
        self.assertIn("needs.corpus.result == 'failure'", report["if"])
        self.assertIn("github.run_id", self.text)
        self.assertIn("RUN_ID", self.text)
        self.assertIn("gh pr create", self.text)

    def test_when_the_pull_request_cannot_be_opened_an_issue_with_the_same_facts_is_opened(self):
        # the setting "Allow GitHub Actions to create and approve pull requests" may be off, or the organization may forbid it: that must not hide the failure
        self.assertIn("if gh pr create", self.text)
        self.assertIn("gh issue create", self.text)
        self.assertIn("::warning::", self.text)
        self.assertLess(self.text.index("gh pr create"), self.text.index("gh issue create"))
        self.assertIn("RUN_ID", self.text.split("gh issue create")[0].split("issue.md")[0])

    def test_the_report_can_be_tested_by_hand_without_the_heavy_gate(self):
        self.assertEqual(self.nt["on"]["workflow_dispatch"]["inputs"]["report_test"]["default"], False)
        self.assertIn("report_test == 'true'", self.nt["jobs"]["report"]["if"])
        self.assertIn("run=false", self.text.split("REPORT_TEST\" = \"true\"")[1].split("exit 0")[0])   # the gate is skipped in a test
        self.assertIn("[TEST]", self.text)

    def test_no_event_text_reaches_a_shell_line(self):
        for job in self.nt["jobs"].values():
            for step in job.get("steps", []):
                if "run" in step:
                    self.assertNotIn("${{", step["run"])   # inputs go through env, not into the script text


@unittest.skipIf(yaml is None, "no PyYAML")
class LightPushTests(unittest.TestCase):
    """p1.yml (docs/DELTA-DIFF-GATE.md, 4.1): a push to main runs only the cheap checks; the nightly FULL and the pull request run the rest."""

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        self.jobs = self.p1["jobs"]

    def test_the_scope_job_calls_a_push_light_and_a_pull_request_by_its_diff(self):
        # a push is light unless the caller forces full (the FULL after a change of this workflow, nightly.yml)
        self.assertIn('[ "$EVENT" = "push" ] && [ "$FORCE_FULL" != "true" ] && mode=light', self.text)
        self.assertIn("diff_gate.py scope", self.text)
        self.assertEqual(self.jobs["scope"]["outputs"], {"mode": "${{ steps.s.outputs.mode }}", "engine": "${{ steps.s.outputs.engine }}",
                                                         "seeded_shards": "${{ steps.s.outputs.seeded_shards }}", "seeded_n": "${{ steps.s.outputs.seeded_n }}",
                                                         "fuzz_matrix": "${{ steps.s.outputs.fuzz_matrix }}"})

    def test_the_heavy_jobs_do_not_run_on_a_light_push(self):
        for name in ("seeded-defects-shard", "evidence-selftest"):
            self.assertIn("scope", _needs(self.jobs[name]), name)
            self.assertEqual(self.jobs[name]["if"], "${{ needs.scope.outputs.mode != 'light' }}", name)
        self.assertIn("mode != 'light'", self.jobs["seeded-defects"]["if"])      # the joining job, which keeps the name the required checks list
        # the fuzz targets: one job each, and none on a light push (the matrix is empty there as well); `fuzz` joins them under the name that is required
        self.assertIn("mode != 'light'", self.jobs["fuzz-target"]["if"])
        self.assertIn("fuzz_matrix != '[]'", self.jobs["fuzz-target"]["if"])
        self.assertIn("mode != 'light'", self.jobs["fuzz"]["if"])
        self.assertIn("fuzz-target", _needs(self.jobs["fuzz"]))
        self.assertIn("mode != 'light'", self.jobs["quality"]["if"])
        for name in ("quality-mutation-rust", "quality-mutation-go"):
            self.assertIn("mode == 'full'", self.jobs[name]["if"], name)       # light is not full
        for name in ("diff-mutation-rust", "diff-mutation-go"):
            self.assertIn("mode == 'diff'", self.jobs[name]["if"], name)

    def test_the_cheap_checks_run_whatever_the_mode_is(self):
        for name in ("rust", "go", "branch-coverage", "quality-gate-selftest", "quality-lines", "parity"):
            self.assertNotIn("if", self.jobs[name], name)


@unittest.skipIf(yaml is None, "no PyYAML")
class ShardPlanTests(unittest.TestCase):
    """p1.yml (docs/DELTA-DIFF-GATE.md, 9.2): the shard count of the full mutation is one computed value, not a number written in three places."""

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        self.jobs = self.p1["jobs"]

    def test_the_plan_job_feeds_both_matrices_and_the_gate_waits_for_it(self):
        self.assertIn("mode == 'full'", self.jobs["plan"]["if"])
        for name in ("quality-mutation-rust", "quality-mutation-go"):
            self.assertIn("plan", _needs(self.jobs[name]), name)
            self.assertIn("fromJSON(needs.plan.outputs.", self.jobs[name]["strategy"]["matrix"]["shard"], name)
        self.assertIn("plan", _needs(self.jobs["quality"]))
        self.assertNotIn("/6", self.text)                      # no shard count written into a script
        self.assertNotIn("RUST_SHARDS: 6", self.text)

    def test_the_gate_wants_the_plan_to_have_succeeded_in_full_and_skipped_in_diff(self):
        self.assertIn('fast_not && test "$PLAN" = success', self.text)      # the classic full; the fast full has no plan
        self.assertIn('diff) fast_not && test "$PLAN" = skipped', self.text)


@unittest.skipIf(yaml is None, "no PyYAML")
class DiffShardTests(unittest.TestCase):
    """p1.yml (#92): the diff is sharded like the full run, and a change inside existing tests is narrowed by their coverage."""

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        self.jobs = self.p1["jobs"]

    def test_the_diff_plan_feeds_both_diff_matrices_and_the_gate_waits_for_it(self):
        self.assertIn("mode == 'diff'", self.jobs["diff-plan"]["if"])
        for name, lang in (("diff-mutation-rust", "rust"), ("diff-mutation-go", "go")):
            self.assertIn("diff-plan", _needs(self.jobs[name]), name)
            self.assertEqual(self.jobs[name]["strategy"]["matrix"]["shard"], "${{ fromJSON(needs.diff-plan.outputs.%s_shards) }}" % lang, name)
        self.assertIn("diff-plan", _needs(self.jobs["quality"]))
        self.assertIn('diff) fast_not && test "$PLAN" = skipped && test "$DIFF_PLAN" = success', self.text)
        self.assertIn('test "$DIFF_PLAN" = skipped && test "$DIFF_RUST" = skipped', self.text)   # and skipped in full

    def test_every_diff_shard_report_is_read_against_the_one_list(self):
        self.assertIn('--rust-mutants "$Q"/in/rust-diff-mutants-*/outcomes.json --rust-listed "$Q/in/rust-diff-listed/rust-listed.json"', self.text)
        self.assertIn('--go-ledgers "$Q"/in/go-diff-ledger-*/ledger', self.text)
        self.assertIn('CIC_MUTATION_SHARD="${{ matrix.shard }}/$GO_N"', self.text)
        self.assertIn("shard_plan.py verify", str(self.jobs["diff-mutation-rust"]["steps"]))

    def test_the_scope_measures_only_when_a_changed_test_is_all_that_keeps_it_full(self):
        steps = {s.get("id"): s for s in self.jobs["scope"]["steps"] if s.get("id")}
        self.assertIn("only = v[\"reasons\"] == [d.PENDING]", steps["w"]["run"])
        self.assertIn("--measure", steps["s"]["run"])
        for s in self.jobs["scope"]["steps"]:
            if s.get("uses", "").startswith("./.github/actions/") or "Rust toolchain" in s.get("name", ""):
                self.assertIn("steps.w.outputs.", s.get("if", ""), s)


@unittest.skipIf(yaml is None, "no PyYAML")
class ChainReviewTests(unittest.TestCase):
    """#94: the toolchain is pinned in one place, the caches find their files, the gate knows the Go count, the nightly reports
    every way it can fail and runs the FULL once per commit."""

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        self.nightly, self.ntext = load("nightly.yml")

    def test_the_rust_toolchain_is_pinned_once_and_every_rust_job_installs_it(self):
        self.assertRegex(self.p1["env"]["RUST_TOOLCHAIN"], r"^\d+\.\d+\.\d+$")
        self.assertNotIn("+stable", self.text)
        self.assertNotIn("install stable", self.text)
        for name, job in self.p1["jobs"].items():
            steps = str(job.get("steps", []))
            if '+"$RUST_TOOLCHAIN"' in steps or "'kind': 'cargo'" in steps or "seeded_defects.py" in steps:
                self.assertIn('toolchain install "$RUST_TOOLCHAIN"', steps, name)

    def test_the_caches_find_their_files(self):
        for name, job in self.p1["jobs"].items():
            for s in job.get("steps", []):
                if s.get("uses", "").startswith("actions/setup-go@"):
                    self.assertEqual(s["with"].get("cache-dependency-path"), "go/go.sum", name)
        self.assertIn("Swatinem/rust-cache@", self.text)

    def test_the_gate_checks_the_go_count_of_the_plan(self):
        for job in ("plan", "diff-plan"):
            self.assertIn("go_mutants", self.p1["jobs"][job]["outputs"], job)
        self.assertEqual(self.text.count('--go-expected "$GO_MUTANTS"'), 2)

    def test_the_nightly_runs_once_per_commit_and_reports_every_failure(self):
        changed = str(self.nightly["jobs"]["changed"]["steps"])
        self.assertIn("nightly_last_full.py", changed)
        self.assertNotIn("48 hours ago", changed)
        cond = self.nightly["jobs"]["report"]["if"]
        for part in ("needs.changed.result == 'failure'", "needs.changed.result == 'cancelled'", "needs.p1.result == 'cancelled'",
                     "needs.corpus.result == 'cancelled'"):
            self.assertIn(part, cond)
        self.assertIn('git push origin --delete "$branch"', self.ntext)


@unittest.skipIf(yaml is None, "no PyYAML")
class EveryPullRequestTests(unittest.TestCase):
    """#97: the checks of p1 are required on main, so p1 must report them on every pull request."""

    def test_p1_has_no_path_filter_on_pull_requests(self):
        p1, _ = load("p1.yml")
        triggers = p1[True] if True in p1 else p1["on"]   # PyYAML reads the key `on` as True
        self.assertIn("pull_request", triggers)
        self.assertFalse((triggers["pull_request"] or {}).get("paths"))


def _needs(job):
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else n


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(yaml is None, "no PyYAML")
class FastFullTests(unittest.TestCase):
    """p1.yml (docs/FAST-FULL.md): the full run of a pull request is measured by cargo-gamma and go-mutants, the nightly FULL by the classic tools."""

    FAST = ("fast-list", "fast-mutation-rust", "fast-mutation-go")
    CLASSIC = ("plan", "quality-mutation-rust", "quality-mutation-go")

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        self.jobs = self.p1["jobs"]

    def test_only_a_pull_request_in_full_mode_is_measured_by_the_fast_tools(self):
        self.assertIn('[ "$mode" = full ] && [ "$EVENT" = pull_request ] && engine=fast', self.text)
        for name in self.FAST:
            self.assertEqual(self.jobs[name]["if"], "${{ needs.scope.outputs.engine == 'fast' }}", name)
        for name in self.CLASSIC:
            self.assertIn("mode == 'full' && needs.scope.outputs.engine == 'classic'", self.jobs[name]["if"], name)

    def test_the_gate_reads_the_reports_of_the_engine_that_ran(self):
        gate = next(st for st in self.jobs["quality"]["steps"] if st.get("name", "").startswith("Gate, the pull request's full run"))
        self.assertIn("engine == 'fast'", gate["if"])
        for flag in ("--gamma-reports", "--gamma-listed", "--gomutants-reports"):
            self.assertIn(flag, gate["run"])
        self.assertNotIn("--rust-mutants", gate["run"])
        for name in self.FAST:
            self.assertIn(name, _needs(self.jobs["quality"]), name)
        # each engine must find the other's jobs skipped, not merely successful or absent
        self.assertIn('fast_ran && test "$PLAN" = skipped', self.text)
        self.assertIn('fast_not && test "$PLAN" = success', self.text)

    def test_every_shard_the_gate_expects_has_a_matrix_entry(self):
        env = self.p1["env"]
        self.assertEqual(self.jobs["fast-mutation-rust"]["strategy"]["matrix"]["shard"], list(range(int(env["GAMMA_SHARDS"]))))
        self.assertEqual(self.jobs["fast-mutation-go"]["strategy"]["matrix"]["shard"], list(range(1, int(env["GOMUTANTS_SHARDS"]) + 1)))
        self.assertIn('--shard-count "$GAMMA_SHARDS"', self.text)
        self.assertIn('"$GOMUTANTS_SHARDS"', self.jobs["fast-mutation-go"]["steps"][-2]["run"])

    def test_the_fast_tools_are_pinned_and_the_shard_report_must_exist(self):
        self.assertIn("cargo-gamma@0.3.0", self.text)
        self.assertRegex(self.text, r"go-mutants/cmd/go-mutants@v0\.0\.0-\d{14}-[0-9a-f]{12}")
        for name in ("fast-mutation-rust", "fast-mutation-go"):
            up = self.jobs[name]["steps"][-1]
            self.assertEqual(up["with"]["if-no-files-found"], "error", name)    # a shard that wrote no report is red, never a smaller sample
        self.assertNotIn("|| true", "\n".join(st.get("run", "") for st in self.jobs["fast-mutation-rust"]["steps"]))

    def test_the_nightly_shadow_gates_nothing(self):
        shadow = self.jobs["shadow-gamma"]
        self.assertTrue(shadow["continue-on-error"])
        self.assertIn("engine == 'classic'", shadow["if"])
        self.assertNotIn("shadow-gamma", _needs(self.jobs["quality"]))

    def test_the_seeded_proof_of_the_fast_full_weakens_the_tests_and_wants_both_scores_red(self):
        proof = next(st for st in self.jobs["quality"]["steps"] if st.get("name", "").startswith("Seeded proof, weakened tests turn the mutation score red (cargo-gamma"))
        self.assertIn("engine == 'fast'", proof["if"])
        self.assertIn("--include-test subset", proof["run"])
        self.assertIn("FAIL rust mutation score .*BELOW_THRESHOLD", proof["run"])
        self.assertIn("FAIL go mutation score .*BELOW_THRESHOLD", proof["run"])

    def test_the_diff_gamma_is_a_shadow_that_runs_where_a_diff_is_measured(self):
        job = self.jobs["diff-gamma"]
        self.assertTrue(job["continue-on-error"])
        self.assertEqual(job["if"], "${{ needs.scope.outputs.mode == 'diff' || needs.scope.outputs.engine == 'fast' }}")
        self.assertNotIn("diff-gamma", _needs(self.jobs["quality"]))
        run = "\n".join(st.get("run", "") for st in job["steps"])
        self.assertIn("--in-diff", run)
        self.assertIn("--mutators all", run)

    def test_the_full_run_of_a_pull_request_measures_its_diff_with_the_classic_tools_too(self):
        # the evidence on the code a pull request changes is the one it always was; only the whole-tree measurement moves to the fast tools
        both = "${{ needs.scope.outputs.mode == 'diff' || needs.scope.outputs.engine == 'fast' }}"
        for name in ("diff-plan", "diff-mutation-rust", "diff-mutation-go", "diff-gamma"):
            self.assertEqual(self.jobs[name]["if"], both, name)
        gate = next(st for st in self.jobs["quality"]["steps"] if st.get("name", "").startswith("Gate, the diff"))
        self.assertEqual(gate["if"], both)
        self.assertIn("--diff", gate["run"])
        # and the gate wants them to have run, not merely not failed
        self.assertIn('fast_ran && test "$PLAN" = skipped && test "$FULL_RUST" = skipped && test "$FULL_GO" = skipped && test "$DIFF_PLAN" = success '
                      '&& test "$DIFF_RUST" = success && test "$DIFF_GO" = success', self.text)


@unittest.skipIf(yaml is None, "no PyYAML")
class SeededDefectsShardTests(unittest.TestCase):
    """p1.yml: the seeded defects of a full run are cut into shards (one serial job took 33 of its 40 minutes); one job keeps the name that is required."""

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        self.jobs = self.p1["jobs"]

    def test_the_shard_list_and_the_shard_count_are_written_together(self):
        m = re.search(r"seeded='(\[[0-9,]*\])'; seeded_n=(\d+); else seeded='(\[[0-9,]*\])'; seeded_n=(\d+)", self.text)
        self.assertIsNotNone(m)
        full, full_n, diff, diff_n = json.loads(m.group(1)), int(m.group(2)), json.loads(m.group(3)), int(m.group(4))
        self.assertEqual(full, list(range(full_n)))      # every shard K of N, each once: together they cover every defect exactly once
        self.assertEqual(diff, list(range(diff_n)))
        self.assertGreater(full_n, 1)
        self.assertEqual(diff_n, 1)

    def test_the_joining_job_is_red_when_a_shard_is_not_green_and_never_skipped(self):
        join = self.jobs["seeded-defects"]
        self.assertIn("always()", join["if"])
        self.assertEqual(set(_needs(join)), {"scope", "seeded-defects-shard"})
        run = join["steps"][0]["run"]
        self.assertIn('test "$RESULT" = success', run)
        self.assertIn("exit 1", run)

    def test_every_shard_runs_its_slice_of_the_same_count(self):
        shard = self.jobs["seeded-defects-shard"]
        self.assertEqual(shard["strategy"]["matrix"]["shard"], "${{ fromJSON(needs.scope.outputs.seeded_shards) }}")
        step = next(st for st in shard["steps"] if "seeded_defects.py" in st.get("run", ""))
        self.assertEqual(step["env"]["SHARD"], "${{ matrix.shard }}/${{ needs.scope.outputs.seeded_n }}")
        self.assertEqual(step["run"].count('--shard "$SHARD"'), 2)       # the diff and the full command both take their slice


@unittest.skipIf(yaml is None, "no PyYAML")
class FailClosedPlanTests(unittest.TestCase):
    """p1.yml, diff-plan (the review of #108): a mutant listing that failed is not "no mutants in the diff"."""

    def setUp(self):
        self.p1, self.text = load("p1.yml")
        step = next(st for st in self.p1["jobs"]["diff-plan"]["steps"] if st.get("id") == "c")
        self.run = step["run"]

    def test_a_failed_rust_listing_fails_the_job(self):
        self.assertIn("set -e", self.run)
        self.assertNotIn("|| echo 0", self.run)                       # the old fallback that turned a failed count into zero
        self.assertNotIn("2>/dev/null", self.run)
        self.assertIn('if [ -s "$S/rust.patch" ]; then', self.run)    # only an empty diff has nothing to list (cargo-mutants prints nothing for it)
        self.assertIn("assert isinstance(d, list)", self.run)

    def test_the_go_count_is_believed_only_from_a_finished_run(self):
        self.assertNotIn("grep -c", self.run)
        self.assertIn("go=$(python3 tools/go_list_count.py", self.run)
        # the one `|| true` left is on go-mutesting itself, whose exit status is not the answer; its output is, and the counter refuses a missing summary
        ors = [line for line in self.run.splitlines() if "|| true" in line]
        self.assertEqual(len(ors), 1)
        self.assertIn("go-list.out", ors[0])
