#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the edit differential (P3b step E4: the six guarantees of ADR 0012, Rust against Go).

    gen_edit.py OUT_JSONL [--count N] [--seed S]

Deterministic: the same seed gives the same file. Each line is one JSON object
`{"id": n, "yaml": text, "path": p, "back_path": p-after-the-edit|null, "value": typed, "orig": typed|null, "orig_canonical": bool, "other": {"path": p, "value": typed}|null}`.
The documents are drawn over the tree `doc` of corpus/edit (name, count, enabled, ip.gateway, ip.prefix, routes[{to}].metric, tags): styles
(plain, single and double quoted, comments, non-ASCII), line ends (LF, CRLF, lone CR), a BOM, no final newline, block and multi-line and flow
scalars, an expanded wrapper, an empty value, a list of records. The path and the new value are drawn too, also the wrong ones (a composite, an
unknown field, a string for an integer, null, a value with a control character, a string that a plain scalar cannot carry).
`orig` is the value the scalar holds now and `orig_canonical` says whether its token is the spelling an edit writes (so that "edit and back" has
to restore the bytes). `other` is a second position that is independent of `path` (a different top-level scalar).
The Rust and Go dumps (`parity_edit_dump`) answer every input and `compare_edit.py` compares them and checks the guarantees on its own.
"""
import argparse
import json
import random

EXPANDED = "  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n"
WORDS = ["alpha", "beta", "gamma", "s7", "x", "host-1", "a.b", "w3", "tűrő", "árvíz"]
TRICKY = ["it's", 'say "hi" \\', "a: b", "a #b", " lead", "trail ", "true", "12", "null", "~", "-dash", "?q", "", "é", "tab\there", "a\nb", "a\x07b", "yes", "1.5", "a:", "[x", "{y", "a'b", 'a"b']


def typed(v):
    if v is None:
        return {"null": True}
    if isinstance(v, dict):
        return {"map": True}                      # a collection is no scalar
    if isinstance(v, list):
        return {"seq": True}
    if isinstance(v, bool):
        return {"boolean": v}
    if isinstance(v, int):
        return {"integer": v}
    return {"string": v}


def plain_ok(s):
    """The plain-safe rule as the generator knows it (only for `orig_canonical`: whether the token of a document it wrote is the canonical one)."""
    return bool(s) and s == s.strip(" ") and s[0] not in "-?:,[]{}#&*!|>'\"%@`" and ": " not in s and " #" not in s and not s.endswith(":") and s not in ("true", "false", "null", "~") and not s.lstrip("+-").isdigit()


def scalar(r, kind):
    """(token text, value, canonical?) of one scalar of the given kind written in a drawn style."""
    if kind == "string":
        v = r.choice(WORDS)
        style = r.choice(["plain", "plain", "plain", "single", "double", "comment"])
        if style == "single":
            return "'" + v.replace("'", "''") + "'", v, True
        if style == "double":
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"', v, True
        tok = v + ("   # a comment" if style == "comment" else "")
        return tok, v, True
    if kind == "integer":
        n = r.choice([0, 3, 7, 24, -5, 1000, 65535])
        style = r.choice(["dec", "dec", "plus"])
        if style == "plus" and n >= 0:
            return "+" + str(n), n, False
        return str(n), n, True
    b = r.choice([True, False])
    style = r.choice(["low", "low", "cap"])
    return (("True" if b else "False") if style == "cap" else ("true" if b else "false")), b, style == "low"


def build(r):
    """One document: its lines, then the positions it holds: {path: (value, canonical)}."""
    fields = {}
    lines = []
    if r.random() < 0.15:
        lines.append("# a leading comment")
    for name, kind in (("name", "string"), ("count", "integer"), ("enabled", "boolean")):
        if r.random() < 0.85:
            tok, v, canon = scalar(r, kind)
            shape = r.random()
            if shape < 0.08:                       # an expanded wrapper that holds only the value
                lines += [name + ":", "  value: " + tok.split("   #")[0]] + EXPANDED.rstrip("\n").split("\n")
                fields["$." + name] = (v, canon and "'" not in tok and '"' not in tok)
            elif shape < 0.14 and kind == "string":    # a block scalar, a multi-line scalar, an empty value
                lines.append(name + ": " + r.choice(["|", ">"]))
                lines.append("  " + v)
            elif shape < 0.18 and kind == "string":
                lines += [name + ": " + v, "  " + v + "2"]
            elif shape < 0.21 and kind == "string":
                lines.append(name + ":")
            else:
                lines.append(name + ": " + tok)
                fields["$." + name] = (v, canon)
    if r.random() < 0.6:
        g, gv, gc = scalar(r, "string")
        p, pv, pc = scalar(r, "integer")
        if r.random() < 0.12:
            lines.append("ip: {gateway: %s, prefix: %s}" % (gv, pv))
            fields["$.ip.gateway"] = (gv, False)                     # a position in a flow collection: EDIT_FLOW_CONTEXT
            fields["$.ip.prefix"] = (pv, False)
        else:
            lines += ["ip:", "  gateway: " + g, "  prefix: " + p]
            fields["$.ip.gateway"] = (gv, gc)
            fields["$.ip.prefix"] = (pv, pc)
    if r.random() < 0.5:
        lines.append("routes:")
        for to in r.sample(["a", "b", "c"], r.randint(1, 3)):
            m, mv, mc = scalar(r, "integer")
            lines += ["  - to: " + to, "    metric: " + m]
            fields['$.routes[{to="%s"}].metric' % to] = (mv, mc)
            fields['$.routes[{to="%s"}].to' % to] = (to, True)
    if r.random() < 0.15:
        lines += ["tags:", "  - t1", "  - t2"]
    if r.random() < 0.05:
        lines.append(r.choice(["name: dup", "bogus: 1", "count: three"]))      # a document that does not parse or validate
    return lines, fields


def render(r, lines):
    eol = r.choice(["\n", "\n", "\n", "\r\n", "\r"])
    text = eol.join(lines) + (eol if r.random() < 0.85 else "")
    if r.random() < 0.1:
        text = chr(0xFEFF) + text
    return text


def draw_value(r, path, current):
    kind = ("integer" if path.endswith(("count", "prefix", "metric")) else "boolean" if path.endswith("enabled") else "string")
    roll = r.random()
    if roll < 0.12:
        return r.choice([None, 5, True, "three", {"x": 1}])
    if roll < 0.35 and kind == "string":
        return r.choice(TRICKY)
    if roll < 0.45 and current is not None:
        return current                                # the value it already holds: a no-op
    if kind == "integer":
        return r.choice([0, 1, -1, 42, 65536, -9223372036854775808, 9223372036854775807])
    if kind == "boolean":
        return r.choice([True, False])
    return r.choice(WORDS + ["new value", "n-1", "x y"])


QUOTE_VALUES = ['say "hi"', "back\\slash", 'both \\" mixed', "it's", "''", '\\"', '"', "'", "\\", "a'b\\c\"d", "plain", "with space"]


def quoted_case(r, i):
    """A quoted scalar and a value that needs escaping: the generic draw reaches this too rarely for the escapes to be tested."""
    q = r.choice(["'", '"'])
    word = r.choice(WORDS)
    text = render(r, ["name: %s%s%s" % (q, word, q), "count: 3"])
    value = r.choice(QUOTE_VALUES)
    return {"id": i, "yaml": text, "path": "$.name", "back_path": "$.name", "value": typed(value), "orig": typed(word), "orig_canonical": True, "other": None}


def case(r, i):
    if r.random() < 0.15:
        return quoted_case(r, i)
    lines, fields = build(r)
    text = render(r, lines)
    paths = sorted(fields) + ["$.ip", "$.tags", "$.zzz", "$.name", '$.routes[{to="q"}].metric']
    path = r.choice(paths)
    cur = fields.get(path)
    value = draw_value(r, path, cur[0] if cur else None)
    if path.endswith("].to") and r.random() < 0.7:
        value = r.choice(["a", "b", "c", "d"])             # often the key of another element: two elements would share one identity (EDIT_INVALID_RESULT)
    others = [p for p in sorted(fields) if p != path and not p.startswith("$.routes") and not path.startswith("$.routes")]
    other = None
    if others and r.random() < 0.5:
        op = r.choice(others)
        other = {"path": op, "value": typed(draw_value(r, op, fields[op][0]))}
    back_path = path
    if path.endswith("].to") and cur:        # the key of a list element: its address changes with it (ADR 0012, guarantee 6)
        simple = isinstance(value, str) and value != "" and all(c.isalnum() or c in "_.-" for c in value)
        back_path = path.replace('{to="%s"}' % cur[0], '{to="%s"}' % value) if simple else None     # (a key that needs the escapes of ADR 0006 is not edited back)
    return {"id": i, "yaml": text, "path": path, "back_path": back_path, "value": typed(value),
            "orig": typed(cur[0]) if cur else None, "orig_canonical": bool(cur and cur[1]), "other": other}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20261023)
    a = ap.parse_args()
    r = random.Random(a.seed)
    with open(a.out, "w", encoding="utf-8") as f:
        for i in range(a.count):
            f.write(json.dumps(case(r, i), sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
