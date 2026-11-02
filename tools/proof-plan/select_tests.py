"""From the planner's decisions to the commands that run only what has to run (REPORT ONLY in this step: nothing here is obeyed by a job).

    python3 select_tests.py --plan plan.json --repo . --rust-toolchain T      prints what would be skipped and checks the commands against the real test lists

The form is "everything except the tests whose earlier result may stand": Go `-skip '^(A|B)$'`, Rust `--exact --skip name` per test target. A test the
plan does not know, a target it does not list and the doctests keep running, so a gap in the plan can only run more, never less.
Only REUSE_ELIGIBLE tests are skipped, and only when the plan is authorized (plan.authority: evidence AND planner from main); otherwise
the skip set is empty and the report says what it would have been.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan as pl  # noqa: E402

GO_FLAGS = ["-race", "-count=1", "-rapid.checks=1000"]
LIB = "cic_primitive_forms"


def eligible(plan, lang):
    return sorted(d["key"][len(lang) + 1:] for d in plan["decisions"] if d["status"] == "REUSE_ELIGIBLE" and d["key"].startswith(lang + ":"))


def authorized(plan):
    return pl.authority(plan.get("previous_source") or {"trust": "none"}, plan.get("tooling_trust"))[0]


def go_skip_regex(names):
    return "^(" + "|".join(re.escape(n) for n in names) + ")$" if names else None


def go_command(names):
    cmd = ["go", "test"] + GO_FLAGS
    if names:
        cmd += ["-skip", go_skip_regex(names)]
    return cmd + ["./..."]


def rust_groups(names):
    """{target: [test names]} from "<target>::<test path>"."""
    g = {}
    for n in names:
        target, rest = n.split("::", 1)
        g.setdefault(target, []).append(rest)
    return g


def rust_target_args(target):
    return ["--lib"] if target == LIB else ["--test", target]


def rust_commands(names):
    """One `cargo test` per target that has something to skip."""
    return [["cargo", "test", "--locked"] + rust_target_args(t) + ["--", "--exact"] + [x for n in sorted(ns) for x in ("--skip", n)]
            for t, ns in sorted(rust_groups(names).items())]


def go_listed(root):
    out = subprocess.run(["go", "test", "-list", ".*", "./..."], cwd=os.path.join(root, "go"), capture_output=True, text=True, check=True).stdout
    return sorted({l for l in out.splitlines() if l.startswith(("Test", "Fuzz"))})


def check_go(root, names):
    """Python and Go regexps agree on a pattern of escaped identifiers: the pattern must match exactly `names` among the tests that exist."""
    listed = go_listed(root)
    rx = re.compile(go_skip_regex(names)) if names else None
    hit = sorted(t for t in listed if rx and rx.match(t))
    return hit == sorted(names), "go: %d tests exist, the skip pattern matches %d of the %d to skip" % (len(listed), len(hit), len(names))


def check_rust(root, tc, names):
    """For each target: the real listing with the skips has exactly (all - skipped) tests."""
    ok, lines = True, []
    for t, ns in sorted(rust_groups(names).items()):
        base = ["rustup", "run", tc, "cargo", "test", "--locked"] + rust_target_args(t) + ["--", "--list", "--format", "terse"]
        count = lambda extra: len([l for l in subprocess.run(base + extra, cwd=os.path.join(root, "rust"), capture_output=True, text=True).stdout.splitlines()  # noqa: E731
                                   if l.endswith(": test")])
        total = count([])
        left = count(["--exact"] + [x for n in ns for x in ("--skip", n)])
        good = left == total - len(ns)
        ok &= good
        lines.append("rust %s: %d tests, %d to skip, %d left in the real listing%s" % (t, total, len(ns), left, "" if good else "  <-- MISMATCH"))
    return ok, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--rust-toolchain", required=True)
    a = ap.parse_args()
    plan = json.load(open(a.plan))
    auth = authorized(plan)
    go, rs = eligible(plan, "go"), eligible(plan, "rust")
    print("### Selection (report only: every test still runs)")
    print("Skipping would be %s. Tests whose earlier result could stand: Go %d, Rust %d." % ("authorized" if auth else "NOT authorized (the skip set is empty)", len(go), len(rs)))
    ok_go, msg = check_go(a.repo, go)
    ok_rs, rlines = check_rust(a.repo, a.rust_toolchain, rs)
    bad = [l for l in rlines if "MISMATCH" in l]
    skipped = sum(len(v) for v in rust_groups(rs).values())
    print("- " + msg)
    print("- rust: %d targets with something to skip, %d tests to skip, %d target(s) where the real listing disagrees" % (len(rust_groups(rs)), skipped, len(bad)))
    for line in bad:
        print("  - " + line)
    print("- commands, if skipping were authorized: `%s`; %d cargo test invocation(s) with --skip, one per target, the other targets and the doctests unchanged" % (
        " ".join(go_command(go)[:5]) + " -skip ...", len(rust_commands(rs))))
    print("Selection check: %s" % ("consistent" if ok_go and ok_rs else "MISMATCH"))
    return 0 if ok_go and ok_rs else 1


if __name__ == "__main__":
    sys.exit(main())
