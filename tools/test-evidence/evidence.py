#!/usr/bin/env python3
"""Command line of the test evidence tools (docs/TEST-EVIDENCE.md).

    evidence.py canon < DOCUMENT          the canonical form of a JSON document, or the class of its refusal
    evidence.py hash FILE                 the content hash of a record or any canonical document
    evidence.py key --root DIR --env FILE the validity key of a checkout in an environment
    evidence.py check-gh                  that `gh attestation verify` knows the flags the verifier uses
    evidence.py pins [--root DIR]         that every action the workflows use is pinned to a commit
    evidence.py env --fields F --out FILE [--toolchain T]
                                          the part of the execution environment a job is responsible for
    evidence.py selftest                  the unit tests and probes, and the Go implementation on the vectors

    evidence.py collect --sha S --trigger push|dispatch --out DIR      find the FULL runs of S, wait, gather jobs and artifacts
    evidence.py build --collected DIR --root DIR --event E [--slot D] --out DIR     make the record of the run (a file named by its hash)
    evidence.py publish --record FILE --origin URL                     add it to the ci-evidence branch (token in $EVIDENCE_TOKEN)
    evidence.py verify-published --evidence DIR --root DIR --record NAME   read the branch back as a verifier would
    evidence.py watch --evidence DIR --root DIR                       exit 3 if the latest due slot has no record, 4 if that cannot be decided

These are the steps of .github/workflows/evidence.yml (docs/EVIDENCE-PUBLISHING.md).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build  # noqa: E402
import canon  # noqa: E402
import collect  # noqa: E402
import envcap  # noqa: E402
import pipeline  # noqa: E402
import publish  # noqa: E402
import key as keymod  # noqa: E402
import pins  # noqa: E402
import verify  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("canon")
    h = sub.add_parser("hash")
    h.add_argument("file")
    k = sub.add_parser("key")
    k.add_argument("--root", required=True)
    k.add_argument("--env", required=True)
    sub.add_parser("check-gh")
    pn = sub.add_parser("pins")
    pn.add_argument("--root", default=os.path.abspath(os.path.join(HERE, "..", "..")))
    ev = sub.add_parser("env")
    ev.add_argument("--fields", required=True)
    ev.add_argument("--out", required=True)
    ev.add_argument("--toolchain")
    c = sub.add_parser("collect")
    c.add_argument("--sha", required=True)
    c.add_argument("--trigger", required=True)
    c.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    c.add_argument("--out", required=True)
    b = sub.add_parser("build")
    b.add_argument("--collected", required=True)
    b.add_argument("--root", required=True)
    b.add_argument("--event", required=True)
    b.add_argument("--slot", default="")
    b.add_argument("--out", required=True)
    pb = sub.add_parser("publish")
    pb.add_argument("--record", required=True)
    pb.add_argument("--origin", required=True)
    vp = sub.add_parser("verify-published")
    vp.add_argument("--evidence", required=True)
    vp.add_argument("--root", required=True)
    vp.add_argument("--record", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--evidence", required=True)
    w.add_argument("--root", required=True)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "canon":
            sys.stdout.buffer.write(canon.canonicalize(canon.parse_strict(sys.stdin.buffer.read())))
        elif a.cmd == "hash":
            with open(a.file, "rb") as fh:
                print(hashlib.sha256(canon.canonicalize(canon.parse_strict(fh.read()))).hexdigest())
        elif a.cmd == "key":
            with open(a.env, "rb") as fh:
                env = canon.parse_strict(fh.read())
            print(keymod.validity_key(a.root, env)[0])
        elif a.cmd == "collect":
            return cmd_collect(a)
        elif a.cmd == "build":
            return cmd_build(a)
        elif a.cmd == "publish":
            return cmd_publish(a)
        elif a.cmd == "verify-published":
            return cmd_verify_published(a)
        elif a.cmd == "watch":
            return cmd_watch(a)
        elif a.cmd == "env":
            return envcap.main(["--fields", a.fields, "--out", a.out] + (["--toolchain", a.toolchain] if a.toolchain else []))
        elif a.cmd == "pins":
            actions, problems = pins.scan(a.root)
            for line in problems:
                print(line)
            if problems or not actions:
                print("pins: %d problem(s), %d action(s) pinned" % (len(problems) or 1, len(actions)))
                return 1
            print("pins: %d actions, all pinned to a commit" % len(actions))
        elif a.cmd == "check-gh":
            missing = verify.GhAttestation().missing_flags()
            if missing:
                print("gh attestation verify does not list: %s" % ", ".join(missing))
                return 1 if os.environ.get("CIC_REQUIRE_GH_ATTESTATION") else 0
            print("gh attestation verify knows %s" % ", ".join(verify.GH_FLAGS))
        else:
            return selftest()
    except canon.CanonError as e:
        print(e.kind, file=sys.stderr)
        return 1
    except (collect.CollectError, build.BuildError, publish.PublishError, pipeline.PipelineError) as e:
        print("%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 3
    except envcap.EnvError as e:
        print("%s %s" % (e.kind, e), file=sys.stderr)
        return 1
    except keymod.KeyError_ as e:
        print("%s %s" % (e.kind, e), file=sys.stderr)
        return 1
    return 0


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc)


def cmd_collect(a):
    os.makedirs(a.out, exist_ok=True)
    jobs, artifacts, ids = collect.collect(collect.GhCli(), a.repo, a.sha, a.trigger, os.path.join(a.out, "artifacts"))
    with open(os.path.join(a.out, "jobs.json"), "w") as fh:
        json.dump(jobs, fh, sort_keys=True)
    print("collected %d jobs of runs %s and the artifacts %s" % (len(jobs), ids, ", ".join(artifacts)))
    return 0


def cmd_build(a):
    now = _now()
    git = verify.GitRepo(a.root)
    target, run, slot, _ = pipeline.make_inputs(os.environ, a.event, a.slot, now, git)
    with open(os.path.join(a.collected, "jobs.json")) as fh:
        jobs = json.load(fh)
    with open(os.path.join(HERE, "required-checks.json")) as fh:
        expected = json.load(fh)
    rec, raw, name, kind = build.build(os.path.join(a.collected, "artifacts"), jobs, a.root, target, run, slot, now, expected)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, name), "wb") as fh:
        fh.write(raw)
    with open(os.path.join(a.out, "kind"), "w") as fh:
        fh.write(kind)
    print("%s %s outcome=%s anchor=%s slot=%s" % (kind, name, rec["outcome"], rec["anchor"], rec["slot"]))
    return 0


def cmd_publish(a):
    now = _now()
    with open(a.record, "rb") as fh:
        raw = fh.read()
    name = os.path.basename(a.record)
    kind = open(os.path.join(os.path.dirname(a.record), "kind")).read().strip()
    sha = publish.publish(a.origin, name, raw, kind, now.strftime("%Y-%m-%d"), token=os.environ.get("EVIDENCE_TOKEN"))
    print("published %s as %s in %s" % (name, kind, sha))
    return 0


def _read_ctx(a):
    required = json.load(open(os.path.join(HERE, "required-checks.json")))
    git = verify.GitRepo(a.root)
    return verify.Context(now=_now(), target_commit=os.environ.get("GITHUB_SHA", ""), root=a.root, environment={}, is_ancestor=git.is_ancestor,
                          attestation=verify.GhAttestation(), expected_checks=required, tree_of=git.tree_of, parents_of=git.parents_of)


def cmd_verify_published(a):
    got = pipeline.verify_published(a.evidence, _now(), _read_ctx(a), must_contain=a.record)
    print("verified %d record(s) of the last days, among them %s" % (len(got), a.record))
    return 0


WATCH_NO_RECORD = 3   # the one answer the watchdog acts on: the latest due slot has no verified record
WATCH_UNDECIDED = 4   # the branch could not be read or a listed record does not verify: nobody is asked to run anything


def cmd_watch(a):
    try:
        slot, present = pipeline.watch(a.evidence, _now(), _read_ctx(a))
    except pipeline.PipelineError as e:
        # not "no record": a record that is listed and does not verify must be looked at, not answered with another run
        print("%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return WATCH_UNDECIDED
    print("slot %s: %s" % (slot, "has a record" if present else "has no record"))
    return 0 if present else WATCH_NO_RECORD


def selftest():
    import unittest
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    rc = 0 if result.wasSuccessful() else 1
    go = subprocess.run(["go", "run", ".", os.path.join(HERE, "vectors", "canon.json")], cwd=os.path.join(HERE, "canon-go"))
    return rc or go.returncode


if __name__ == "__main__":
    sys.exit(main())
