"""Find the evidence an earlier run left: the newest successful run of the proof-plan workflow on main whose commit is an ancestor of HEAD.

    python3 tools/proof-plan/find_prev.py --repo OWNER/NAME --workflow proof-plan.yml --out DIR [--head HEAD] [--branch main] [--branch PRBRANCH] [--sha SHA]

Not only the last commit: the nearest ancestor that has evidence (a run can be missing, expired or failed). Main's evidence comes first (`prefer_main`):
the newest evidence of a branch would be a pull request's own earlier run, which can authorize nothing. The artifact is `proof-evidence`
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


def trust_of(run):
    """The trust level of the evidence a run left. It comes from what GitHub says about the run (the branch it ran on and the event), never from
    the content of the artifact, which is input: "main" only for a push to main (a process that only merged code can start), "branch" for the rest."""
    return "main" if (run.get("headBranch") == "main" and run.get("event") == "push") else "branch"


def prefer_main(runs):
    """Main's evidence first (newest first), then the others (newest first). What may authorize skipping is evidence of a push to main; the newest
    evidence of any branch is, from its second push on, the pull request's own earlier run, which authorizes nothing. A pull request is then set
    against main's evidence at every push, and its own earlier run is the fallback only when main has none. Pure."""
    newest_first = sorted(runs, key=lambda r: r.get("createdAt", ""), reverse=True)
    return sorted(newest_first, key=lambda r: trust_of(r) != "main")   # a stable sort: the order by time stays inside each group


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
    ap.add_argument("--sha", help="only the run of exactly this commit (the evidence at the commit an earlier result was obtained on)")
    a = ap.parse_args()
    runs = []
    for branch in a.branch or ["main"]:
        ls = subprocess.run(["gh", "run", "list", "--repo", a.repo, "--workflow", a.workflow, "--branch", branch, "--status", "success",
                             "--limit", str(a.limit), "--json", "databaseId,headSha,conclusion,createdAt,headBranch,event"], capture_output=True, text=True)
        if ls.returncode:
            print("no run list for %s (%s)" % (branch, ls.stderr.strip()[:200]), file=sys.stderr)
            continue
        runs += json.loads(ls.stdout or "[]")
    runs = prefer_main(runs)
    if a.sha:
        runs = [r for r in runs if r["headSha"] == a.sha]
    anc = lambda sha: subprocess.run(["git", "merge-base", "--is-ancestor", sha, a.head], capture_output=True).returncode == 0
    for _ in range(len(runs)):
        r = pick(runs, anc)
        if r is None:
            break
        os.makedirs(a.out, exist_ok=True)
        d = subprocess.run(["gh", "run", "download", str(r["databaseId"]), "--repo", a.repo, "-n", "proof-evidence", "-D", a.out],
                           capture_output=True, text=True)
        if d.returncode == 0 and locate(a.out):
            json.dump({"run_id": r["databaseId"], "head_sha": r["headSha"], "branch": r.get("headBranch"), "event": r.get("event"),
                       "trust": trust_of(r)}, open(os.path.join(a.out, "source.json"), "w"))
            print(r["headSha"])
            return 0
        runs = [x for x in runs if x["databaseId"] != r["databaseId"]]  # no artifact (expired, or none): the next ancestor
    print("no evidence found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
