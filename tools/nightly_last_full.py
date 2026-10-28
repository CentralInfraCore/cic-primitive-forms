#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Does main need a FULL tonight? (#94)

    nightly_last_full.py --repo OWNER/REPO --head SHA --current RUN_ID    prints run=true or run=false (for $GITHUB_OUTPUT)

The nightly FULL measures the head of main. It is needed when the last nightly whose `p1` reached its verdict (the job `quality`, which joins every
measurement, succeeded or failed: a failed FULL of a commit is not repeated, its incident is open) measured another commit. One job of `p1` that
succeeded does not make a measurement: `scope` is green seconds after the start, and a run cancelled after it, or whose runner was shut down, measured nothing. A 48-hour window ran the FULL on two nights for one commit;
a comparison with the measured commit does not, and it does not depend on when a scheduled run starts. Any doubt - no such run, an
API error - answers `run=true`: a FULL too many costs time, one too few hides a failure.
"""
import argparse
import json
import subprocess
import sys

FULL_JOB = "p1 / quality"   # the job of the called p1 workflow that joins every measurement, as the nightly run names it


def last_full(runs, jobs_of, current):
    """The head commit of the newest completed nightly run (other than `current`) in which the quality job of p1 succeeded or failed, or None."""
    for r in runs:
        if r["id"] == current or r.get("status") != "completed":
            continue
        if any(j["name"] == FULL_JOB and j.get("conclusion") in ("success", "failure") for j in jobs_of(r["id"])):
            return r["head_sha"]
    return None


def gh(path):
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--current", required=True, type=int)
    a = ap.parse_args()
    try:
        runs = gh("repos/%s/actions/workflows/nightly.yml/runs?branch=main&per_page=30" % a.repo)["workflow_runs"]
        last = last_full(runs, lambda i: gh("repos/%s/actions/runs/%d/jobs?per_page=100" % (a.repo, i))["jobs"], a.current)
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as e:
        print("the last FULL could not be read (%s): the FULL runs" % e, file=sys.stderr)
        print("run=true")
        return 0
    if last is None:
        print("no earlier nightly ran the FULL: it runs", file=sys.stderr)
    elif last == a.head:
        print("the last FULL measured %s, the head of main: nothing to do" % last, file=sys.stderr)
    else:
        print("the last FULL measured %s, main is at %s: the FULL runs" % (last, a.head), file=sys.stderr)
    print("run=%s" % ("false" if last == a.head else "true"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
