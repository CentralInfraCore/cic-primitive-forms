#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the P3 differential (G3 of the P3 exit-criteria check): schema, document, grammar and unknown objects.

    gen_p3.py OUT_JSONL [--count N] [--seed S]

Deterministic: the same seed gives the same file. Each line is one JSON object
`{"id": n, "schema": text, "yaml": text, "grammar": 1, "pin": sf["grammar_pin"], "access_keys": sf["access_keys"], "unknown": [paths]}`. The schema body is the one of the shared resolution
cases; its `policy_surface` is drawn (default_access / default_modify as nobody, a list, `open` or `owner`, surface_rules, inherit_policy);
the document is drawn too (which fields are present, short or expanded positions with drawn access / modify / inherit, list elements).
The Rust and Go dumps (`parity_p3_dump`) normalize each input, resolve its policy, and `compare_p3.py` compares the outcomes.
"""
import argparse
import json
import os
import random

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RULES = ["[]", "[O=a]", "[O=b, CN=c]", "[OU=x*]"]
MODES = ["true", "false", "0"]


def shared():
    with open(os.path.join(ROOT, "testdata", "p1", "resolution.json"), encoding="utf-8") as f:
        return json.load(f)


def schema_body(shared_file):
    head, tail = shared_file["cases"][0]["schema"].split("  config_surface:\n", 1)
    return head, "  config_surface:\n" + tail


def policy_surface(r):
    lines = []
    if r.random() < 0.5:
        lines.append("    default_access: " + r.choice(RULES + ["open"]))
    if r.random() < 0.5:
        lines.append("    default_modify: " + r.choice(RULES + ["owner"]))
    if r.random() < 0.3:
        lines.append("    surface_rules:")
        lines.append("      - surface: " + r.choice(["config_surface", "state_surface"]))
        if r.random() < 0.7:
            lines.append("        access: " + r.choice(RULES))
        if r.random() < 0.4:
            lines.append("        modify: " + r.choice(RULES))
    if r.random() < 0.5:
        lines.append("    inherit_policy: " + r.choice(MODES))
    if not lines:
        return "" if r.random() < 0.5 else "  policy_surface: {}\n"
    return "  policy_surface:\n" + "\n".join(lines) + "\n"


def position(name, value, r, indent, block=False):
    """A position: short (`name: value`) or expanded with drawn access / modify / inherit. value is one line or a list of lines."""
    pad = " " * indent
    lines = value if isinstance(value, list) else [value]
    if r.random() < 0.55:
        if len(lines) == 1 and not block:
            return [pad + name + ": " + lines[0]]
        return [pad + name + ":"] + [pad + "  " + v for v in lines]
    out = [pad + name + ":", pad + "  value:" if block or len(lines) > 1 else pad + "  value: " + lines[0]]
    if block or len(lines) > 1:
        out += [pad + "    " + v for v in lines]
    out += [
        pad + "  access: " + r.choice(["inherit"] + RULES),
        pad + "  modify: " + r.choice(["inherit"] + RULES),
        pad + "  inherit: " + r.choice(MODES),
        pad + "  default_injection: ~",
        pad + "  conformance: implemented",
    ]
    return out


def document(r):
    lines = position("type", "t", r, 0)
    if r.random() < 0.5:
        lines += position("label", r.choice(["x", "~"]), r, 0)
    c = r.random()
    if c < 0.15:
        lines += position("ip", "~", r, 0)
    elif c < 0.7:
        inner = []
        if r.random() < 0.8:
            inner += position("gateway", "g", r, 0)
        if r.random() < 0.5:
            inner += position("prefix", "24", r, 0)
        lines += position("ip", inner or ["{}"], r, 0, block=bool(inner))
    if r.random() < 0.6:
        items = []
        for to in r.sample(["a", "b", "c"], r.randint(0, 3)):
            item = ["- to: " + to]
            if r.random() < 0.5:
                item += ["  " + x for x in position("metric", "1", r, 0)]
            items.append(item)
        flat = [x for it in items for x in it]
        lines += position("routes", flat or ["[]"], r, 0, block=bool(flat))
    return "\n".join(lines) + "\n"


def unknown(r, doc):
    out = []
    if r.random() < 0.2:
        out.append("$")
    if r.random() < 0.15 and "ip" in doc:
        out.append("$.ip")
    if r.random() < 0.1:
        out.append('$.routes[{to="a"}]')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261022)
    a = ap.parse_args()
    r = random.Random(a.seed)
    sf = shared()
    head, tail = schema_body(sf)
    with open(a.out, "w", encoding="utf-8") as f:
        for i in range(a.count):
            doc = document(r)
            case = {"id": i, "schema": head + policy_surface(r) + tail, "yaml": doc, "grammar": 1, "pin": sf["grammar_pin"], "access_keys": sf["access_keys"], "unknown": unknown(r, doc)}
            f.write(json.dumps(case, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
