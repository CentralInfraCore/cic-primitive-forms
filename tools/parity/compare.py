#!/usr/bin/env python3
"""Compare the Rust and Go parser outcomes for the same inputs (tools/parity/gen_inputs.py).

    compare.py INPUTS RUST_OUT GO_OUT [--show N] [--json FILE] [--allow FILE] [--emit-allow FILE]

--allow names a file of known open divergences, one per line: the hex-encoded input and the digest of the pair
of outcomes (`<hex> <sha256>`, `#` comments allowed). A divergence is allowed only when both the input and the
digest match: the same input diverging in another way (another outcome on either side) is CHANGED, an input
that is not in the file is NEW, and either makes the exit status 1. An allowed input that no longer diverges
is reported and is not an error (a fix is not a failure; drop the line). --emit-allow writes the divergent
inputs of this run in that format.

Classes of agreement:
  same        identical outcomes (accepted: the same tree with the same positions; contract error: the same
              violations; refused: the same code)
  refused     both refused the input but with different non-contract codes (YAML_SYNTAX against
              UNSUPPORTED_SYNTAX, for example): the same answer, a different reason
Everything else is a divergence and the exit status is 1: one accepts and the other refuses, the trees or the
positions differ, or one gives a contract error and the other does not.
"""
import argparse
import collections
import hashlib
import json
import sys

NONCONTRACT = {"YAML_SYNTAX", "UNSUPPORTED_SYNTAX", "LIMIT_EXCEEDED", "INVALID_UTF8"}


def digest(r, g):
    """The identity of a divergence: what each side answered, not only which input."""
    blob = json.dumps([r, g], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def klass(o):
    if "ok" in o:
        return "ok"
    if o["err"] == "CONTRACT":
        return "contract"
    return "refused:" + o["err"]


def first_diff(a, b, path="$"):
    if type(a) != type(b):
        return path, a, b
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return path + "." + k, a.get(k), b.get(k)
            d = first_diff(a[k], b[k], path + "." + k)
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return path + ".len", len(a), len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, "%s[%d]" % (path, i))
            if d:
                return d
        return None
    return None if a == b else (path, a, b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--json")
    ap.add_argument("--allow")
    ap.add_argument("--emit-allow")
    a = ap.parse_args()
    allowed = {}
    if a.allow:
        for l in open(a.allow):
            l = l.split("#")[0].split()
            if l:
                allowed[bytes.fromhex(l[0])] = l[1]
    ins = [bytes.fromhex(l.strip()) for l in open(a.inputs)]
    rs = [json.loads(l) for l in open(a.rust)]
    gs = [json.loads(l) for l in open(a.go)]
    if not (len(ins) == len(rs) == len(gs)):
        print("FAIL: %d inputs, %d Rust outcomes, %d Go outcomes" % (len(ins), len(rs), len(gs)))
        return 2
    counts = collections.Counter()
    div = collections.defaultdict(list)
    for i, (b, r, g) in enumerate(zip(ins, rs, gs)):
        if r == g:
            counts["same:" + klass(r).split(":")[0]] += 1
            continue
        kr, kg = klass(r), klass(g)
        if kr.startswith("refused") and kg.startswith("refused"):
            counts["refused (different reason)"] += 1
            continue
        key = "%s | %s" % (kr, kg)
        if kr == kg == "ok":
            key += "  first difference at " + str(first_diff(r, g)[0]).split("[")[0]
        elif kr == kg == "contract":
            key += "  violations differ"
        div[key].append(i)
    total_div = sum(len(v) for v in div.values())
    if a.emit_allow:
        with open(a.emit_allow, "w") as fh:
            fh.write("# Known open Rust/Go parser divergences on the fixed-seed run: input, digest of both outcomes\n")
            fh.write("# (tools/parity/compare.py --emit-allow). A changed outcome is not allowed.\n")
            for idx in div.values():
                for i in idx:
                    fh.write("%s %s\n" % (ins[i].hex(), digest(rs[i], gs[i])))
    new, changed, seen = [], [], set()
    for idx in div.values():
        for i in idx:
            seen.add(ins[i])
            if ins[i] not in allowed:
                new.append(i)
            elif allowed[ins[i]] != digest(rs[i], gs[i]):
                changed.append(i)
    gone = sum(1 for b in allowed if b not in seen)
    print("inputs: %d" % len(ins))
    for k, v in sorted(counts.items()):
        print("  %-34s %d" % (k, v))
    print("divergences: %d in %d groups" % (total_div, len(div)))
    report = {}
    for key, idx in sorted(div.items(), key=lambda kv: -len(kv[1])):
        print("\n[%d] Rust | Go = %s" % (len(idx), key))
        report[key] = []
        for i in idx[: a.show]:
            text = ins[i].decode("utf-8", "backslashreplace")
            short = text if len(text) <= 70 else text[:70] + "..."
            print("   %r" % short)
            print("      rust: %s" % json.dumps(rs[i])[:160])
            print("      go  : %s" % json.dumps(gs[i])[:160])
            report[key].append({"input_hex": ins[i].hex(), "rust": rs[i], "go": gs[i]})
    if a.json:
        json.dump(report, open(a.json, "w"), indent=1)
    if a.allow:
        for i in changed:
            print("CHANGED: %r is a known divergence but its outcomes differ from the allowed ones\n   rust: %s\n   go  : %s"
                  % (ins[i].decode("utf-8", "backslashreplace")[:70], json.dumps(rs[i])[:160], json.dumps(gs[i])[:160]))
        for i in new[:a.show]:
            print("NEW: %r" % ins[i].decode("utf-8", "backslashreplace")[:70])
        print("\nallowed (known, open): %d; changed: %d; new: %d; allowed but no longer divergent: %d"
              % (total_div - len(new) - len(changed), len(changed), len(new), gone))
        return 1 if new or changed else 0
    return 1 if total_div else 0


if __name__ == "__main__":
    sys.exit(main())
