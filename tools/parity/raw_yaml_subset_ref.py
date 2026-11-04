#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A reference reader of the DOCUMENTED YAML subset (docs/P1-SUPPORTED-SUBSET.md, "Supported subset" and "Explicitly not supported"), built on the PyYAML event stream.

It is a yardstick for the tests of the raw YAML generator (tools/parity/gen_raw_yaml.py), nothing more: it checks that the labels the generator writes by construction are
consistent with an executable reading of the documented table. It is NOT one of the readers of the proof (those are the Rust and Go parsers; docs/P5-RAW-YAML-UNIT3-CONTRACT.md),
it is not the subset gate of decision D9, and it makes no claim about the Forms parsers.

    read(data: bytes) -> ("accept", typed_tree) | ("refuse", "syntax") | ("refuse", "duplicate_key")

A typed tree is {"k": "map", "v": [[key, node], ...]} (order kept), {"k": "seq", "v": [...]}, {"k": "str" | "int" | "bool" | "null", "v": ...}.
A syntax problem anywhere wins over a duplicate key (docs/P1-SUPPORTED-SUBSET.md: "a syntax error anywhere wins"); a case where the two meet is labelled "any refusal" by the generator.
"""
import re

import yaml

MAX_BYTES = 1 << 20
MAX_DEPTH = 64
MAX_NODES = 100000
INT64 = (-(1 << 63), (1 << 63) - 1)
CONTROL = set(range(0, 9)) | {0x0B, 0x0C} | set(range(0x0E, 0x20)) | {0x7F}
LINE_BREAKS = {0x85, 0x2028, 0x2029}
INT = re.compile(r"[+-]?[0-9]+")
FLOATISH = re.compile(r"[+-]?(\.[0-9]+|[0-9]+\.[0-9]*)([eE][+-]?[0-9]+)?|[+-]?[0-9]+[eE][+-]?[0-9]+|[+-]?\.(inf|Inf|INF)|\.(nan|NaN|NAN)|0[xX][0-9a-fA-F]+|0[oO][0-7]+")
BOOLS = {"true": True, "True": True, "TRUE": True, "false": False, "False": False, "FALSE": False}


class Syntax(Exception):
    pass


def scalar(ev):
    value = ev.value
    plain = ev.style is None and ev.implicit[0]
    if not plain:
        return {"k": "str", "v": value}
    if value in ("", "null", "~"):
        return {"k": "null", "v": None}
    if value in BOOLS:
        return {"k": "bool", "v": BOOLS[value]}
    if INT.fullmatch(value):
        digits = value.lstrip("+-")
        if len(digits) > 1 and digits.startswith("0"):
            raise Syntax("leading zero")
        n = int(value)
        if not INT64[0] <= n <= INT64[1]:
            raise Syntax("integer beyond 64 bits")
        return {"k": "int", "v": n}
    if FLOATISH.fullmatch(value):
        raise Syntax("float, hex or octal")
    return {"k": "str", "v": value}


def read(data):
    if len(data) > MAX_BYTES:
        return ("refuse", "syntax")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ("refuse", "syntax")
    for i, ch in enumerate(text):
        c = ord(ch)
        if c in CONTROL or ch == "\t" or c in LINE_BREAKS or (c == 0xFEFF and i > 0):
            return ("refuse", "syntax")
    for line in text.split("\n"):
        if line.startswith("%") or line.rstrip() == "...":
            return ("refuse", "syntax")
    duplicate = False
    try:
        root, stack, documents, nodes = None, [], 0, 0
        for ev in yaml.parse(text):
            if isinstance(ev, yaml.AliasEvent):
                raise Syntax("alias")
            if isinstance(ev, yaml.DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise Syntax("more than one document")
                continue
            if isinstance(ev, (yaml.ScalarEvent, yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                if ev.anchor is not None or (ev.tag is not None):
                    raise Syntax("anchor or tag")
                nodes += 1
                if nodes > MAX_NODES:
                    raise Syntax("too many nodes")
            if isinstance(ev, yaml.ScalarEvent):
                node = scalar(ev)
                _put(stack, node, ev)
            elif isinstance(ev, yaml.MappingStartEvent):
                if len(stack) >= MAX_DEPTH:
                    raise Syntax("too deep")
                stack.append({"k": "map", "v": [], "pending": [], "keys": set()})
            elif isinstance(ev, yaml.SequenceStartEvent):
                if len(stack) >= MAX_DEPTH:
                    raise Syntax("too deep")
                stack.append({"k": "seq", "v": []})
            elif isinstance(ev, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                top = stack.pop()
                node = {"k": top["k"], "v": top["v"]}
                if top.get("dup"):
                    duplicate = True
                if stack:
                    _put(stack, node, ev)
                else:
                    root = node
    except (yaml.YAMLError, Syntax):
        return ("refuse", "syntax")
    if root is None:
        return ("refuse", "syntax")
    return ("refuse", "duplicate_key") if duplicate else ("accept", root)


def _put(stack, node, ev):
    if not stack:
        raise Syntax("a scalar document")
    top = stack[-1]
    if top["k"] == "seq":
        top["v"].append(node)
        return
    if not top["pending"]:
        if not isinstance(ev, yaml.ScalarEvent):
            raise Syntax("non-scalar key")
        key = ev.value
        if key == "<<" and ev.style is None:
            raise Syntax("merge key")
        top["pending"].append(key)
        return
    key = top["pending"].pop()
    if key in top["keys"]:
        top["dup"] = True
    top["keys"].add(key)
    top["v"].append([key, node])
