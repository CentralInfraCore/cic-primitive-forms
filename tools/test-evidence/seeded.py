#!/usr/bin/env python3
"""Seeded defects of the test evidence tools: the probes must catch an implementation that is deliberately wrong.

A verifier whose probes have never been shown to fail on the defect they exist for proves nothing. For each defect
below this copies the tool directory to a temporary place, applies one textual change (it must match exactly once, so
a stale entry cannot pass silently), runs the unit tests there and requires them to FAIL. The unmodified copy has to
pass first. Nothing in the repository is modified.

    seeded.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# (name, file, [(old, new), ...])
DEFECTS = [
    ("duplicate member names are accepted", "canon.py",
     [('if k in out:\n                raise CanonError("DUPLICATE_KEY", k)', 'if False:\n                raise CanonError("DUPLICATE_KEY", k)')]),
    ("members are sorted by code point, not by UTF-16 code unit", "canon.py",
     [("return s.encode(\"utf-16-be\", \"surrogatepass\")", "return s.encode(\"utf-32-be\")")]),
    ("a document that is not canonical is read as canonical", "canon.py",
     [('if canonicalize(v) != bytes(data):\n        raise CanonError("NOT_CANONICAL")', 'if False:\n        raise CanonError("NOT_CANONICAL")')]),
    ("-0 is a number", "canon.py",
     [('if n == 0:\n                raise CanonError("NUMBER", "-0")', 'if False:\n                raise CanonError("NUMBER", "-0")')]),
    ("a fraction is a number", "canon.py",
     [('if k < len(self.s) and self.s[k] in ".eE":\n            raise CanonError("NUMBER", "fraction or exponent")', 'if False:\n            raise CanonError("NUMBER", "fraction or exponent")')]),
    ("a lone surrogate escape is read", "canon.py",
     [('if 0xDC00 <= cu <= 0xDFFF:\n                    raise CanonError("LONE_SURROGATE")', 'if False:\n                    raise CanonError("LONE_SURROGATE")')]),
    ("the name of a record is not checked against its hash", "verify.py",
     [('if filename != digest + ".json":', 'if False:')]),
    ("the attestation result is ignored", "verify.py",
     [('if ctx.attestation.verify(path, digest, ctx.trust, rec["run"]["commit"]) is not True:', 'if False:')]),
    ("anything truthy passes as an attestation", "verify.py",
     [('verify(path, digest, ctx.trust, rec["run"]["commit"]) is not True', 'verify(path, digest, ctx.trust, rec["run"]["commit"]) is False')]),
    ("the attestation is asked about the wrong commit", "verify.py",
     [('ctx.attestation.verify(path, digest, ctx.trust, rec["run"]["commit"])', 'ctx.attestation.verify(path, digest, ctx.trust, ctx.target_commit)')]),
    ("gh is asked for any workflow", "verify.py",
     [('"--signer-workflow", "%s/%s" % (trust.repository, trust.signer_workflow),', '')]),
    ("a missing gh is a pass", "verify.py",
     [("        except (OSError, subprocess.SubprocessError):\n            return False\n        return done.returncode == 0", "        except (OSError, subprocess.SubprocessError):\n            return True\n        return done.returncode == 0")]),
    ("the validity key is not compared", "verify.py",
     [('if rec["validity_key"] != want:', 'if False:')]),
    ("the key is not checked against the inputs written in the record", "verify.py",
     [('if keymod.key_of(rec["validity_inputs"]) != rec["validity_key"]:', 'if False:')]),
    ("the ancestry is not checked", "verify.py",
     [('if commit != ctx.target_commit and not ctx.is_ancestor(commit, ctx.target_commit):', 'if False:')]),
    ("a record of the future is fresh", "verify.py",
     [('if age > ctx.max_age or age < datetime.timedelta(0):', 'if age > ctx.max_age:')]),
    ("age is not checked", "verify.py",
     [('if age > ctx.max_age or age < datetime.timedelta(0):', 'if False:')]),
    ("a failed record is a baseline", "verify.py",
     [('if rec["outcome"] != "pass" or rec["anchor"] is not True:', 'if False:')]),
    ("a missing artifact is fine", "verify.py",
     [("        if data is None:\n            raise VerifyError(\"ARTIFACT_MISSING\", ref[\"artifact\"])", "        if data is None:\n            continue")]),
    ("an artifact digest is not checked", "verify.py",
     [('if hashlib.sha256(data).hexdigest() != ref["digest"] or len(data) != ref["size"]:', 'if False:')]),
    ("a missed run does not break the chain", "verify.py",
     [('if not any(v.rec["slot"] == slot for v in mains):', 'if False:')]),
    ("a failed newest record does not break the chain", "verify.py",
     [('if newest.rec["outcome"] != "pass" or newest.rec["anchor"] is not True:', 'if False:')]),
    ("a pass before a failure is a baseline", "verify.py",
     [("and (last_fail is None or v.measured_at > last_fail)]", "]")]),
    ("records of the same second that disagree are not uncertain", "verify.py",
     [('if len({v.rec["outcome"] for v in ties}) > 1:', 'if False:')]),
    ("a listed record that is missing is skipped", "verify.py",
     [("        if raw is None:\n            return Choice(None, \"UNCERTAIN\", \"listed record %s is missing\" % digest)", "        if raw is None:\n            continue")]),
    ("a listed record that does not verify is skipped", "verify.py",
     [("        except VerifyError as e:\n            return Choice(None, \"UNCERTAIN\", \"listed record %s does not verify: %s\" % (digest, e.code))", "        except VerifyError as e:\n            continue")]),
    ("the chosen baseline is not verified for the target", "verify.py",
     [("        verify_baseline(raw, choice.baseline.digest + \".json\", ctx)", "        pass")]),
    ("an extra member in a record is allowed", "record.py",
     [('    if extra:\n        _fail("EXTRA"', '    if False:\n        _fail("EXTRA"')]),
    ("a missing member in a record is allowed", "record.py",
     [('    if missing:\n        _fail("MISSING"', '    if False:\n        _fail("MISSING"')]),
    ("an action may be named by a tag", "record.py",
     [('"actions": _map_of(_re(SHA1)),', '"actions": _map_of(_str),')]),
    ("a toolchain may be a label", "record.py",
     [('"rustc": version, "cargo": version,', '"rustc": _str, "cargo": version,')]),
    ("the anchor may disagree with the outcome", "record.py",
     [('    if rec["anchor"] != (rec["outcome"] == "pass"):', '    if False:')]),
    ("a pass with a failed required check", "record.py",
     [('    if rec["outcome"] == "pass" and not green:', '    if False:')]),
    ("the required checks may differ from the expected ones", "record.py",
     [("    if expected_checks is not None and sorted", "    if False and sorted")]),
    ("a pattern that matches nothing is fine", "key.py",
     [("        if n == 0:\n            raise KeyError_(\"NO_MATCH\", p)", "        if n == 0:\n            pass")]),
    ("a symlink is followed", "key.py",
     [("            if os.path.islink(full) or not os.path.isfile(full):", "            if not os.path.isfile(full):")]),
    ("the environment is not part of the key", "key.py",
     [('    out["environment"] = canon.content_hash(environment)', '    out["environment"] = "0" * 64')]),
    ("the selector version is not part of the key", "key.py",
     [('    out["selector"] = _sha("selector-version:%d" % SELECTOR_VERSION)', '    out["selector"] = _sha("selector-version:1")')]),
    ("the pattern list is not part of the key", "key.py",
     [('    out["patterns"] = canon.content_hash(patterns)', '    out["patterns"] = "0" * 64')]),
    ("a verdict that the numbers do not give is accepted", "record.py",
     [('    if q["gate_verdict"] != ("PASS" if numbers_pass(q) and q["completeness"] == "complete" else "FAIL"):', '    if False:')]),
    ("exactly the threshold is a pass", "record.py",
     [("    return total > 0 and covered * 100 > total * threshold", "    return total > 0 and covered * 100 >= total * threshold")]),
    ("a measurement of nothing is a pass", "record.py",
     [("    return total > 0 and covered * 100 > total * threshold", "    return total == 0 or covered * 100 > total * threshold")]),
    ("survivors do not count against the mutation score", "record.py",
     [('above(q[k]["killed"], q[k]["killed"] + q[k]["survived"], t["mutation"])', 'above(q[k]["killed"], q[k]["killed"], t["mutation"])')]),
    ("unviable mutants count against the mutation score", "record.py",
     [('above(q[k]["killed"], q[k]["killed"] + q[k]["survived"], t["mutation"])', 'above(q[k]["killed"], q[k]["killed"] + q[k]["survived"] + q[k]["unviable" if k == "rust_mutation" else "uncompilable"], t["mutation"])')]),
    ("a record made by a run on another commit is accepted", "record.py",
     [('    if rec["target"]["commit"] != rec["run"]["commit"]:', '    if False:')]),
    ("the tree of the commit is not compared", "verify.py",
     [('    if tree != t["tree"]:', '    if False:')]),
    ("an unknown commit is not noticed", "verify.py",
     [('    if tree is None:', '    if False:')]),
    ("the parents of a merge candidate are not compared", "verify.py",
     [('        if parents != [t["base_commit"], t["head_commit"]]:', '        if False:')]),
    ("the parents of a merge candidate may come in any order", "verify.py",
     [('        if parents != [t["base_commit"], t["head_commit"]]:', '        if sorted(parents or []) != sorted([t["base_commit"], t["head_commit"]]):')]),
    ("no way to ask git is a pass", "verify.py",
     [('    if ctx.tree_of is None or ctx.parents_of is None:', '    if False:')]),
    ("any argument reaches git", "verify.py",
     [('        if not isinstance(sha, str) or not self.FULL.match(sha):', '        if not isinstance(sha, str):')]),
    ("a tree id is taken for a commit", "verify.py",
     [('        return rc == 0 and kind == "commit"', '        return rc == 0')]),
    ("a symlink to a directory is skipped without a word", "key.py",
     [('                if could_hold_selected(rel) and not excluded(rel):', '                if False:')]),
    ("a symlink to a directory in an excluded place is refused", "key.py",
     [('                if could_hold_selected(rel) and not excluded(rel):', '                if could_hold_selected(rel):')]),
    ("a tag is accepted as a pin", "pins.py",
     [('FULL = re.compile(r"[0-9a-f]{40}\\Z")', 'FULL = re.compile(r".+")')]),
    ("a short id is accepted as a pin", "pins.py",
     [('FULL = re.compile(r"[0-9a-f]{40}\\Z")', 'FULL = re.compile(r"[0-9a-f]{7,40}\\Z")')]),
    ("an upper case id is accepted as a pin", "pins.py",
     [('FULL = re.compile(r"[0-9a-f]{40}\\Z")', 'FULL = re.compile(r"[0-9a-fA-F]{40}\\Z")')]),
    ("an action without a revision is accepted", "pins.py",
     [('if not at or not name_ or "/" not in name_:', 'if False:')]),
    ("an image is accepted", "pins.py",
     [('if ref.startswith("docker://"):', 'if False:')]),
    ("one action at two revisions is accepted", "pins.py",
     [('if seen.setdefault(name_, rev) != rev:', 'if False:')]),
    ("the composite actions are not read", "pins.py",
     [('        dirnames.sort()', '        dirnames[:] = [d for d in sorted(dirnames) if d != "actions"]')]),
    ("a gate that disagrees with its numbers is believed", "build.py",
     [('if name in expected_checks and (conclusion[name] == "success") != ok:', 'if False:')]),
    ("a branch report of another commit is accepted", "build.py",
     [('and rep["source"]["commit"] == commit', '')]),
    ("a job that did not finish is a pass", "build.py",
     [('if j.get("conclusion") not in record.CONCLUSIONS:', 'if False:')]),
    ("a missing job is skipped", "build.py",
     [('        if not found:\n            raise BuildError("MISSING_JOB", name)', '        if not found:\n            continue')]),
    ("two jobs of one name are taken for one", "build.py",
     [('        if len(found) > 1:\n            raise BuildError("AMBIGUOUS_JOB", name)', '        if len(found) > 1:\n            pass')]),
    ("incomplete shards still make a record", "build.py",
     [('kind = "INCOMPLETE_MEASUREMENT" if r.reason == "INCOMPLETE_SHARDS" else "UNMEASURED"\n        raise BuildError(kind, "%s: %s" % (r.reason, r.detail))', 'return (0, 1)')]),
    ("the verdict is the job\'s, not the numbers\'", "build.py",
     [('    q["gate_verdict"] = "PASS" if green else "FAIL"', '    q["gate_verdict"] = "PASS"')]),
    ("a conflict in the environment is overwritten", "envcap.py",
     [('if path[-1] in node and node[path[-1]] != value:', 'if False:')]),
    ("an unpinned action is tolerated in the environment", "envcap.py",
     [('if problems or not actions:', 'if not actions:')]),
    ("a label is a version", "envcap.py",
     [('    if not record.VERSION.match(value):', '    if False:')]),
    ("a runner that does not say its image is accepted", "envcap.py",
     [('environ["ImageVersion"]', 'environ.get("ImageVersion", "unknown")')]),
    ("workflows are not part of the key", "key-inputs.json",
     [('  ".github/workflows/**",\n', '')]),
    ("a push is a force push", "publish.py",
     [('git("push", "origin", "HEAD:refs/heads/%s" % branch, check=False)', 'git("push", "--force", "origin", "HEAD:refs/heads/%s" % branch, check=False)')]),
    ("the token travels in the arguments", "publish.py",
     [('git("push", "origin", "HEAD:refs/heads/%s" % branch, check=False)', 'git("push", "https://x-access-token:%s@github.com/x/y" % token, "HEAD:refs/heads/%s" % branch, check=False)')]),
    ("a rejected push is given up on", "publish.py",
     [('            _clear(work)\n        raise PublishError("RACE"', '            raise PublishError("RACE", "")\n        raise PublishError("RACE"')]),
    ("any refusal of the push is retried", "publish.py",
     [('            if not _rejected(err):', '            if False:')]),
    ("a record with other content is overwritten", "publish.py",
     [('            if fh.read() != raw:', '            if False:')]),
    ("a failed record is published as a pass", "publish.py",
     [('(rec.get("outcome") == "pass") != (kind == "full")', 'False')]),
    ("a run of another commit is collected", "collect.py",
     [('            if run["head_sha"] != sha:\n                raise CollectError("RUN_FOR_ANOTHER_COMMIT", "%s ran for %s, the commit is %s" % (workflow, run["head_sha"], sha))', '            pass'),
      ('        if run["head_sha"] != sha:\n            raise CollectError("RUN_FOR_ANOTHER_COMMIT", "%s: %s" % (wf, run["head_sha"]))', '        pass')]),
    ("an expired artifact is used", "collect.py",
     [('            raise CollectError("ARTIFACT_EXPIRED", a["name"])', '            continue')]),
    ("two artifacts of one name are taken for one", "collect.py",
     [('        raise CollectError("DUPLICATE_ARTIFACT", ", ".join(names))', '        pass')]),
    ("a slot of the future is accepted", "pipeline.py",
     [('    if day > today:', '    if False:')]),
    ("a slot of any age is accepted", "pipeline.py",
     [('    if (today - day).days > max_age_days:', '    if False:')]),
    ("evidence is made on any ref", "pipeline.py",
     [('    if ref != "refs/heads/main":', '    if False:')]),
    ("a push may stand for a slot", "pipeline.py",
     [('    if event == "push" and slot_input:', '    if False:')]),
    ("the workflow of another repository is the signer", "pipeline.py",
     [('        raise PipelineError("WORKFLOW_REF", workflow_ref)', '        pass')]),
    ("the probes wait for the evidence to be switched on", ".github/workflows/evidence-probes.yml",
     [('  branch:\n', "  branch:\n    if: ${{ vars.EVIDENCE_ENABLED == 'true' }}\n")]),
    ("apply does not look at main", "setup/apply.sh",
     [('rules/branches/main', 'rules/branches/other')]),
    ("a record that does not verify is answered with a request for a run", "evidence.py",
     [('        return WATCH_UNDECIDED', '        return WATCH_NO_RECORD')]),
    ("the watchdog asks for a run without a slot", ".github/workflows/evidence.yml",
     [('            [ -n "$slot" ] || { echo "::error::exit 3 without a slot line: not asking for a run"; exit 1; }\n', '')]),
    ("the watchdog takes anything for a slot", ".github/workflows/evidence.yml",
     [("\\([0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}\\): has no record$/", "\\([0-9-]*\\):.*/")]),
    ("the watchdog hides a failure it cannot read", ".github/workflows/evidence.yml",
     [('            exit $rc\n          fi', '            exit 0\n          fi')]),
    ("the evidence workflow can be started by a pull request", ".github/workflows/evidence.yml",
     [('on:\n', 'on:\n  pull_request:\n')]),
    ("the evidence workflow runs without the environment", ".github/workflows/evidence.yml",
     [('    environment: evidence\n', '')]),
    ("the evidence workflow may write the contents", ".github/workflows/evidence.yml",
     [('      contents: read       # the checkout\n      actions: write       # to ask', '      contents: write      # the checkout\n      actions: write       # to ask')]),
    ("the token is held by the whole job", ".github/workflows/evidence.yml",
     [('    environment: evidence\n', '    environment: evidence\n    env:\n      T: ${{ steps.app.outputs.token }}\n')]),
]


def parse_error(path, text):
    """Why the defective file does not parse, or "": Python by compile(), YAML by yaml.safe_load when PyYAML is there."""
    try:
        if path.endswith(".py"):
            compile(text, path, "exec")
        elif path.endswith((".yml", ".yaml")):
            try:
                import yaml
            except ImportError:
                return ""
            yaml.safe_load(text)
    except Exception as e:   # SyntaxError, yaml.YAMLError
        return "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:80])
    return ""


def run_tests(directory):
    env = dict(os.environ)
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", directory, "-p", "test_*.py"],
                       capture_output=True, text=True, env=env, cwd=directory)
    return p.returncode, p.stdout + p.stderr


def main():
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        tools = os.path.join(tmp, "tools")
        work = os.path.join(tools, "test-evidence")
        os.makedirs(tools)
        for gate in ("quality_gate.py", "coverage_gate.py"):  # the builder reads the raw reports with the gate's own functions
            shutil.copy(os.path.join(os.path.dirname(HERE), gate), tools)
        shutil.copytree(HERE, work, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(os.path.dirname(os.path.dirname(HERE)), ".github"), os.path.join(tmp, ".github"))
        rc, out = run_tests(work)
        print("%-62s: %s" % ("baseline (unmodified)", "pass" if rc == 0 else "FAIL"))
        if rc != 0:
            print(out[-2000:])
            return 1
        for name, rel, ops in DEFECTS:
            path = os.path.join(tmp, rel) if rel.startswith(".github/") else os.path.join(work, rel)
            original = open(path, encoding="utf-8").read()
            text = original
            stale = False
            for old, new in ops:
                if text.count(old) != 1:
                    print("%-62s: STALE %r occurs %d times" % (name, old[:50], text.count(old)))
                    stale = True
                    break
                text = text.replace(old, new)
            if stale:
                bad += 1
                continue
            unreadable = parse_error(path, text)
            if unreadable:   # a defect the tests "catch" only because the file does not parse proves nothing (#94)
                print("%-62s: DOES NOT PARSE (%s)" % (name, unreadable))
                bad += 1
                continue
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            rc, out = run_tests(work)
            caught = rc != 0
            print("%-62s: %s" % (name, "CAUGHT" if caught else "NOT CAUGHT"))
            bad += 0 if caught else 1
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
    print("SEEDED DEFECTS: %s" % ("every defect is caught (%d)" % len(DEFECTS) if bad == 0 else "FAILED (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
