"""From the planner's decisions to the commands that run only what has to run (REPORT ONLY in this step: nothing here is obeyed by a job).

    python3 select_tests.py check --plan plan.json --repo . --rust-toolchain T     prints what would be skipped and checks the commands against the real test lists
    python3 select_tests.py run go|rust --plan plan.json --repo . [--rust-toolchain T] [--observed-out F]   runs the tests of p1-proof's job: the p1 command, minus the
        skips if authorized, and keeps what each test did (the planner does not run the tests again)

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
import time

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


def load_plan(path):
    """The plan, or None when there is none or it cannot be read: no plan means no skip, the p1 command."""
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def skips(plan, lang):
    """The tests to skip in this run: the REUSE_ELIGIBLE ones when the plan is authorized, else none."""
    try:
        return eligible(plan, lang) if plan and authorized(plan) else []
    except (KeyError, TypeError, AttributeError):
        return []


def run_go(plan, repo, observed_out=None):
    """The p1 command with `-json` (the same tests, the same flags), echoed as the text `go test` prints, and what each test did is kept
    for the planner: the job observes its own run, nothing runs twice for the planner."""
    names = skips(plan, "go")
    print("p1-proof go: %d test(s) skipped" % len(names), flush=True)
    cmd = go_command(names)
    cmd.insert(cmd.index("./..."), "-json")
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=os.path.join(repo, "go"), stdout=subprocess.PIPE, text=True)
    tests = {}
    for line in p.stdout:
        try:
            e = json.loads(line)
        except ValueError:
            sys.stdout.write(line)   # not an event (a build error, a panic): as it comes
            continue
        if e.get("Action") == "output":
            sys.stdout.write(e.get("Output", ""))
        t = e.get("Test")
        if t and "/" not in t and e.get("Action") in ("pass", "fail", "skip"):
            tests["go:" + t] = {"outcome": e["Action"].upper(), "elapsed": e.get("Elapsed", 0.0)}
    rc = p.wait()
    if observed_out:
        json.dump({"tests": tests, "skipped": ["go:" + n for n in names], "wall_s": round(time.time() - t0, 1), "exit": rc}, open(observed_out, "w"))
    return rc


def rust_executables(repo, tc):
    b = subprocess.run(["rustup", "run", tc, "cargo", "test", "--locked", "--no-run", "--message-format=json"], cwd=os.path.join(repo, "rust"),
                       capture_output=True, text=True)
    if b.returncode:
        sys.stderr.write(b.stderr)
        return None
    exes = []
    for line in b.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
            exes.append((m["target"]["name"].replace("-", "_"), m["executable"]))
    return exes


RESULT = re.compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)", re.M)


def run_rust(plan, repo, tc, observed_out=None):
    """Every test binary `cargo test` would run, run one after the other (a failing binary stops the rest, as in `cargo test`), with the skips of the
    plan; then the doctests through cargo. What each test did is kept for the planner. If the binaries cannot be made the command of p1 runs."""
    names = skips(plan, "rust")
    crate = os.path.join(repo, "rust")
    exes = rust_executables(repo, tc)
    if exes is None:
        print("p1-proof rust: the test binaries could not be listed, the command of p1", flush=True)
        return subprocess.run(["cargo", "+" + tc, "test", "--locked"], cwd=crate).returncode
    groups = rust_groups(names)
    print("p1-proof rust: %d test(s) skipped in %d target(s)" % (len(names), len(groups)), flush=True)
    env = dict(os.environ, CARGO_MANIFEST_DIR=crate)
    tests, worst, t0 = {}, 0, time.time()
    for target, exe in exes:
        extra = ["--exact"] + [x for n in sorted(groups[target]) for x in ("--skip", n)] if target in groups else []
        p = subprocess.run([exe] + extra, cwd=crate, env=env, capture_output=True, text=True)
        sys.stdout.write(p.stdout)
        sys.stderr.write(p.stderr)
        for m in RESULT.finditer(p.stdout):
            tests["rust:%s::%s" % (target, m.group(1))] = {"outcome": {"ok": "PASS", "FAILED": "FAIL", "ignored": "SKIP"}[m.group(2)]}
        if p.returncode:
            worst = p.returncode
            break
    # the doctests are not in the plan: they always run
    rc = worst or subprocess.run(["cargo", "+" + tc, "test", "--locked", "--doc"], cwd=crate).returncode
    if observed_out:
        json.dump({"tests": tests, "skipped": ["rust:" + n for n in names], "wall_s": round(time.time() - t0, 1), "exit": rc}, open(observed_out, "w"))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "run"])
    ap.add_argument("lang", nargs="?", choices=["go", "rust"])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--rust-toolchain")
    ap.add_argument("--observed-out", help="where `run` writes what each test did (for the planner's finish phase)")
    a = ap.parse_args()
    if a.cmd == "run":
        plan = load_plan(a.plan)
        return run_go(plan, a.repo, a.observed_out) if a.lang == "go" else run_rust(plan, a.repo, a.rust_toolchain, a.observed_out)
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
