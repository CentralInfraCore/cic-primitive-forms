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


if __name__ == "__main__":
    unittest.main()
