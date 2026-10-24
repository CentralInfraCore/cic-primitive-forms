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
        self.assertEqual(self.jobs["scope"]["outputs"], {"mode": "${{ steps.s.outputs.mode }}", "fuzz_matrix": "${{ steps.s.outputs.fuzz_matrix }}"})

    def test_the_heavy_jobs_do_not_run_on_a_light_push(self):
        for name in ("seeded-defects", "evidence-selftest"):
            self.assertIn("scope", _needs(self.jobs[name]), name)
            self.assertEqual(self.jobs[name]["if"], "${{ needs.scope.outputs.mode != 'light' }}", name)
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
        self.assertIn('full) test "$PLAN" = success', self.text)
        self.assertIn('diff) test "$PLAN" = skipped', self.text)


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
        self.assertIn('diff) test "$PLAN" = skipped && test "$DIFF_PLAN" = success', self.text)
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


def _needs(job):
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else n


if __name__ == "__main__":
    unittest.main()
