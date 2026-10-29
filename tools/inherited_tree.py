#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Is this push to main the very tree that a pull request has already been checked on?

    inherited_tree.py --repo OWNER/REPO --sha SHA --workflows p1.yml,corpus.yml     prints inherited=true or inherited=false (for $GITHUB_OUTPUT)

A pull request is checked, and merged. If the commit that lands on main has exactly the tree of the head that was checked, the checks that a push to main would run
again are the same checks on the same bytes, and running them says nothing new. This decides that, and only that; it never decides that something may be skipped
that the pull request did not run.

It answers `true` only when ALL of this holds, and `false` on any doubt (an API error, a missing object, a rerun still running):
  * the commit is the merge commit of exactly one merged pull request into main;
  * the tree of the commit is the tree of the head of that pull request (`git diff` between them is empty);
  * for each workflow named, the newest run on that head for the event pull_request is completed and succeeded (a workflow run succeeds when every job
    succeeded or was skipped, so a pull request run that decided `diff` or `full` counts for the lighter run of a push).
What it does not cover, on purpose: the nightly FULL (it measures main with the classic tools and is a lane of its own), a push that is not the merge of a pull
request, and a run that asks for the FULL. The workflow that calls this keeps those out (see .github/workflows/inherit.yml).
"""
import argparse
import json
import subprocess
import sys


def decide(sha, pulls, tree_of, runs_of, workflows):
    """-> (inherited, reason). `pulls`: the pull requests the API lists for the commit; `tree_of(rev)`: the tree id or None; `runs_of(workflow, head)`: the runs."""
    merged = [p for p in pulls if p.get("merged_at") and p.get("merge_commit_sha") == sha and (p.get("base") or {}).get("ref") == "main"]
    if len(merged) != 1:
        return False, "the commit is the merge of %d pull requests into main, not of exactly one" % len(merged)
    head = merged[0]["head"]["sha"]
    t_head, t_sha = tree_of(head), tree_of(sha)
    if t_head is None or t_sha is None:
        return False, "a tree could not be read (%s: %s, %s: %s)" % (head[:7], t_head, sha[:7], t_sha)
    if t_head != t_sha:
        return False, "the tree of the commit (%s) is not the tree of the head that was checked, %s (%s)" % (t_sha[:10], head[:7], t_head[:10])
    for w in workflows:
        runs = [r for r in runs_of(w, head) if r.get("head_sha") == head and r.get("event") == "pull_request"]
        if not runs:
            return False, "%s has no pull request run on %s" % (w, head[:7])
        newest = max(runs, key=lambda r: (r["run_number"], r.get("run_attempt", 1)))
        if newest.get("conclusion") != "success":  # (a run that has not completed has no conclusion)
            return False, "the newest %s run on %s is %s/%s" % (w, head[:7], newest.get("status"), newest.get("conclusion"))
    return True, "the tree is the one of %s, checked by %s" % (head[:7], ", ".join(workflows))


def gh(path):
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout)


def tree_of(rev):
    p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", rev + "^{tree}"], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--workflows", required=True, help="comma separated workflow files whose pull request run must have succeeded")
    a = ap.parse_args()
    try:
        pulls = gh("repos/%s/commits/%s/pulls" % (a.repo, a.sha))
        merged = [p for p in pulls if p.get("merged_at") and p.get("merge_commit_sha") == a.sha]
        for p in merged[:1]:  # the head of the pull request is not on main: fetch it, so that its tree can be read
            subprocess.run(["git", "fetch", "--no-tags", "--quiet", "origin", "refs/pull/%d/head" % p["number"]], check=True)
        ok, why = decide(a.sha, pulls, tree_of,
                         lambda w, head: gh("repos/%s/actions/workflows/%s/runs?head_sha=%s&event=pull_request&per_page=30" % (a.repo, w, head))["workflow_runs"],
                         a.workflows.split(","))
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as e:
        ok, why = False, "could not decide (%s): the checks run" % e
    print(why, file=sys.stderr)
    print("inherited=%s" % ("true" if ok else "false"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
