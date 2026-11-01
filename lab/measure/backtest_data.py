"""The backtest with data edges: a data change reruns only the tests that read the changed data.

    python3 lab/measure/backtest_data.py --repo . --work DIR --upstream DIR --rust-toolchain 1.99.1 --out report.json

It reads the cache of backtest.py (DIR/cache.json: per commit the Merkle key of every test, the environment keys and the data
digest) and, for every commit pair whose data digest differs, records on the PARENT which files each test reads (every test run
alone under strace, trace_data.py). The parent is the right place: a deterministic test whose closure is the same and whose read
files are the same on the child reads the same files there too, because what it opens is decided by its code and by what it
read before. A test that failed on the parent is not reused (its reads may have stopped early).

The upstream checkouts are moved to the commits the parent pins (corpus/pins.yaml), since the tests verify them.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import trace_data  # noqa: E402


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def git(repo, *args):
    return run(["git", "-C", repo] + list(args), check=True).stdout


def runner_key(key):
    """The backtest's test ids (from the graphs) as the runners name the tests."""
    lang, rest = key.split(":", 1)
    if lang == "go":
        return "go:" + rest.rsplit(".", 1)[-1]
    target = "cic_primitive_forms"
    if rest.startswith("tests/"):
        target, rest = rest[len("tests/"):].split("::", 1)
    rest = rest.replace("<macro>/", "")
    return "rust:%s::%s" % (target, rest[: -len("().")].replace("/", "::"))


def tree(repo, commit, memo={}):
    if commit not in memo:
        files = {}
        for line in git(repo, "ls-tree", "-r", commit).splitlines():
            meta, path = line.split("\t", 1)
            files[path] = meta.split()[2]
        memo[commit] = files
    return memo[commit]


def content(repo, commit, path, kind):
    if kind == "opaque":
        return object()  # unknown: never equal, so a test with such a read is never reused
    files = tree(repo, commit)
    if kind == "dir":
        prefix = path.rstrip("/") + "/"
        return sorted({p[len(prefix):].split("/", 1)[0] for p in files if p.startswith(prefix)})
    return files.get(path, "absent")


def pins(repo, commit):
    try:
        text = git(repo, "show", "%s:corpus/pins.yaml" % commit)
    except subprocess.CalledProcessError:
        return {}
    out = {}
    m = re.search(r"^    commit: (\S+)", text, re.M)
    if m:
        out["cic-primitives"] = m.group(1)
    m = re.search(r"^  registry-network-interface:.*?^    commit: (\S+)", text, re.M | re.S)
    if m:
        out["cic-schema-registry"] = m.group(1)
    return out


