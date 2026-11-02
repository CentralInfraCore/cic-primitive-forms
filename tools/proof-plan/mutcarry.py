"""Mutation verdicts carried from an earlier FULL into the report of a shard (cargo-mutants, p1-proof's `quality-mutation-rust`).

    mutcarry.py plan    --prev-repo D --cur-repo D --scipdeps B --rust-toolchain T --outcomes DIR... --prev-env F --list all.json --out carried.json
    mutcarry.py exclude --carried carried.json --names shard-names.json --out exclude.txt     regex of the shard's carried mutants ("" if none)
    mutcarry.py rest    --carried carried.json --names shard-names.json --out rest.json     the shard's mutants that still have to run
    mutcarry.py merge   --carried carried.json --names shard-names.json --fresh outcomes.json --out merged.json [--source ID]

`plan` applies the rule of mutplan.py and keeps only mutants that the current listing has under the same name. `exclude` makes the `-E` regex that keeps
cargo-mutants from running them. `merge` puts their earlier outcomes (marked `carried_from`) into the shard's outcomes.json with the totals recounted,
so that the gate, which wants every listed mutant in exactly one shard's report and totals that agree with the outcomes, sees a complete report.

Nothing is carried unless `plan` says so, and `plan` carries nothing unless it is authorized (--authorized: the planner and the earlier run are main's).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import mutplan  # noqa: E402
import shard_plan  # noqa: E402

VERDICTS = {"CaughtMutant": "caught", "MissedMutant": "missed", "Timeout": "timeout", "Unviable": "unviable"}


def carried_set(rows, raw, listed):
    """{name: earlier outcome} for the mutants the rule carries that the current listing has, and whose earlier outcome is not in conflict."""
    out = {}
    for r in rows:
        name = r["mutant"]
        if r["carry"] and name in listed and name in raw and not raw[name][1] and raw[name][0]["summary"] == "CaughtMutant":
            out[name] = raw[name][0]
    return out


def exclude_regex(carried, shard_names):
    """The regex for `cargo mutants -E` that matches exactly the carried mutants of this shard, or "" if there are none."""
    mine = sorted(set(carried) & set(shard_names))
    return shard_plan.regex_of(mine) if mine else ""


def recount(outcomes):
    """The totals of a cargo-mutants outcomes.json from its outcomes (the gate recounts them and refuses a report that disagrees)."""
    t = dict.fromkeys(VERDICTS.values(), 0)
    muts = [o for o in outcomes if isinstance(o.get("scenario"), dict)]
    for o in muts:
        t[VERDICTS[o["summary"]]] += 1
    t["total_mutants"] = len(muts)
    return t


def merge(fresh, carried, shard_names, source):
    """The shard's report: the fresh outcomes of the mutants that ran, plus the carried ones of this shard. A carried mutant that also ran is a
    contradiction (the exclusion failed): error."""
    ran = {o["scenario"]["Mutant"]["name"] for o in fresh["outcomes"] if isinstance(o.get("scenario"), dict)}
    mine = {n: o for n, o in carried.items() if n in set(shard_names)}
    both = ran & set(mine)
    if both:
        raise ValueError("%d carried mutant(s) also ran: %s" % (len(both), sorted(both)[:2]))
    out = dict(fresh)
    out["outcomes"] = list(fresh["outcomes"]) + [dict(o, carried_from=source) for _, o in sorted(mine.items())]
    out.update(recount(out["outcomes"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--prev-repo", required=True)
    p.add_argument("--cur-repo", required=True)
    p.add_argument("--scipdeps", required=True)
    p.add_argument("--rust-toolchain", required=True)
    p.add_argument("--outcomes", action="append", required=True)
    p.add_argument("--prev-env", required=True)
    p.add_argument("--list", required=True)
    p.add_argument("--authorized", action="store_true")
    p.add_argument("--source", default="", help="the run the earlier outcomes come from (kept in carried.json, written on every carried outcome)")
    p.add_argument("--out", required=True)
    e = sub.add_parser("exclude")
    e.add_argument("--carried", required=True)
    e.add_argument("--names", required=True)
    e.add_argument("--out", required=True)
    r = sub.add_parser("rest")
    r.add_argument("--carried", required=True)
    r.add_argument("--names", required=True)
    r.add_argument("--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--carried", required=True)
    m.add_argument("--names", required=True)
    m.add_argument("--fresh", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--source")
    a = ap.parse_args()
    if a.cmd == "plan":
        import rust_graph as rg
        import subprocess

        def made(repo):
            crate = os.path.join(repo, "rust")
            subprocess.run(["rustup", "run", a.rust_toolchain, "cargo", "test", "--locked", "--no-run"], cwd=crate, check=True, capture_output=True)
            return rg.graph(crate, a.scipdeps, a.rust_toolchain)
        raw = mutplan.load_raw(a.outcomes)
        listed = {x["name"] for x in json.load(open(a.list))}
        rows = mutplan.predict(made(a.prev_repo), made(a.cur_repo), mutplan.load_outcomes(a.outcomes), json.load(open(a.prev_env)),
                               mutplan.env_of_tree(a.cur_repo, a.rust_toolchain))
        carried = carried_set(rows, raw, listed) if a.authorized else {}
        json.dump({"authorized": a.authorized, "source": a.source, "would_carry": sum(r["carry"] for r in rows), "carried": carried}, open(a.out, "w"))
        print(mutplan.report(rows))
        print("authorized: %s; carried into the reports: %d" % (a.authorized, len(carried)))
    elif a.cmd == "rest":
        carried = json.load(open(a.carried))["carried"]
        json.dump(sorted(set(json.load(open(a.names))) - set(carried)), open(a.out, "w"))
    elif a.cmd == "exclude":
        open(a.out, "w").write(exclude_regex(json.load(open(a.carried))["carried"], json.load(open(a.names))))
    else:
        c = json.load(open(a.carried))
        merged = merge(json.load(open(a.fresh)), c["carried"], json.load(open(a.names)), a.source or c.get("source") or "unknown")
        json.dump(merged, open(a.out, "w"))
        print("shard report: %d mutants, %d carried" % (merged["total_mutants"], sum(1 for o in merged["outcomes"] if "carried_from" in o)))


if __name__ == "__main__":
    main()
