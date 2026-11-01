"""The Go keys of the backtest, made again with another build of the typed extractor.

    python3 lab/measure/backtest_rekey.py --repo . --from DIR --to DIR2 --typeddeps BIN [--rev R] [-- EXTRACTOR_ARG ...]

DIR holds the cache.json and traces/ of backtest.py / backtest_data.py. DIR2 gets a cache.json in which the key of every Go test is
the Merkle key from BIN (run with the arguments after `--`), and everything else (Rust keys, environment, data digests) as it was; the
traces are linked, since the files a test reads are recorded on the parent and do not depend on the model of the code. The Rust keys
are kept because the change measured here (the const group rule) is a Go one. Then iota_exposure.py --work DIR2 gives the reusable runs
of the new keys by the same rule as before.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import backtest_data as bd  # noqa: E402
import merkle  # noqa: E402


def main():
    argv = sys.argv[1:]
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--typeddeps", required=True)
    ap.add_argument("--rev", default="origin/main")
    a = ap.parse_args(argv[: argv.index("--")] if "--" in argv else argv)
    repo, src, dst = os.path.abspath(a.repo), os.path.abspath(a.src), os.path.abspath(a.to)
    cache = json.load(open(os.path.join(src, "cache.json")))
    commits = [c for c in bd.git(repo, "rev-list", "--first-parent", "--reverse", a.rev).split() if c in cache]
    os.makedirs(dst, exist_ok=True)
    if not os.path.exists(os.path.join(dst, "traces")):
        os.symlink(os.path.join(src, "traces"), os.path.join(dst, "traces"))
    wt = os.path.join(dst, "rekey-wt")
    env = dict(os.environ, GOFLAGS="-mod=mod", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
    out = {}
    try:
        for c in commits:
            entry = json.loads(json.dumps(cache[c]))
            if not os.path.isdir(wt):
                bd.run(["git", "-C", repo, "worktree", "add", "--detach", wt, c], check=True)
            bd.run(["git", "-C", wt, "checkout", "-q", "--detach", "--force", c], check=True)
            if os.path.isfile(os.path.join(wt, "go", "go.mod")):
                r = bd.run([os.path.abspath(a.typeddeps), *extra, os.path.join(wt, "go")], env=env)
                entry["tests"] = {k: v for k, v in entry["tests"].items() if not k.startswith("go:")}
                if r.returncode:
                    entry["errors"]["go"] = r.stderr[-500:]
                else:
                    g = json.loads(r.stdout)
                    keys, _ = merkle.keys(g)
                    for n in g["nodes"]:
                        if n.get("entry"):
                            entry["tests"]["go:" + n["id"].rsplit("/", 1)[-1]] = keys[n["id"]]
            out[c] = entry
            print(c[:7], len(entry["tests"]), file=sys.stderr, flush=True)
    finally:
        if os.path.isdir(wt):
            bd.run(["git", "-C", repo, "worktree", "remove", "--force", wt])
    json.dump(out, open(os.path.join(dst, "cache.json"), "w"))


if __name__ == "__main__":
    main()
