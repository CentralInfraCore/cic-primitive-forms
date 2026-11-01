"""Does what the kernel decided agree with what the tests did?

    python3 lab/measure/shadow_check.py --decisions decisions.txt --verdicts verdicts.json --repo . --work DIR --rev R --out report.json

decisions.txt: `kernel_backtest.py --verdicts ... --dump-decisions`. verdicts.json: `shadow_replay.py`. For every Go decision of
a commit pair, the observed outcome of the test on the parent and on the child:

  REUSE_ELIGIBLE  the kernel says the parent's result may stand. Checked against the child:
      CONSISTENT       the child passes, both runs        (the reuse would have been right)
      FALSE_REUSE      the child fails, both runs         (the reuse would have been WRONG: the result that matters)
      NONDETERMINISTIC the child's two runs differ        (the test draws its cases; not a decision error, counted apart)
      OTHER            skipped or missing on the child
  RERUN_REQUIRED / UNVERIFIABLE  the kernel makes no claim about the outcome. Counted to see what the rerun bought:
      SAME_OUTCOME     the child's outcome equals the parent's   (a rerun the outcome did not need)
      OUTCOME_CHANGED  it differs                                (a rerun that was needed)

Zero FALSE_REUSE over the history is the acceptance; it is evidence that the decisions were not wrong on these pairs, not a proof.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402


def stable(v):
    return v[0] if v and v[0] == v[1] else None


def classify(lines, verdicts, parent):
    """(tally by decision and kind, the false reuses, the nondeterministic test names) of decision lines `child test status reasons`."""
    tally = collections.defaultdict(collections.Counter)
    false_reuse, flaky = [], set()
    for line in lines:
        if not line.strip():
            continue
        c, t, status, reasons = (line.split(" ", 3) + [""])[:4]
        if not t.startswith("go:"):
            continue
        name = t.split(":", 1)[1].rsplit(".", 1)[-1]
        p = parent[c]
        vp, vc = verdicts[p]["go"].get(name), verdicts[c]["go"].get(name)
        sp, sc = stable(vp), stable(vc)
        if status == "REUSE_ELIGIBLE":
            if sc == "PASS" and sp == "PASS":
                kind = "CONSISTENT"
            elif sc == "FAIL" and sp == "PASS":
                kind = "FALSE_REUSE"
                false_reuse.append({"commit": c, "test": t, "parent": vp, "child": vc})
            elif vc and vc[0] != vc[1] or vp and vp[0] != vp[1]:
                kind = "NONDETERMINISTIC"
                flaky.add(name)
            else:
                kind = "OTHER"
        else:
            if sp and sc:
                kind = "SAME_OUTCOME" if sp == sc else "OUTCOME_CHANGED"
            else:
                kind = "NO_STABLE_OUTCOME"
        tally[status][kind] += 1
    return tally, false_reuse, flaky


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    cache = json.load(open(os.path.join(os.path.abspath(a.work), "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]
    parent = {commits[i + 1]: commits[i] for i in range(len(commits) - 1)}
    verdicts = json.load(open(a.verdicts))
    tally, false_reuse, flaky = classify(open(a.decisions), verdicts, parent)
    report = {"decisions": {k: dict(v) for k, v in tally.items()}, "false_reuse": false_reuse, "nondeterministic_tests": sorted(flaky)}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "false_reuse"}, indent=1))
    print("FALSE_REUSE:", len(false_reuse))
    return 1 if false_reuse else 0


if __name__ == "__main__":
    sys.exit(main())
