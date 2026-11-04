# SPDX-License-Identifier: Apache-2.0
"""The limits of tools/parity/run_raw_yaml.py are ENFORCED, not only reported (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, section 3; Unit 3b scope item 6).

A stand-in reader (a Python script) answers every case except one that allocates past the memory limit and one that never answers. With the limits lowered, the runner must KILL the one
that never answers (its process is gone afterwards, not left running in the background), record exactly those two as CRASH and TIMEOUT, restart after each, and keep the answers of the others.

    python3 -W error::ResourceWarning -m unittest tools/parity/test_run_raw_yaml.py
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_raw_yaml as rr  # noqa: E402

READER = r'''
import json, os, sys, time
pidfile = os.environ.get("TEST_PIDFILE")
cases = [json.loads(l) for l in open(os.environ["PARITY_RAW_YAML_IN"]) if l.strip()]
out = open(os.environ["PARITY_RAW_YAML_OUT"], "w")
for c in cases:
    if c["id"] == "boom":
        x = bytearray(2 * 1024 ** 3)          # past the memory limit of the runner
    if c["id"] == "hang":
        if pidfile:
            open(pidfile, "a").write(str(os.getpid()) + "\n")
        time.sleep(3600)                      # never answers: the runner has to kill this process
    out.write(json.dumps({"id": c["id"], "ok": False, "class": "syntax", "code": "SYNTAX"}) + "\n")
    out.flush()
'''


class Enforced(unittest.TestCase):
    def test_a_reader_that_overruns_memory_or_time_is_killed_recorded_per_case_and_the_rest_is_kept(self):
        d = tempfile.mkdtemp()
        script = os.path.join(d, "reader.py")
        pidfile = os.path.join(d, "pids")
        with open(script, "w") as f:
            f.write(READER)

        def launch(inp, outp):
            return [sys.executable, script], d, {"PARITY_RAW_YAML_IN": inp, "PARITY_RAW_YAML_OUT": outp, "TEST_PIDFILE": pidfile}

        lines = [json.dumps({"id": i}) for i in ("a", "boom", "b", "hang", "c")]
        old = (rr.MEMORY, rr.TIMEOUT, rr.STARTUP)
        rr.MEMORY, rr.TIMEOUT, rr.STARTUP = 1024 ** 3, 2, 3
        try:
            out, stats = rr.run_dump(launch, lines, dict(os.environ), d, "t")
        finally:
            rr.MEMORY, rr.TIMEOUT, rr.STARTUP = old
        got = {json.loads(l)["id"]: json.loads(l) for l in out}
        self.assertEqual([json.loads(l)["id"] for l in out], ["a", "boom", "b", "hang", "c"])
        self.assertEqual(got["boom"]["class"], "CRASH")
        self.assertEqual(got["hang"]["class"], "TIMEOUT")
        for ok in ("a", "b", "c"):
            self.assertEqual(got[ok]["class"], "syntax")
        self.assertEqual((stats["crash"], stats["timeout"], stats["restarts"]), (1, 1, 2))
        # the overrun case is not left running: the process that was stuck in it is gone
        time.sleep(0.2)
        with open(pidfile) as f:
            pids = [int(x) for x in f.read().split()]
        self.assertEqual(len(pids), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)


if __name__ == "__main__":
    unittest.main()
