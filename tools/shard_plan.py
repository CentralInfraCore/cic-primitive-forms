#!/usr/bin/env python3
"""Splits the Rust mutants between the shards of the sharded quality job by their measured cost.

`cargo mutants --shard k/N` deals the mutants out round robin, so a handful of mutants that run into the test timeout (each
costs the whole timeout, ~2 minutes) can land on one shard and make it the longest job of the workflow. This tool does the
dealing from a table of measured costs instead: the longest first onto the shard with the least load (LPT), deterministic, the
same answer in every shard job.

    shard_plan.py costs  --outcomes F... --out tools/mutation-costs.json
    shard_plan.py select --list rust-listed.json --costs tools/mutation-costs.json --shards N --shard K
                         --regex-out F --names-out F
    shard_plan.py verify --names F --selected cargo-mutants-list.json

It decides only who runs what. It never touches the timeout, the threshold, the tests or the mutants: the gate
(tools/quality_gate.py) still requires every listed mutant in exactly one shard's report, whichever way the shards were dealt,
and `verify` makes a shard stop, before it runs, if the regex it was given selects other mutants than the plan says. A stale or
missing cost makes the balance worse, never the measurement different.
"""
import argparse
import json
import re
import sys

SCHEMA = "mutation-costs/1"
NAME = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): (?P<what>.*)$")
# Characters that mean something in the regex syntax of cargo-mutants (Rust regex); everything else is literal.
META = re.compile(r"([\\.+*?()|\[\]{}^$#&~-])")


class PlanError(Exception):
    pass


def parse_name(name):
    m = NAME.match(name)
    if not m:
        raise PlanError("not a mutant name: %r" % (name,))
    return m.group("file"), int(m.group("line")), int(m.group("col")), m.group("what")


def keys(names):
    """name -> key without line and column, which move when code above changes: `file: what#k`, k counting the mutants of
    the same text in the order of their position. Independent of the order the names come in."""
    parsed = sorted((parse_name(n), n) for n in set(names))
    seen, out = {}, {}
    for (f, _, _, what), n in parsed:
        base = "%s: %s" % (f, what)
        seen[base] = seen.get(base, 0) + 1
        out[n] = "%s#%d" % (base, seen[base])
    return out


def costs_from_outcomes(docs):
    """Seconds per mutant from cargo-mutants `outcomes.json` documents (all the shards of one run)."""
    by_name = {}
    for doc in docs:
        for o in doc.get("outcomes", []):
            sc = o.get("scenario")
            if not isinstance(sc, dict) or "Mutant" not in sc:
                continue
            name = sc["Mutant"]["name"]
            by_name[name] = sum(float(p.get("duration", 0)) for p in o.get("phase_results", []))
    if not by_name:
        raise PlanError("no mutant in the outcomes")
    k = keys(by_name)
    return {k[n]: round(s, 1) for n, s in sorted(by_name.items(), key=lambda kv: k[kv[0]])}


def default_cost(table):
    return sum(table.values()) / len(table) if table else 10.0


def deal(names, table, shards):
    """name -> shard, and the predicted load of each shard in seconds. Longest first onto the least loaded shard; ties by the
    name and the lowest shard number, so that every job gets the same answer."""
    if shards < 1:
        raise PlanError("shards must be at least 1")
    if not names:
        raise PlanError("no mutants")
    if len(set(names)) != len(names):
        raise PlanError("a mutant is listed twice")
    k = keys(names)
    fallback = default_cost(table)
    cost = {n: float(table.get(k[n], fallback)) for n in names}
    loads = [0.0] * shards
    owner = {}
    for n in sorted(names, key=lambda n: (-cost[n], n)):
        j = min(range(shards), key=lambda i: (loads[i], i))
        owner[n] = j
        loads[j] += cost[n]
    return owner, loads, sum(1 for n in names if k[n] not in table)


def regex_of(names):
    """A regex that matches exactly these names (`cargo mutants --re` searches, so it is anchored)."""
    if not names:
        raise PlanError("a shard with no mutants")
    return "^(?:%s)$" % "|".join(META.sub(r"\\\1", n) for n in sorted(names))


def load_listed(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, list) or not doc or not all(isinstance(m, dict) and isinstance(m.get("name"), str) for m in doc):
        raise PlanError("%s is not `cargo mutants --list --json`" % path)
    return [m["name"] for m in doc]


def load_costs(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    t = doc.get("costs") if isinstance(doc, dict) else None
    if doc.get("schema") != SCHEMA or not isinstance(t, dict) or not all(isinstance(v, (int, float)) and v >= 0 for v in t.values()):
        raise PlanError("%s is not %s" % (path, SCHEMA))
    return t


def cmd_costs(a):
    docs = []
    for p in a.outcomes:
        with open(p, encoding="utf-8") as fh:
            docs.append(json.load(fh))
    table = costs_from_outcomes(docs)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"schema": SCHEMA, "unit": "seconds", "costs": table}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("%d mutants, %.1f minutes" % (len(table), sum(table.values()) / 60))


def cmd_select(a):
    if not 0 <= a.shard < a.shards:
        raise PlanError("shard %d of %d" % (a.shard, a.shards))
    names = load_listed(a.list)
    owner, loads, unknown = deal(names, load_costs(a.costs), a.shards)
    mine = sorted(n for n, j in owner.items() if j == a.shard)
    with open(a.regex_out, "w", encoding="utf-8") as fh:
        fh.write(regex_of(mine))
    with open(a.names_out, "w", encoding="utf-8") as fh:
        json.dump(mine, fh)
    print("shard %d/%d: %d of %d mutants, predicted %.1f min (longest shard %.1f min, shortest %.1f min); %d without a measured cost"
          % (a.shard, a.shards, len(mine), len(names), loads[a.shard] / 60, max(loads) / 60, min(loads) / 60, unknown))


def cmd_verify(a):
    with open(a.names, encoding="utf-8") as fh:
        planned = json.load(fh)
    chosen = load_listed(a.selected)
    if sorted(planned) != sorted(chosen):
        missing, extra = set(planned) - set(chosen), set(chosen) - set(planned)
        raise PlanError("the regex selects other mutants than planned: %d missing (%s), %d extra (%s)"
                        % (len(missing), sorted(missing)[:2], len(extra), sorted(extra)[:2]))
    print("the regex selects exactly the %d planned mutants" % len(planned))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("costs")
    c.add_argument("--outcomes", nargs="+", required=True)
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_costs)
    s = sub.add_parser("select")
    s.add_argument("--list", required=True)
    s.add_argument("--costs", required=True)
    s.add_argument("--shards", type=int, required=True)
    s.add_argument("--shard", type=int, required=True)
    s.add_argument("--regex-out", required=True)
    s.add_argument("--names-out", required=True)
    s.set_defaults(fn=cmd_select)
    v = sub.add_parser("verify")
    v.add_argument("--names", required=True)
    v.add_argument("--selected", required=True)
    v.set_defaults(fn=cmd_verify)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except (PlanError, OSError, ValueError) as e:
        print("shard_plan: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
