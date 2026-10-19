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

Producing and publishing records is not here: that is step B.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import canon  # noqa: E402
import envcap  # noqa: E402
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
    except envcap.EnvError as e:
        print("%s %s" % (e.kind, e), file=sys.stderr)
        return 1
    except keymod.KeyError_ as e:
        print("%s %s" % (e.kind, e), file=sys.stderr)
        return 1
    return 0


def selftest():
    import unittest
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    rc = 0 if result.wasSuccessful() else 1
    go = subprocess.run(["go", "run", ".", os.path.join(HERE, "vectors", "canon.json")], cwd=os.path.join(HERE, "canon-go"))
    return rc or go.returncode


if __name__ == "__main__":
    sys.exit(main())
