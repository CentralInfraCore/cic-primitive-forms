"""How many tests would the history of main have reused? The Merkle keys of every test, commit by commit.

    python3 lab/measure/backtest.py --repo . --rev origin/main --limit N --work DIR --typeddeps BIN --scipdeps BIN \
        --rust-toolchain 1.99.1 --out backtest.json

For every first-parent commit of the revision (the oldest first) the Go module and the Rust crate of that commit are read with
the extractors of the lab (not of the commit), and every test entry (Go TestX/FuzzX, Rust #[test]) gets its Merkle key. A test
of a commit may reuse the result of the same test on the parent commit when all of these are equal on both:

* its Merkle key (the source of its whole closure);
* the environment key of its language: the lockfiles and manifests (go.mod, go.sum / Cargo.toml, Cargo.lock) and the toolchain
  versions the gate pins (RUST_TOOLCHAIN and go-version in the p1 workflow);
* the data key: every tracked file under go/, rust/, testdata/ and corpus/ that is not Go or Rust source. A test may read any of
  them; until data edges are modelled, a change of one of them reruns every test (fail closed).

A commit whose code the extractor cannot read (it does not type check, or an index fails) reruns everything and is listed.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=True).stdout


def tree_digest(repo, commit, keep):
    h = hashlib.sha256()
    for line in git(repo, "ls-tree", "-r", commit).splitlines():
        meta, path = line.split("\t", 1)
        if keep(path):
            h.update(("%s %s\n" % (path, meta.split()[2])).encode())
    return h.hexdigest()


def pins(repo, commit):
    try:
        wf = git(repo, "show", "%s:.github/workflows/p1.yml" % commit)
    except subprocess.CalledProcessError:
        return {}
    out = {}
    m = re.search(r'RUST_TOOLCHAIN:\s*"?([^"\s]+)', wf)
    out["rust"] = m.group(1) if m else "stable"
    m = re.search(r'go-version:\s*"?([^"\s]+)', wf)
    out["go"] = m.group(1) if m else ""
    return out


def is_data(path):
    if not path.startswith(("go/", "rust/", "testdata/", "corpus/")):
        return False
    return not path.endswith((".go", ".rs", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock"))


def env_keys(repo, commit):
    p = pins(repo, commit)
    go = tree_digest(repo, commit, lambda x: x in ("go/go.mod", "go/go.sum")) + p.get("go", "")
    rust = tree_digest(repo, commit, lambda x: x in ("rust/Cargo.toml", "rust/Cargo.lock")) + p.get("rust", "")
    return {"go": hashlib.sha256(go.encode()).hexdigest(), "rust": hashlib.sha256(rust.encode()).hexdigest()}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--work", required=True)
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--scipdeps", required=True)
    ap.add_argument("--rust-toolchain", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    commits = git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split()
    if a.limit:
        commits = commits[-a.limit:]
    wt = os.path.join(a.work, "wt")
    if not os.path.isdir(wt):
        run(["git", "-C", repo, "worktree", "add", "--detach", wt, commits[0]], check=True)
    cache_path = os.path.join(a.work, "cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    per_commit = []
    for c in commits:
        if c not in cache:
            run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", c], check=True)
            entry = {"subject": git(repo, "log", "-1", "--format=%s", c).strip()[:100], "errors": {}, "tests": {}}
            if os.path.isfile(os.path.join(wt, "go", "go.mod")):
                g = run([a.typeddeps, os.path.join(wt, "go")])
                if g.returncode:
                    entry["errors"]["go"] = g.stderr[-500:]
                else:
                    graph = json.loads(g.stdout)
                    keys, _ = merkle.keys(graph)
                    for n in graph["nodes"]:
                        if n.get("entry"):
                            entry["tests"]["go:" + n["id"].rsplit("/", 1)[-1]] = keys[n["id"]]
            if os.path.isfile(os.path.join(wt, "rust", "Cargo.toml")):
                scip = os.path.join(a.work, "index.scip")
                r = run(["rustup", "run", a.rust_toolchain, "rust-analyzer", "scip", ".", "--output", scip],
                        cwd=os.path.join(wt, "rust"))
                s = run([a.scipdeps, scip, os.path.join(wt, "rust")]) if r.returncode == 0 else r
                if s.returncode:
                    entry["errors"]["rust"] = s.stderr[-500:]
                else:
                    graph = json.loads(s.stdout)
                    keys, _ = merkle.keys(graph)
                    for n in graph["nodes"]:
                        if n.get("entry"):
                            entry["tests"]["rust:" + n["id"]] = keys[n["id"]]
            entry["env"] = env_keys(repo, c)
            entry["data"] = tree_digest(repo, c, is_data)
            cache[c] = entry
            json.dump(cache, open(cache_path, "w"))
            print("%s %-60s go/rust tests %d %s" % (c[:7], entry["subject"][:60], len(entry["tests"]),
                                                   ("errors " + ",".join(entry["errors"])) if entry["errors"] else ""),
                  file=sys.stderr, flush=True)
        per_commit.append((c, cache[c]))

    rows, totals = [], {"tests": 0, "reusable": 0, "new": 0, "code": 0, "data": 0, "env": 0, "unreadable": 0, "same_code": 0}
    for (pc, prev), (c, cur) in zip(per_commit, per_commit[1:]):
        row = {"commit": c[:12], "subject": cur["subject"], "tests": len(cur["tests"]), "reusable": 0, "new": 0, "code": 0,
               "data": 0, "env": 0, "unreadable": 0, "same_code": 0}
        unreadable = set(prev["errors"]) | set(cur["errors"])
        for t, k in cur["tests"].items():
            lang = t.split(":", 1)[0]
            if lang in unreadable:
                why = "unreadable"
            elif t not in prev["tests"]:
                why = "new"
            elif prev["env"][lang] != cur["env"][lang]:
                why = "env"
            elif prev["data"] != cur["data"]:
                why = "data"
            elif prev["tests"][t] != k:
                why = "code"
            else:
                why = "reusable"
            row[why] += 1
            if lang not in unreadable and t in prev["tests"] and prev["env"][lang] == cur["env"][lang] and prev["tests"][t] == k:
                row["same_code"] += 1  # reusable if the data the test reads were known (data edges)
        for k in totals:
            totals[k] += row[k]
        rows.append(row)
    share = round(totals["reusable"] / totals["tests"], 3) if totals["tests"] else None
    # what the data key costs: the tests whose closure and environment were the same, whatever the data did (an upper bound for
    # what modelling data edges could give)
    code_only = totals["same_code"]
    report = {"commits": len(per_commit), "pairs": len(rows), "totals": totals, "reusable_share": share,
              "reusable_share_if_data_were_modelled_upper_bound": round(code_only / totals["tests"], 3) if totals["tests"] else None,
              "commits_with_unreadable_code": [c[:12] for c, e in per_commit if e["errors"]],
              "rows": rows}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
