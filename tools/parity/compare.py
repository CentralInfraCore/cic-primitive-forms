#!/usr/bin/env python3
"""Compare the Rust and Go parser outcomes for the same inputs (tools/parity/gen_inputs.py).

    compare.py INPUTS RUST_OUT GO_OUT [--show N] [--json FILE] [--allow FILE] [--emit-allow FILE]

--allow names a file of known open divergences, one per line: the hex-encoded input and the digest of the pair
of outcomes (`<hex> <sha256>`, `#` comments allowed). A divergence is allowed only when both the input and the
digest match: the same input diverging in another way (another outcome on either side) is CHANGED, an input
that is not in the file is NEW, and either makes the exit status 1. An allowed input that no longer diverges
is reported and is not an error (a fix is not a failure; drop the line). --emit-allow writes the divergent
inputs of this run in that format.

Before anything is compared, every outcome of both sides is checked against the dump format: the structure
(`ok` with a tree, or `err` with a known code and, for CONTRACT, its violations) and the error code. An
outcome that is not in the format, or whose code is not one the parsers can answer with (NOT_AN_ERROR_TYPE, which
is the Go dump's own signal that something other than a parser error came back, is one), is INVALID and the exit
status is 1, on either side, also when both sides give the same invalid outcome, also for an allowed input: it is a
fault of the harness or of a parser, not an answer to compare.

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
KNOWN_ERRORS = NONCONTRACT | {"CONTRACT"}
# The one violation parse can report (anchored at the second occurrence of a key), in the parse phase.
PARSE_VIOLATIONS = {("DUPLICATE_KEY", "parse")}
SCALARS = {"null", "bool", "int", "str", "seq", "map"}


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _pos(p):
    return p is None or (isinstance(p, list) and len(p) == 3 and all(_int(x) for x in p) and p[0] >= 1 and p[1] >= 1 and p[2] >= 0)


def _node(n):
    """A reason when `n` is not a tree node of the dump format, else None."""
    if not isinstance(n, dict) or set(n) != {"n", "p"}:
        return "a node is not {n, p}"
    if not _pos(n["p"]):
        return "a node position is not null or [line, column, offset]"
    v = n["n"]
    if not isinstance(v, dict) or v.get("t") not in SCALARS:
        return "a node has an unknown type"
    t = v["t"]
    if t == "null":
        return None if set(v) == {"t"} else "a null node has a value"
    if set(v) != {"t", "v"}:
        return "a %s node has no value or has more than one" % t
    x = v["v"]
    if t == "bool":
        return None if isinstance(x, bool) else "a bool node holds a non-bool"
    if t == "int":
        return None if _int(x) else "an int node holds a non-int"
    if t == "str":
        return None if isinstance(x, str) else "a str node holds a non-string"
    if not isinstance(x, list):
        return "a %s node holds a non-list" % t
    for item in x:
        if t == "seq":
            r = _node(item)
        elif not (isinstance(item, list) and len(item) == 3 and isinstance(item[0], str) and _pos(item[1])):
            r = "a map entry is not [key, key position, node]"
        else:
            r = _node(item[2])
        if r:
            return r
    return None


def invalid(o):
    """Why `o` is not an outcome of the dump format, or None."""
    if not isinstance(o, dict):
        return "not an object"
    if set(o) == {"ok"}:
        return _node(o["ok"])
    if "err" not in o or "ok" in o:
        return "neither exactly ok nor err"
    code = o["err"]
    if code not in KNOWN_ERRORS:
        return "unknown error code %r" % (code,)
    if code != "CONTRACT":
        return None if set(o) == {"err"} else "extra keys next to the error code"
    viol = o.get("viol")
    if set(o) != {"err", "viol"} or not isinstance(viol, list) or not viol:
        return "CONTRACT without violations"
    for v in viol:
        if not (isinstance(v, dict) and set(v) == {"code", "phase", "loc"} and (v["code"], v["phase"]) in PARSE_VIOLATIONS
                and _pos(v["loc"])):
            return "a violation is not a parse-phase one of the registry"
    return None


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
    bad = [(side, i, why) for side, outs in (("rust", rs), ("go", gs)) for i, o in enumerate(outs) if (why := invalid(o))]
    if bad:
        print("INVALID OUTCOMES: %d (the dump of a parser, or the harness, is faulty; nothing is compared)" % len(bad))
        for side, i, why in bad[: a.show or 5]:
            print("   %s: %r -> %s: %s" % (side, ins[i].decode("utf-8", "backslashreplace")[:60], json.dumps((rs if side == "rust" else gs)[i])[:100], why))
        return 1
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
