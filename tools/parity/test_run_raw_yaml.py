# SPDX-License-Identifier: Apache-2.0
"""The limits of tools/parity/run_raw_yaml.py are ENFORCED, not only reported (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, section 3; Unit 3b scope item 6).

A stand-in reader (a Python script) answers every case except one that allocates past the memory limit and one that never answers. With the limits lowered, the runner must record
exactly those two as CRASH and TIMEOUT, re-running the failed chunk one case at a time, and keep the answers of the others.

    python3 -W error::ResourceWarning -m unittest tools/parity/test_run_raw_yaml.py
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_raw_yaml as rr  # noqa: E402

READER = r'''
import json, os, sys, time
cases = [json.loads(l) for l in open(os.environ["PARITY_RAW_YAML_IN"]) if l.strip()]
out = []
for c in cases:
    if c["id"] == "boom":
        x = bytearray(2 * 1024 ** 3)          # past the memory limit of the runner
    if c["id"] == "hang":
        time.sleep(3600)
    out.append(json.dumps({"id": c["id"], "ok": False, "class": "syntax", "code": "SYNTAX"}))
open(os.environ["PARITY_RAW_YAML_OUT"], "w").write("".join(l + "\n" for l in out))
'''


class Enforced(unittest.TestCase):
    def test_a_reader_that_overruns_memory_or_time_is_recorded_per_case_and_the_rest_is_kept(self):
        d = tempfile.mkdtemp()
        script = os.path.join(d, "reader.py")
        with open(script, "w") as f:
            f.write(READER)
        lines = [json.dumps({"id": i}) for i in ("a", "boom", "b", "hang", "c")]
        old = (rr.MEMORY, rr.TIMEOUT, rr.SLACK)
        rr.MEMORY, rr.TIMEOUT, rr.SLACK = 1024 ** 3, 2, 3
        try:
            out, stats = rr.run_dump("go", None, lines, dict(os.environ), d, cmd_override=([sys.executable, script], d))
        finally:
            rr.MEMORY, rr.TIMEOUT, rr.SLACK = old
        got = {json.loads(l)["id"]: json.loads(l) for l in out}
        self.assertEqual([json.loads(l)["id"] for l in out], ["a", "boom", "b", "hang", "c"])
        self.assertEqual(got["boom"]["class"], "CRASH")
        self.assertEqual(got["hang"]["class"], "TIMEOUT")
        for ok in ("a", "b", "c"):
            self.assertEqual(got[ok]["class"], "syntax")
        self.assertEqual((stats["crash"], stats["timeout"], stats["rerun_chunks"]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
