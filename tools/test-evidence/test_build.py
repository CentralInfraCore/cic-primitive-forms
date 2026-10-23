"""Probes of the record builder: a record is made only from a run that can be described completely, and its verdict is the numbers'."""
import collections
import copy
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build  # noqa: E402
import canon  # noqa: E402
import record  # noqa: E402
import test_evidence as te  # noqa: E402

A = "a" * 40
PINNED = "      - uses: actions/checkout@%s # v4.4.0\n" % A
FULL_ENV = {"rustc": "1.99.0", "cargo": "1.99.0", "go": "go1.27.2", "python": "3.12.3",
            "runner": {"image_os": "ubuntu24", "image_version": "20261001.1", "os": "Linux", "arch": "X64"},
            "tools": {"cargo-llvm-cov": "0.9.1", "cargo-mutants": "27.1.0", "cargo-fuzz": "0.13.2", "go-mutesting": "0.0.0-20261002081621-9ce5f731e321"}}
NOW = datetime.datetime(2026, 10, 20, 4, 0, 0, tzinfo=datetime.timezone.utc)


def write(base, rel, text):
    p = os.path.join(base, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text if isinstance(text, str) else json.dumps(text))



def load_json(path):
    with open(path) as fh:
        return json.load(fh)


class Run:
    """The leavings of one FULL run: artifacts, jobs, a checkout. Knobs make each broken run."""

    def __init__(self, tc):
        self.tc = tc
        self.fx = te.Fixture(tc)
        self.fx.write(".github/workflows/p1.yml", "jobs:\n  j:\n    steps:\n" + PINNED)
        self.art = tempfile.mkdtemp()
        tc.addCleanup(shutil.rmtree, self.art, True)
        self.jobs = [{"name": n, "conclusion": "success", "html_url": "https://github.com/x/y/actions/runs/1/job/%d" % i, "run_id": 1}
                     for i, n in enumerate(te.CHECKS)]
        self.target = {"kind": "main", "commit": te.C1, "tree": te.TREE, "base_commit": None, "head_commit": None}
        self.run = {"repository": "CentralInfraCore/cic-primitive-forms", "workflow_path": ".github/workflows/evidence.yml",
                    "ref": "refs/heads/main", "run_id": 7, "run_attempt": 1, "commit": te.C1}
        self.rust_lines = (98, 100)
        self.go_lines = (100, 100)
        self.rust_mut = (59, 1, 3)   # killed, survived, unviable over four shards
        self.go_mut = (60, 2, 1)
        self.branches = {"rust": (97, 100), "go": (100, 100)}
        self.lay_out()

    def lay_out(self):
        a = self.art
        write(a, "lines/rust-lines.json", {"data": [{"files": [{"filename": "/r/rust/src/a.rs", "summary": {"lines": {"covered": self.rust_lines[0], "count": self.rust_lines[1]}}}]}]})
        write(a, "lines/go-lines.out", "mode: set\nx/a.go:1.1,2.2 %d 1\nx/a.go:3.1,4.2 %d 0\n" % (self.go_lines[0], self.go_lines[1] - self.go_lines[0]))
        killed, survived, unviable = self.rust_mut
        names = ["src/a.rs:%d:1: replace x with y in f%d" % (i, i) for i in range(killed + survived + unviable)]
        summary = ["MissedMutant"] * survived + ["Unviable"] * unviable + ["CaughtMutant"] * killed
        write(a, "quality-listed/rust-listed.json", [{"name": n} for n in names])
        for s in range(4):
            mine = range(len(names))[s::4]
            outs = [{"scenario": "Baseline", "summary": "Success"}] + [
                {"scenario": {"Mutant": {"name": names[i]}}, "summary": summary[i]} for i in mine]
            n = collections.Counter(summary[i] for i in mine)
            write(a, "rust-mutants-%d/outcomes.json" % s, {"caught": n["CaughtMutant"], "missed": n["MissedMutant"], "timeout": 0,
                                                            "unviable": n["Unviable"], "total_mutants": len(mine), "outcomes": outs})
        gk, gs, gu = self.go_mut
        ids = ["g%03d" % i for i in range(gk + gs + gu)]
        verdict = ["survived"] * gs + ["uncompilable"] * gu + ["killed"] * gk
        for s in range(6):
            rows = ["%s\t%d\t6\t%s" % (m, s, verdict[i] if i % 6 == s else "offshard") for i, m in enumerate(ids)]
            write(a, "go-ledger-%d/ledger" % s, "\n".join(rows) + "\n")
        for lang in ("rust", "go"):
            c, t = self.branches[lang]
            write(a, "branch-reports/%s-report.json" % lang, {"language": lang, "metric": "branch", "source": {"commit": self.run["commit"], "files": []}, "totals": {"covered": c, "total": t}})
        parts = [{k: FULL_ENV[k] for k in ("rustc", "cargo", "python", "runner")}, {"go": FULL_ENV["go"], "tools": FULL_ENV["tools"], "runner": FULL_ENV["runner"]}]
        for i, p in enumerate(parts):
            write(a, "env-%d/env.json" % i, p)

    def build(self):
        slot = "2026-10-20" if self.target["kind"] == "main" else None
        return build.build(self.art, self.jobs, self.fx.root, self.target, self.run, slot, NOW, te.CHECKS)

    def job(self, name):
        return next(j for j in self.jobs if j["name"] == name)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.r = Run(self)

    def refuses(self, kind):
        with self.assertRaises(build.BuildError) as cm:
            self.r.build()
        self.assertEqual(cm.exception.kind, kind)

    def test_a_good_run(self):
        rec, raw, name, kind = self.r.build()
        self.assertEqual((rec["outcome"], rec["anchor"], kind), ("pass", True, "full"))
        q = rec["quality_full"]
        self.assertEqual(q["rust_lines"], {"covered": 98, "total": 100})
        self.assertEqual(q["go_statements"], {"covered": 100, "total": 100})
        self.assertEqual(q["rust_mutation"], {"killed": 59, "survived": 1, "unviable": 3})
        self.assertEqual(q["go_mutation"], {"killed": 60, "survived": 2, "uncompilable": 1})
        self.assertEqual(q["shards"], {"rust": 4, "go": 6})
        self.assertEqual((q["rust_branches"], q["go_branches"]), ({"covered": 97, "total": 100}, {"covered": 100, "total": 100}))
        self.assertEqual(q["gate_verdict"], "PASS")
        self.assertEqual(rec["environment"]["actions"], {"actions/checkout": A})
        self.assertEqual(canon.read_canonical(raw), rec)
        self.assertEqual(name, __import__("hashlib").sha256(raw).hexdigest() + ".json")
        self.assertEqual([a["name"] for a in rec["artifacts"]], sorted(a["name"] for a in rec["artifacts"]))
        self.assertTrue(all(len(a["sha256"]) == 64 for a in rec["artifacts"]))
        record.validate(rec, te.CHECKS)

    def test_the_record_is_the_same_whoever_builds_it(self):
        _, raw1, name1, _ = self.r.build()
        _, raw2, name2, _ = self.r.build()
        self.assertEqual((raw1, name1), (raw2, name2))

    def test_a_failed_required_check_is_an_audit_record(self):
        self.r.job("parity")["conclusion"] = "failure"
        rec, _, _, kind = self.r.build()
        self.assertEqual((rec["outcome"], rec["anchor"], kind), ("fail", False, "audit"))
        self.assertEqual(rec["quality_full"]["gate_verdict"], "PASS")  # the numbers were fine; a check failed
        for conclusion in ("cancelled", "skipped", "timed_out"):
            self.r.job("parity")["conclusion"] = conclusion
            self.assertEqual(self.r.build()[0]["outcome"], "fail")

    def test_the_gate_and_its_numbers_must_agree(self):
        self.r.rust_lines = (50, 100)
        self.r.lay_out()
        self.refuses("GATE_DISAGREES")                       # the job says success, the numbers say fail
        self.r.job("quality")["conclusion"] = "failure"
        rec, _, _, kind = self.r.build()                      # job and numbers agree: a genuine failed run
        self.assertEqual((rec["outcome"], rec["quality_full"]["gate_verdict"], kind), ("fail", "FAIL", "audit"))
        self.r.rust_lines = (98, 100)
        self.r.lay_out()
        self.refuses("GATE_DISAGREES")                       # the numbers pass, the job failed: nobody knows why
        self.r.job("quality")["conclusion"] = "success"
        self.r.branches["go"] = (50, 100)
        self.r.lay_out()
        self.refuses("GATE_DISAGREES")                       # the same for the branch gate

    def test_exactly_the_threshold_is_not_a_pass(self):
        self.r.go_lines = (90, 100)
        self.r.lay_out()
        self.refuses("GATE_DISAGREES")
        self.r.job("quality")["conclusion"] = "failure"
        self.assertEqual(self.r.build()[0]["outcome"], "fail")

    def test_jobs(self):
        for label, edit, kind in (("missing", lambda r: r.jobs.remove(r.job("fuzz")), "MISSING_JOB"),
                                  ("twice", lambda r: r.jobs.append(dict(r.job("fuzz"))), "AMBIGUOUS_JOB"),
                                  ("unfinished", lambda r: r.job("fuzz").update(conclusion=None), "JOB_NOT_FINISHED"),
                                  ("a strange conclusion", lambda r: r.job("fuzz").update(conclusion="great"), "JOB_NOT_FINISHED")):
            with self.subTest(label):
                r = Run(self)
                edit(r)
                with self.assertRaises(build.BuildError) as cm:
                    r.build()
                self.assertEqual(cm.exception.kind, kind)

    def test_artifacts_that_are_missing(self):
        for rel in ("lines/rust-lines.json", "lines/go-lines.out", "quality-listed/rust-listed.json", "branch-reports/go-report.json"):
            with self.subTest(rel):
                r = Run(self)
                os.unlink(os.path.join(r.art, rel))
                with self.assertRaises(build.BuildError) as cm:
                    r.build()
                self.assertEqual(cm.exception.kind, "MISSING_ARTIFACT")
        for pattern in ("rust-mutants-*", "go-ledger-*", "env-*"):
            with self.subTest(pattern):
                r = Run(self)
                for d in [d for d in os.listdir(r.art) if d.startswith(pattern[:-1])]:
                    shutil.rmtree(os.path.join(r.art, d))
                with self.assertRaises(build.BuildError):
                    r.build()

    def test_shards_that_are_incomplete_leave_no_record(self):
        shutil.rmtree(os.path.join(self.r.art, "rust-mutants-3"))
        self.refuses("INCOMPLETE_MEASUREMENT")
        r = Run(self)
        shutil.rmtree(os.path.join(r.art, "go-ledger-5"))
        with self.assertRaises(build.BuildError) as cm:
            r.build()
        self.assertEqual(cm.exception.kind, "INCOMPLETE_MEASUREMENT")

    def test_a_mutant_tested_twice_leaves_no_record(self):
        p = os.path.join(self.r.art, "rust-mutants-1", "outcomes.json")
        d = load_json(p)
        d["outcomes"].append(d["outcomes"][1])   # and the totals say so: a report that agrees with itself, only not with the list
        d["total_mutants"] += 1
        d[{"CaughtMutant": "caught", "MissedMutant": "missed", "Unviable": "unviable"}[d["outcomes"][1]["summary"]]] += 1
        write(self.r.art, "rust-mutants-1/outcomes.json", d)
        self.refuses("INCOMPLETE_MEASUREMENT")

    def test_a_report_that_contradicts_itself_leaves_no_record(self):
        p = os.path.join(self.r.art, "rust-mutants-1", "outcomes.json")
        d = load_json(p)
        d["caught"] += 500
        write(self.r.art, "rust-mutants-1/outcomes.json", d)
        self.refuses("UNMEASURED")

    def test_branch_reports_have_to_be_of_this_commit_and_metric(self):
        for label, edit in (("another commit", lambda d: d["source"].update(commit="9" * 40)), ("another metric", lambda d: d.update(metric="line")),
                            ("another language", lambda d: d.update(language="go")), ("a float", lambda d: d["totals"].update(covered=1.5)),
                            ("a bool", lambda d: d["totals"].update(total=True)), ("no totals", lambda d: d.pop("totals"))):
            with self.subTest(label):
                r = Run(self)
                p = os.path.join(r.art, "branch-reports", "rust-report.json")
                d = load_json(p)
                edit(d)
                write(r.art, "branch-reports/rust-report.json", d)
                with self.assertRaises(build.BuildError) as cm:
                    r.build()
                self.assertEqual(cm.exception.kind, "BAD_BRANCH_REPORT")

    def test_the_environment(self):
        write(self.r.art, "env-1/env.json", dict(load_json(os.path.join(self.r.art, "env-1", "env.json")), go="go1.27.3"))
        write(self.r.art, "env-0/env.json", dict(load_json(os.path.join(self.r.art, "env-0", "env.json")), go="go1.27.2"))
        self.refuses("ENVIRONMENT_CONFLICT")
        r = Run(self)
        write(r.art, "env-1/env.json", {"go": "go1.27.2"})   # the tools are nobody's
        with self.assertRaises(build.BuildError) as cm:
            r.build()
        self.assertEqual(cm.exception.kind, "ENVIRONMENT_INCOMPLETE")
        r = Run(self)
        r.fx.write(".github/workflows/p1.yml", "      - uses: actions/checkout@v4\n")
        with self.assertRaises(build.BuildError) as cm:
            r.build()
        self.assertEqual(cm.exception.kind, "ENVIRONMENT_UNPINNED")
        r = Run(self)
        write(r.art, "env-0/env.json", "not json")
        with self.assertRaises(build.BuildError) as cm:
            r.build()
        self.assertEqual(cm.exception.kind, "BAD_ENVIRONMENT")

    def test_the_key_comes_from_the_checkout(self):
        rec, _, _, _ = self.r.build()
        self.assertEqual(rec["validity_key"], self.r.fx.key(rec["environment"])[0])
        self.r.fx.write("tools/quality_gate.py", "changed")
        self.assertNotEqual(self.r.build()[0]["validity_key"], rec["validity_key"])
        os.unlink(os.path.join(self.r.fx.root, "coverage/tools.yaml"))
        os.unlink(os.path.join(self.r.fx.root, "coverage/scope.yaml"))
        self.refuses("KEY_NO_MATCH")

    def test_a_merge_candidate(self):
        self.r.target = {"kind": "merge_candidate", "commit": te.C1, "tree": te.TREE, "base_commit": te.C2, "head_commit": te.C3}
        rec, _, _, _ = self.r.build()
        self.assertEqual(rec["target"]["kind"], "merge_candidate")
        self.r.target["head_commit"] = None
        self.refuses("INVALID_RECORD")

    def test_the_branch_threshold_is_the_gates(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("no PyYAML")
        sys.path.insert(0, os.path.dirname(HERE))
        import coverage_gate
        self.assertEqual(build.BRANCH_THRESHOLD, coverage_gate.THRESHOLD)


if __name__ == "__main__":
    unittest.main()
