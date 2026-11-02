"""Find the evidence an earlier run left: the newest successful run of the proof-plan workflow on main whose commit is an ancestor of HEAD.

    python3 tools/proof-plan/find_prev.py --repo OWNER/NAME --workflow proof-plan.yml --out DIR [--head HEAD] [--branch main] [--branch PRBRANCH]

Not only the last commit: the nearest ancestor that has evidence (a run can be missing, expired or failed). The artifact is `proof-evidence`
(evidence.json). `--branch` is repeatable (default main): a run of the pull request's own branch is as valid as one of main by the content rules (its
commit must be an ancestor), but it is evidence nobody has authenticated, which is acceptable only while nothing depends on it (report only) and
only for a branch of this repository; the workflow adds it for those pull requests alone. Prints the commit found, or nothing; exit 0 either way: no evidence is a normal state (the planner then says NO_EVIDENCE).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys


def pick(runs, is_ancestor):
    """The first run (newest first) whose head commit is an ancestor of the head. Pure."""
    for r in runs:
        if r.get("conclusion") in (None, "success") and is_ancestor(r["headSha"]):
            return r
    return None


def locate(directory):
    """Make `evidence.json` sit at the root of `directory`: upload-artifact keeps the common parent of several paths, so an artifact can hold
    it in a subdirectory (out/evidence.json). True if it is there afterwards."""
    root = os.path.join(directory, "evidence.json")
    if os.path.exists(root):
        return True
    for base, _dirs, files in os.walk(directory):
        if "evidence.json" in files:
            shutil.copy(os.path.join(base, "evidence.json"), root)
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--branch", action="append")
    a = ap.parse_args()
    runs = []
    for branch in a.branch or ["main"]:
        ls = subprocess.run(["gh", "run", "list", "--repo", a.repo, "--workflow", a.workflow, "--branch", branch, "--status", "success",
                             "--limit", str(a.limit), "--json", "databaseId,headSha,conclusion,createdAt"], capture_output=True, text=True)
        if ls.returncode:
            print("no run list for %s (%s)" % (branch, ls.stderr.strip()[:200]), file=sys.stderr)
            continue
        runs += json.loads(ls.stdout or "[]")
    runs.sort(key=lambda r: r.get("createdAt", ""), reverse=True)  # the newest first, whatever the branch
    anc = lambda sha: subprocess.run(["git", "merge-base", "--is-ancestor", sha, a.head], capture_output=True).returncode == 0
    for _ in range(len(runs)):
        r = pick(runs, anc)
        if r is None:
            break
        os.makedirs(a.out, exist_ok=True)
        d = subprocess.run(["gh", "run", "download", str(r["databaseId"]), "--repo", a.repo, "-n", "proof-evidence", "-D", a.out],
                           capture_output=True, text=True)
        if d.returncode == 0 and locate(a.out):
            print(r["headSha"])
            return 0
        runs = [x for x in runs if x["databaseId"] != r["databaseId"]]  # no artifact (expired, or none): the next ancestor
    print("no evidence found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
