"""Find the evidence an earlier run left: the newest successful run of the proof-plan workflow on main whose commit is an ancestor of HEAD.

    python3 tools/proof-plan/find_prev.py --repo OWNER/NAME --workflow proof-plan.yml --out DIR [--head HEAD]

Not only the last commit: the nearest ancestor that has evidence (a run can be missing, expired or failed). The artifact is `proof-evidence`
(evidence.json). Prints the commit found, or nothing; exit 0 either way: no evidence is a normal state (the planner then says NO_EVIDENCE).
"""
import argparse
import json
import os
import subprocess
import sys


def pick(runs, is_ancestor):
    """The first run (newest first) whose head commit is an ancestor of the head. Pure."""
    for r in runs:
        if r.get("conclusion") in (None, "success") and is_ancestor(r["headSha"]):
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    ls = subprocess.run(["gh", "run", "list", "--repo", a.repo, "--workflow", a.workflow, "--branch", "main", "--status", "success",
                         "--limit", str(a.limit), "--json", "databaseId,headSha,conclusion"], capture_output=True, text=True)
    if ls.returncode:
        print("no run list (%s)" % ls.stderr.strip()[:200], file=sys.stderr)
        return 0
    runs = json.loads(ls.stdout or "[]")
    anc = lambda sha: subprocess.run(["git", "merge-base", "--is-ancestor", sha, a.head], capture_output=True).returncode == 0
    for _ in range(len(runs)):
        r = pick(runs, anc)
        if r is None:
            break
        os.makedirs(a.out, exist_ok=True)
        d = subprocess.run(["gh", "run", "download", str(r["databaseId"]), "--repo", a.repo, "-n", "proof-evidence", "-D", a.out],
                           capture_output=True, text=True)
        if d.returncode == 0 and os.path.exists(os.path.join(a.out, "evidence.json")):
            print(r["headSha"])
            return 0
        runs = [x for x in runs if x["databaseId"] != r["databaseId"]]  # no artifact (expired, or none): the next ancestor
    print("no evidence found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
