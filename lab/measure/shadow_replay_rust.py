"""The outcome of every Rust test at every commit of main, observed, twice.

    python3 lab/measure/shadow_replay_rust.py --repo . --work DIR --upstream DIR --toolchain 1.99.1 --rev R --out verdicts.json

As shadow_replay.py does for Go: for every commit of the cache the test binaries of rust/ are built (cargo test --no-run) with the upstream
checkouts at the commits the commit pins, and each is run twice; per test the two outcomes PASS, FAIL or SKIP (ignored). A test whose two runs
differ is nondeterministic (proptest draws cases). The key of a test is `<target>::<name>` as the libtest output names it, the same the
backtest's runner_key gives; a unit test of the library is under the target cic_primitive_forms.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402


def outcomes(exe, cwd, env):
    r = bd.run([exe], cwd=cwd, env=env)
    out = {}
    for m in re.finditer(r"^test (\S+) \.\.\. (ok|FAILED|ignored)", r.stdout, re.M):
        out[m.group(1)] = {"ok": "PASS", "FAILED": "FAIL", "ignored": "SKIP"}[m.group(2)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--work", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--toolchain", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="the last N commits only (a trial)")
    a = ap.parse_args()
    repo, work, up = os.path.abspath(a.repo), os.path.abspath(a.work), os.path.abspath(a.upstream)
    cache = json.load(open(os.path.join(work, "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]
    if a.limit:
        commits = commits[-a.limit:]
    res = json.load(open(a.out)) if os.path.exists(a.out) else {}
    wt = os.path.join(work, "replay-rust-wt")
    target = os.path.join(work, "replay-rust-target")
    try:
        for c in commits:
            if c in res:
                continue
            if not os.path.isdir(wt):
                bd.run(["git", "-C", repo, "worktree", "add", "--detach", wt, c], check=True)
            bd.run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", c], check=True)
            for name, pin in bd.pins(repo, c).items():
                u = os.path.join(up, name)
                if bd.run(["git", "-C", u, "checkout", "-q", "--detach", "--force", pin]).returncode:
                    bd.run(["git", "-C", u, "fetch", "-q", "origin"], check=True)
                    bd.run(["git", "-C", u, "checkout", "-q", "--detach", "--force", pin], check=True)
            env = dict(os.environ, CIC_FORMS_ROOT=wt, CIC_PRIMITIVES_ROOT=os.path.join(up, "cic-primitives"),
                       CIC_REGISTRY_ROOT=os.path.join(up, "cic-schema-registry"), CARGO_TARGET_DIR=target, CARGO_NET_OFFLINE="true",
                       CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0")
            entry = {"rust": {}}
            if os.path.isfile(os.path.join(wt, "rust", "Cargo.toml")):
                b = bd.run(["rustup", "run", a.toolchain, "cargo", "test", "--no-run", "--message-format=json"], cwd=os.path.join(wt, "rust"), env=env)
                exes = []
                for line in b.stdout.splitlines():
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
                        exes.append((m["target"]["name"].replace("-", "_"), m["executable"]))
                if b.returncode:
                    entry["build_error"] = b.stderr[-300:]
                else:
                    for tgt, exe in exes:
                        r1, r2 = outcomes(exe, os.path.join(wt, "rust"), env), outcomes(exe, os.path.join(wt, "rust"), env)
                        for t in sorted(set(r1) | set(r2)):
                            entry["rust"]["%s::%s" % (tgt, t)] = [r1.get(t, "MISSING"), r2.get(t, "MISSING")]
            res[c] = entry
            json.dump(res, open(a.out, "w"))
            n = entry["rust"]
            print("%s tests %d pass %d fail %d skip %d unstable %d %s" % (
                c[:7], len(n), sum(1 for v in n.values() if v == ["PASS", "PASS"]), sum(1 for v in n.values() if "FAIL" in v),
                sum(1 for v in n.values() if "SKIP" in v), sum(1 for v in n.values() if v[0] != v[1]), entry.get("build_error", "")[:80]),
                  file=sys.stderr, flush=True)
    finally:
        if os.path.isdir(wt):
            bd.run(["git", "-C", repo, "worktree", "remove", "--force", wt])


if __name__ == "__main__":
    main()
