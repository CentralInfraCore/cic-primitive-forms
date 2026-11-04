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
time.sleep(float(os.environ.get("TEST_STARTUP_DELAY", "0")))         # a slow start: not part of any case
out = open(os.environ["PARITY_RAW_YAML_OUT"], "w")                   # the output file is the "ready" signal
cases = [json.loads(l) for l in open(os.environ["PARITY_RAW_YAML_IN"]) if l.strip()]
for c in cases:
    if c["id"] == "slow":
        time.sleep(float(os.environ.get("TEST_SLOW", "0")))
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

        def launch(inp, outp, extra=None):
            return [sys.executable, script], d, dict({"PARITY_RAW_YAML_IN": inp, "PARITY_RAW_YAML_OUT": outp, "TEST_PIDFILE": pidfile}, **(extra or {}))

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

    def drive(self, ids, extra, timeout, startup):
        d = tempfile.mkdtemp()
        script = os.path.join(d, "reader.py")
        with open(script, "w") as f:
            f.write(READER)
        launch = lambda inp, outp: ([sys.executable, script], d, dict({"PARITY_RAW_YAML_IN": inp, "PARITY_RAW_YAML_OUT": outp}, **extra))
        old = (rr.MEMORY, rr.TIMEOUT, rr.STARTUP)
        rr.MEMORY, rr.TIMEOUT, rr.STARTUP = 4 * 1024 ** 3, timeout, startup
        began = time.monotonic()
        try:
            out, stats = rr.run_dump(launch, [json.dumps({"id": i}) for i in ids], dict(os.environ), d, "t")
        finally:
            rr.MEMORY, rr.TIMEOUT, rr.STARTUP = old
        return {json.loads(l)["id"]: json.loads(l) for l in out}, stats, time.monotonic() - began

    def test_the_limit_of_the_first_case_is_the_limit_and_the_start_up_time_is_neither_part_of_it_nor_added_to_it(self):
        # a slow start (3 s) is not held against the case; the first case then gets exactly the limit (2 s), not limit plus start-up grace (2 + 6)
        got, stats, took = self.drive(["slow", "b"], {"TEST_STARTUP_DELAY": "3", "TEST_SLOW": "5"}, timeout=2, startup=6)
        self.assertEqual(got["slow"]["class"], "TIMEOUT")
        self.assertEqual(got["b"]["class"], "syntax")
        # 3 s start twice (the restart) + 2 s limit: well under the 3 + 8 s a limit-plus-grace would have taken for the first case alone, and the case was killed near 2 s
        self.assertLess(took, 3 + 2 + 3 + 3, took)
        self.assertGreater(took, 3 + 2 - 0.5)
        # a case that answers within the limit after a slow start is not a timeout
        got, _, _ = self.drive(["slow", "b"], {"TEST_STARTUP_DELAY": "3", "TEST_SLOW": "1"}, timeout=2, startup=6)
        self.assertEqual({k: v["class"] for k, v in got.items()}, {"slow": "syntax", "b": "syntax"})

    def test_a_reader_that_does_not_start_stops_the_run_it_is_not_a_verdict_on_a_case(self):
        with self.assertRaises(SystemExit):
            self.drive(["a"], {"TEST_STARTUP_DELAY": "30"}, timeout=2, startup=1)


if __name__ == "__main__":
    unittest.main()
