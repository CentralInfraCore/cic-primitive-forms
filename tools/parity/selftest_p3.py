#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A gate that cannot fail proves nothing: break the P3 differential outputs in the ways that must turn compare_p3.py red.

    selftest_p3.py INPUTS RUST_OUT GO_OUT
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run(inputs, rust, go):
    return subprocess.run([sys.executable, os.path.join(HERE, "compare_p3.py"), inputs, rust, go], capture_output=True).returncode


def write(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def main():
    inputs, rust_p, go_p = sys.argv[1:4]
    rust = [json.loads(line) for line in open(rust_p, encoding="utf-8")]
    first_ok = next(i for i, r in enumerate(rust) if r["stage"] == "ok")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.jsonl")
        faults = {}

        def broken(change):
            rows = json.loads(json.dumps(rust))
            change(rows)
            return rows

        def layer(rows):
            e = next(iter(rows[first_ok]["rules"].values()))
            e["access"][1] = e["access"][1] + "-x"

        def source(rows):
            e = next(iter(rows[first_ok]["rules"].values()))
            e["modify"][2] = e["modify"][2] + "x"

        def origin(rows):
            rows[first_ok]["origins"][0][1] = "inherited"

        def obj(rows):
            rows[first_ok]["object"] += " "

        def stage(rows):
            rows[first_ok]["stage"] = "resolve"

        faults = {"a layer differs": layer, "a source path differs": source, "an origin differs": origin,
                  "the object differs": obj, "a stage differs": stage}
        bad = 0
        for name, change in faults.items():
            write(p, broken(change))
            code = run(inputs, p, go_p)
            print("%-24s %s" % (name, "RED (as it must)" if code == 1 else "NOT RED"))
            bad += code != 1
        write(p, rust[:-1])
        code = run(inputs, p, go_p)
        print("%-24s %s" % ("a line is missing", "RED (as it must)" if code == 1 else "NOT RED"))
        bad += code != 1
        write(p, rust)
        code = run(inputs, p, go_p)
        print("%-24s %s" % ("unchanged outputs", "green" if code == 0 else "RED (selftest broken)"))
        bad += code != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