def trace_commit(repo, commit, work, upstream, toolchain):
    path = os.path.join(work, "traces", commit + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    wt = os.path.join(work, "trace-wt")
    if not os.path.isdir(wt):
        run(["git", "-C", repo, "worktree", "add", "--detach", wt, commit], check=True)
    run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", commit], check=True)
    for name, pin in pins(repo, commit).items():
        up = os.path.join(upstream, name)
        if run(["git", "-C", up, "checkout", "-q", "--detach", pin]).returncode:
            run(["git", "-C", up, "fetch", "-q", "origin"], check=True)
            run(["git", "-C", up, "checkout", "-q", "--detach", pin], check=True)
    env = dict(os.environ, CIC_FORMS_ROOT=wt, CIC_PRIMITIVES_ROOT=os.path.join(upstream, "cic-primitives"),
               CIC_REGISTRY_ROOT=os.path.join(upstream, "cic-schema-registry"), GOCOVERDIR=os.path.join(work, "tmp"),
               LLVM_PROFILE_FILE=os.path.join(work, "tmp", "%p.profraw"))
    os.makedirs(os.path.join(work, "tmp"), exist_ok=True)
    roots = {env["CIC_PRIMITIVES_ROOT"]: "corpus/pins.yaml", env["CIC_REGISTRY_ROOT"]: "corpus/pins.yaml"}
    jobs, result = [], {"tests": {}, "build_errors": {}}
    if os.path.isfile(os.path.join(wt, "go", "go.mod")):
        gobin = os.path.join(work, "trace-forms.test")
        b = run(["go", "test", "-c", "-o", gobin, "."], cwd=os.path.join(wt, "go"), env=env)
        if b.returncode:
            result["build_errors"]["go"] = b.stderr[-500:]
        else:
            for n in run([gobin, "-test.list", ".*"], cwd=os.path.join(wt, "go"), env=env).stdout.split():
                if n.startswith(("Test", "Fuzz")):
                    jobs.append(("go:" + n, [gobin, "-test.run", "^" + re.escape(n) + "$", "-test.count=1"], "go"))
    if os.path.isfile(os.path.join(wt, "rust", "Cargo.toml")):
        b = run(["cargo", "+" + toolchain, "test", "--no-run", "--message-format=json"], cwd=os.path.join(wt, "rust"),
                env=dict(env, CARGO_TARGET_DIR=os.path.join(work, "trace-target")))
        exes = []
        for line in b.stdout.splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
                exes.append((m["target"]["name"], m["executable"]))
        if b.returncode:
            result["build_errors"]["rust"] = b.stderr[-500:]
        for target, exe in exes:
            listing = run([exe, "--list", "--format", "terse"], cwd=os.path.join(wt, "rust"), env=env).stdout
            for t in (l[: -len(": test")] for l in listing.splitlines() if l.endswith(": test")):
                jobs.append(("rust:%s::%s" % (target, t), [exe, "--exact", t, "--test-threads=1", "-q"], "rust"))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count()) as ex:
        def one(j):
            key, argv, sub = j
            rc, _, deps, _ = trace_data.traced(argv, os.path.join(wt, sub), wt, roots, env)
            return key, {"rc": rc, "reads": deps}
        result["tests"] = dict(ex.map(one, jobs))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(result, open(path, "w"))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--work", required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--rust-toolchain", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    cache = json.load(open(os.path.join(a.work, "cache.json")))
    commits = [c for c in git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]

    totals = {"tests": 0, "reusable_coarse": 0, "reusable": 0, "data_read_changed": 0, "failed_on_parent": 0, "not_traced": 0}
    rows = []
    for p, c in zip(commits, commits[1:]):
        prev, cur = cache[p], cache[c]
        data_changed = prev["data"] != cur["data"]
        trace = None
        if data_changed and cur["tests"]:
            trace = trace_commit(repo, p, a.work, a.upstream, a.rust_toolchain)
            print("%s traced: %d tests %s" % (p[:7], len(trace["tests"]), trace["build_errors"] or ""), file=sys.stderr, flush=True)
        row = {"commit": c[:12], "subject": cur["subject"], "tests": len(cur["tests"]), "reusable_coarse": 0, "reusable": 0,
               "data_read_changed": 0, "failed_on_parent": 0, "not_traced": 0}
        unreadable = set(prev["errors"]) | set(cur["errors"])
        for t, k in cur["tests"].items():
            lang = t.split(":", 1)[0]
            same_code = (lang not in unreadable and t in prev["tests"] and prev["tests"][t] == k
                         and prev["env"][lang] == cur["env"][lang])
            if not same_code:
                continue
            if not data_changed:
                row["reusable_coarse"] += 1
                row["reusable"] += 1
                continue
            rec = trace["tests"].get(runner_key(t))
            if rec is None:
                row["not_traced"] += 1
            elif rec["rc"] != 0:
                row["failed_on_parent"] += 1
            elif any(content(repo, p, path, kind) != content(repo, c, path, kind) for path, kind in rec["reads"].items()):
                row["data_read_changed"] += 1
            else:
                row["reusable"] += 1
        for key in totals:
            totals[key] += row[key]
        rows.append(row)
    report = {"pairs": len(rows), "totals": totals,
              "reusable_share_coarse": round(totals["reusable_coarse"] / totals["tests"], 3),
              "reusable_share_with_data_edges": round(totals["reusable"] / totals["tests"], 3), "rows": rows}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
