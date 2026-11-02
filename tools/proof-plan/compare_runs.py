"""Compare the p1-proof run of a commit with the p1 run of the same commit, job by job (the proof that p1-proof can replace p1).

    python3 compare_runs.py --repo OWNER/REPO --sha HEAD_SHA --event EVENT --own-run RUN_ID [--comparison comparison.json] [--timeout S]

The p1 run and the p1-proof run start together, so this waits (up to --timeout) until the p1 run is complete. Divergence is a job whose
conclusion differs (a job of p1-proof is `proof / <name>` for p1's `<name>`), a FALSE_REUSE in the planner's comparison, or a job that p1 has
and p1-proof lacks. Exit 1 on divergence. When the comparison cannot be made (no p1 run found, p1 not complete in time) the report says NOT COMPARED
and the exit is 0: an infrastructure fault is not a divergence, but it is also not a pass, and the report never says it is.
"""
import argparse
import json
import subprocess
import sys
import time

PREFIX = "proof / "
ONLY_HERE = {"proof-plan / plan", "proof-compare", "proof-plan"}   # jobs p1-proof has and p1 does not


def gh(path, jq):
    """One JSON object per line, as `gh api --paginate --jq` prints them."""
    r = subprocess.run(["gh", "api", "--paginate", path, "--jq", jq], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-300:])
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]


def jobs_of(repo, run_id):
    return gh("repos/%s/actions/runs/%s/jobs?per_page=100" % (repo, run_id), ".jobs[] | {name, conclusion}")


def normalize(name):
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def compare(p1_jobs, proof_jobs):
    """(rows, divergent): rows are (job, p1 conclusion, p1-proof conclusion, equal)."""
    a = {j["name"]: j.get("conclusion") for j in p1_jobs}
    b = {normalize(j["name"]): j.get("conclusion") for j in proof_jobs if normalize(j["name"]) not in ONLY_HERE}
    rows = []
    for name in sorted(set(a) | set(b)):
        x, y = a.get(name), b.get(name)
        rows.append((name, x, y, x is not None and x == y))
    return rows, [r for r in rows if not r[3]]


def find_p1_run(repo, sha, event):
    runs = gh("repos/%s/actions/workflows/p1.yml/runs?head_sha=%s&event=%s&per_page=20" % (repo, sha, event),
              ".workflow_runs[] | {id, status, conclusion, run_number}")
    return max(runs, key=lambda x: x["run_number"]) if runs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--event", required=True)
    ap.add_argument("--own-run", required=True)
    ap.add_argument("--comparison")
    ap.add_argument("--timeout", type=int, default=1500)
    a = ap.parse_args()
    print("### p1-proof against p1, commit `%s`" % a.sha[:7])
    deadline = time.time() + a.timeout
    run = None
    try:
        while True:
            run = find_p1_run(a.repo, a.sha, a.event)
            if run and run["status"] == "completed":
                break
            if time.time() > deadline:
                break
            time.sleep(20)
        if not run or run["status"] != "completed":
            print("**NOT COMPARED**: %s. This is not a pass." % ("no p1 run of this commit found" if not run else "the p1 run was not complete in %d s" % a.timeout))
            return 0
        rows, bad = compare(jobs_of(a.repo, run["id"]), jobs_of(a.repo, a.own_run))
    except (RuntimeError, ValueError, KeyError) as e:
        print("**NOT COMPARED**: %s. This is not a pass." % e)
        return 0
    false_reuse = 0
    if a.comparison:
        try:
            false_reuse = int(json.load(open(a.comparison)).get("false_reuse", 0) or 0)
        except (OSError, ValueError):
            print("(no planner comparison to read: FALSE_REUSE not checked)")
    print("p1 run %s: %s; jobs compared: %d; divergent: %d; FALSE_REUSE of the planner: %d" % (run["id"], run["conclusion"], len(rows), len(bad), false_reuse))
    for name, x, y, ok in rows:
        if not ok:
            print("- DIVERGENT `%s`: p1 %s, p1-proof %s" % (name, x, y))
    print("**%s**" % ("EQUAL: every job has the conclusion p1 has" if not bad and not false_reuse else "DIVERGENT"))
    return 1 if (bad or false_reuse) else 0


if __name__ == "__main__":
    sys.exit(main())
