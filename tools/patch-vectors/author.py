#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Authoring tool of corpus/patch/vectors.yaml (ADR 0013, step P4-1).

    author.py [--check]      write corpus/patch/vectors.yaml (or with --check: fail if the file differs from what this tool writes)

The EXPECTATIONS are written here by hand, from the rules of ADR 0013 and docs/P4-DIFF-PATCH.md, and not taken from any implementation: every case
below states the source text, the events of the diff, and for each replaced scalar the token that is there now and the exact text of the token the rules
of ADR 0012 say must be written; for a refusal the code, the phase and the anchor the rules name. The tool only does the byte arithmetic that is tedious
to do by hand (the spans before and after, the output as the source with the tokens replaced, the line and column of an error position, the canonical
bytes of the diff), and `tools/corpus_check.py` re-checks that arithmetic independently (PATCH_BYTES, PATCH_ROUNDTRIP, the diff rules of ADR 0008).
Neither Rust nor Go runs these cases yet (step P4-2).

Conventions of the suite (the trees are a notation, ADR 0005): every field is optional and non-nullable; a document of a positive case validates against its
tree; `routes` is a list whose element identity is the key `to`; `note` is opaque; `tags` is a set. The events are in the canonical order of ADR 0008 (the
segment-wise path order), which is not the order of the document.
"""
import argparse
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT = os.path.join(ROOT, "corpus", "patch", "vectors.yaml")
GRAMMAR = "cic-primitives-73ae469-6a188078"
BOM = chr(0xFEFF)


def q(s):
    return json.dumps(s, ensure_ascii=False)


def as_bytes(src):
    return src if isinstance(src, bytes) else src.encode("utf-8")


def source_field(src):
    """input_yaml for a text without CR and BOM, input_hex for anything else (a text field cannot spell those)."""
    b = as_bytes(src)
    text = b.decode("utf-8")
    if "\r" in text or text.startswith(BOM):
        return "input_hex: %s" % q(b.hex())
    return "input_yaml: %s" % q(text)


def out_field(b):
    text = b.decode("utf-8")
    if "\r" in text or text.startswith(BOM):
        return "output_hex: %s" % q(b.hex())
    return "output_yaml: %s" % q(text)


def loc(data, off):
    """{line, column, offset} of a byte offset (ADR 0007): 1-based, columns in code points, a BOM is no column, CR / LF / CRLF is one break."""
    text = data[:off].decode("utf-8")
    if text.startswith(BOM):
        text = text[1:]
    line, col, i = 1, 1, 0
    while i < len(text):
        ch = text[i]
        if ch == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
            i += 1
            ch = "\n"
        if ch in "\r\n":
            line, col = line + 1, 1
        else:
            col += 1
        i += 1
    return {"line": line, "column": col, "offset": off}


def start_of(src, prefix, token, nth=1):
    b, pt = as_bytes(src), (prefix + token).encode("utf-8")
    idx = -1
    for _ in range(nth):
        idx = b.index(pt, idx + 1)
    return idx + len(prefix.encode("utf-8"))


# --- typed values and events of ADR 0008 (the corpus notation: {type, value}) -------------------------------------------------------------------------
def tv(v):
    if v is None:
        return {"type": "null", "value": None}
    if isinstance(v, bool):
        return {"type": "boolean", "value": v}
    if isinstance(v, int):
        return {"type": "integer", "value": v}
    if isinstance(v, list):
        return {"type": "list", "value": v}
    return {"type": "string", "value": v}


def frag(value):
    """The expanded fragment of a position that appears or disappears (ADR 0008)."""
    return {"type": "map", "value": {"access": "inherit", "conformance": "implemented", "default_injection": None, "inherit": True, "modify": "inherit", "value": value}}


def vc(path, prev, nxt):
    return {"kind": "value_change", "path": path, "previous": tv(prev), "next": tv(nxt)}


def added(path, value):
    return {"kind": "added", "path": path, "next": frag(value)}


def removed(path, value):
    return {"kind": "removed", "path": path, "previous": frag(value)}


def policy(path, field, prev, nxt):
    return {"kind": "policy_declaration_change", "path": path, "changed": {field: {"previous": tv(prev), "next": tv(nxt)}}}


def set_change(path, add, rem):
    return {"kind": "set_change", "path": path, "added": tv(add), "removed": tv(rem)}


def repr_entry(path, prev, nxt):
    return {"kind": "representation_change", "path": path, "previous": prev, "next": nxt}


def native(e):
    """The plain JSON of an event: the typed wrapper is the corpus notation, the canonical bytes of ADR 0008 are plain."""
    out = {}
    for k, v in e.items():
        if k == "changed":
            out[k] = {f: {"previous": x["previous"]["value"], "next": x["next"]["value"]} for f, x in v.items()}
        elif isinstance(v, dict) and "type" in v:
            out[k] = v["value"]
        else:
            out[k] = v
    return out


def canonical(events, reprs):
    return json.dumps({"version": 1, "changes": [native(e) for e in events], "representation": reprs}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def diff_field(events, reprs=()):
    reprs = list(reprs)
    return "diff: " + q({"changes": events, "representation": reprs, "canonical": canonical(events, reprs)})


def direct_field(events):
    """A diff handed to the patch directly (ADR 0013, the amendment of 2026-10-29): events and version, no canonical bytes. It is the one way to hold a path twice."""
    return "diff: " + q({"changes": events, "version": 1})


CASES = []


def add(cid, polarity, desc, src, events, reprs, expect, tree="doc", extra=(), status=None):
    lines = ["  - id: patch.%s" % cid, "    kind: patch", "    polarity: %s" % polarity, "    description: %s" % q(desc),
             "    schema: {synthetic: {tree: %s}}" % tree, "    " + source_field(src)]
    lines += ["    " + e for e in extra]
    lines.append("    " + (events if isinstance(events, str) else diff_field(events, reprs)))
    if status:
        lines.append("    codes_status: %s" % status)
    lines.append("    expect: %s" % expect)
    CASES.append("\n".join(lines))


def ok(cid, desc, src, events, edits, reprs=(), tree="doc", extra=(), round_trip=None):
    """A success. `events`: the value_change events in canonical order. `edits`: for each event, in the same order, (prefix, old token, new token[, nth]):
    the token that is in the source now and the token that must be there afterwards (written by hand)."""
    b = as_bytes(src)
    rows = []
    for e, ed in zip(events, edits):
        prefix, old, new = ed[0], ed[1], ed[2]
        nth = ed[3] if len(ed) > 3 else 1
        s = start_of(src, prefix, old, nth)
        rows.append((s, s + len(old.encode("utf-8")), new.encode("utf-8"), e["path"]))
    order = sorted(range(len(rows)), key=lambda i: rows[i][0])
    out, pos, delta, after = b"", 0, 0, {}
    for i in order:
        s, e_, new, path = rows[i]
        out += b[pos:s] + new
        pos = e_
        after[i] = (s + delta, s + delta + len(new))
        delta += len(new) - (e_ - s)
    out += b[pos:]
    spans = [{"path": rows[i][3], "before": {"start": rows[i][0], "end": rows[i][1]}, "after": {"start": after[i][0], "end": after[i][1]}} for i in range(len(rows))]
    ex = list(extra)
    if round_trip is not None:
        ex.append("round_trip: " + q(round_trip))
    expect = "{%s, replaced: %s, spans: %s}" % (out_field(out), q([e["path"] for e in events]), q(spans))
    add(cid, "positive", desc, src, events, reprs, expect, tree=tree, extra=ex)


def bad(cid, desc, src, events, code, phase, at, logical_path=None, reprs=(), tree="doc", extra=(), status="stable"):
    """A refusal. `at`: a byte offset of the anchor, or {state: not_applicable|unavailable}."""
    if isinstance(at, str):
        where = "{state: %s}" % at
    else:
        l = loc(as_bytes(src), at)
        where = "{line: %d, column: %d, offset: %d}" % (l["line"], l["column"], l["offset"])
    e = "{code: %s, phase: %s, %ssource_location: %s}" % (code, phase, ("logical_path: %s, " % q(logical_path)) if logical_path else "", where)
    add(cid, "negative", desc, src, events, reprs, "{errors: [%s]}" % e, tree=tree, extra=extra, status=status)


D1 = "name: alpha\ncount: 3\nenabled: true\n"
IP = "ip:\n  gateway: g1\n  prefix: 24\n"
LIST = "routes:\n  - to: a\n    metric: 1\n  - to: b\n    metric: 2\n"
EXP = ("name:\n  value: alpha\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\ncount: 3\n")


def render():
    CASES.clear()
    # ---- success: one change ----------------------------------------------------------------------------------------------------------------------
    ok("apply-a-string", "one string scalar is replaced, nothing else moves", D1, [vc("$.name", "alpha", "beta")], [("name: ", "alpha", "beta")])
    ok("apply-an-integer", "one integer scalar", D1, [vc("$.count", 3, 4)], [("count: ", "3", "4")])
    ok("apply-a-boolean", "one boolean scalar", D1, [vc("$.enabled", True, False)], [("enabled: ", "true", "false")])
    ok("apply-a-negative-integer", "the new value is written in the canonical decimal spelling", D1, [vc("$.count", 3, -5)], [("count: ", "3", "-5")])
    ok("apply-a-nested-field", "a scalar below a composite", IP, [vc("$.ip.gateway", "g1", "g2")], [("gateway: ", "g1", "g2")])
    ok("apply-a-list-element-by-identity", "an element of a list is found by its identity, not by its index", LIST,
       [vc('$.routes[{to="b"}].metric', 2, 9)], [("metric: ", "2", "9")])
    ok("apply-a-list-element-after-reorder", "the order of the list in the document is not the order of the diff", "routes:\n  - to: b\n    metric: 2\n  - to: a\n    metric: 1\n",
       [vc('$.routes[{to="b"}].metric', 2, 9)], [("metric: ", "2", "9")])
    ok("apply-a-value-in-an-expanded-position", "the value of an expanded position is replaced, its other members are not touched", EXP,
       [vc("$.name", "alpha", "beta")], [("  value: ", "alpha", "beta")])
    # ---- success: several changes -----------------------------------------------------------------------------------------------------------------
    ok("apply-three-in-canonical-order", "the events are in path order, the document is not: `replaced` follows the diff", D1,
       [vc("$.count", 3, 4), vc("$.enabled", True, False), vc("$.name", "alpha", "beta")],
       [("count: ", "3", "4"), ("enabled: ", "true", "false"), ("name: ", "alpha", "beta")])
    ok("apply-two-in-one-composite", "two fields of one composite, one longer and one shorter", IP, [vc("$.ip.gateway", "g1", "gateway-2"), vc("$.ip.prefix", 24, 8)],
       [("gateway: ", "g1", "gateway-2"), ("prefix: ", "24", "8")])
    ok("apply-two-list-elements", "two elements of one list", LIST, [vc('$.routes[{to="a"}].metric', 1, 10), vc('$.routes[{to="b"}].metric', 2, 20)],
       [("metric: ", "1", "10"), ("metric: ", "2", "20")])
    # ---- success: the bytes around the scalar -----------------------------------------------------------------------------------------------------
    ok("apply-keeps-a-single-quoted-style", "the quotes stay, a quote in the value is doubled", "name: 'alpha'\ncount: 3\n", [vc("$.name", "alpha", "it's")], [("name: ", "'alpha'", "'it''s'")])
    ok("apply-keeps-a-double-quoted-style", "the quotes stay, a quote and a backslash in the value are escaped", 'name: "alpha"\ncount: 3\n',
       [vc("$.name", "alpha", 'say "hi" \\')], [("name: ", '"alpha"', '"say \\"hi\\" \\\\"')])
    ok("apply-keeps-comments", "the comments around the scalars are not touched", "# top\nname: alpha   # the host\n# between\ncount: 3 # n\n",
       [vc("$.count", 3, 4), vc("$.name", "alpha", "beta")], [("count: ", "3", "4"), ("name: ", "alpha", "beta")])
    ok("apply-keeps-crlf", "CRLF line breaks are not touched", "name: alpha\r\ncount: 3\r\n", [vc("$.name", "alpha", "beta")], [("name: ", "alpha", "beta")])
    ok("apply-keeps-a-bom", "a leading BOM stays and counts in the offsets", BOM + "name: alpha\ncount: 3\n", [vc("$.count", 3, 4)], [("count: ", "3", "4")])
    ok("apply-keeps-a-missing-final-newline", "a text without a final line break stays without one", "name: alpha\ncount: 3", [vc("$.count", 3, 44)], [("count: ", "3", "44")])
    ok("apply-keeps-non-ascii-text", "the offsets are bytes: the text around and in the value has multi-byte characters", "name: tűrő\ncount: 3\n",
       [vc("$.count", 3, 1000), vc("$.name", "tűrő", "árvíz")], [("count: ", "3", "1000"), ("name: ", "tűrő", "árvíz")])
    ok("apply-matches-a-noncanonical-previous", "True is the value true: `previous` is a value, not a spelling", "enabled: True\n", [vc("$.enabled", True, False)], [("enabled: ", "True", "false")])
    ok("apply-matches-a-signed-previous", "+5 is the integer 5", "count: +5\n", [vc("$.count", 5, 6)], [("count: ", "+5", "6")])
    # ---- success: the diff says nothing to apply ---------------------------------------------------------------------------------------------------
    ok("an-empty-diff-is-the-identity", "no change: the text is returned as it was, and nothing was replaced", D1, [], [])
    ok("representation-entries-are-not-applied", "a representation difference is evidence of writing, never a change: the text is returned as it was", D1, [],
       [], reprs=[repr_entry("$.name", "short", "expanded")])
    ok("representation-next-to-a-value-change", "the value change is applied, the representation entry is ignored (the position stays short)", D1,
       [vc("$.name", "alpha", "beta")], [("name: ", "alpha", "beta")], reprs=[repr_entry("$.name", "short", "expanded")])
    # ---- the round trip against B (ADR 0013, section 4, proved from outside) -----------------------------------------------------------------------
    ok("round-trip-full-scope", "the diff against B is empty after the patch: B is written with other quotes and a comment, the logical content is the same", D1,
       [vc("$.count", 3, 4), vc("$.name", "alpha", "beta")], [("count: ", "3", "4"), ("name: ", "alpha", "beta")],
       round_trip={"b_yaml": "name: 'beta'   # the host\ncount: 4\nenabled: true\n"})
    ok("round-trip-short-versus-expanded", "B writes the position expanded: the same logical content", D1, [vc("$.name", "alpha", "beta")], [("name: ", "alpha", "beta")],
       reprs=[repr_entry("$.name", "short", "expanded")],
       round_trip={"b_yaml": "name:\n  value: beta\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\ncount: 3\nenabled: true\n"})
    ok("round-trip-partial-scope", "the diff was made over a scope: B differs from the patched text outside it, and the claim is only for the observed region", D1,
       [vc("$.name", "alpha", "beta")], [("name: ", "alpha", "beta")],
       round_trip={"b_yaml": "name: beta\ncount: 99\nenabled: false\n", "scope": ["$.name"]})
    ok("round-trip-list-reordered", "B lists the elements in another order: the logical content is the same", LIST, [vc('$.routes[{to="b"}].metric', 2, 9)], [("metric: ", "2", "9")],
       round_trip={"b_yaml": "routes:\n  - to: b\n    metric: 9\n  - to: a\n    metric: 1\n"})
    # ---- the canonical bytes of the diff may be given alone: a diff read back from its bytes --------------------------------------------------------
    # (the structured events and the bytes of every case above agree, the checker holds them to it)

    # ---- refusals: a conflict ---------------------------------------------------------------------------------------------------------------------
    bad("conflict-the-value-differs", "A holds 3, the diff says it was 5", D1, [vc("$.count", 5, 6)], "PATCH_CONFLICT", "patch", start_of(D1, "count: ", "3"), "$.count")
    bad("conflict-is-type-strict-false-versus-zero", "false is not 0", "enabled: false\n", [vc("$.enabled", 0, 1)], "PATCH_CONFLICT", "patch", start_of("enabled: false\n", "enabled: ", "false"), "$.enabled")
    bad("conflict-is-type-strict-string-versus-integer", "the string '3' is not the integer 3", "name: '3'\n", [vc("$.name", 3, 4)], "PATCH_CONFLICT", "patch", start_of("name: '3'\n", "name: ", "'3'"), "$.name")
    bad("conflict-a-replay-of-an-applied-diff", "the diff is applied to its own result: the previous value is not there any more", "name: beta\ncount: 3\n", [vc("$.name", "alpha", "beta")],
        "PATCH_CONFLICT", "patch", start_of("name: beta\ncount: 3\n", "name: ", "beta"), "$.name")
    bad("conflict-names-the-first-event-in-canonical-order", "two events conflict: the first of them in the canonical order is the error",
        D1, [vc("$.count", 9, 10), vc("$.name", "x", "y")], "PATCH_CONFLICT", "patch", start_of(D1, "count: ", "3"), "$.count")
    bad("conflict-leaves-nothing-applied", "the first event would apply, the second conflicts: there is an error and no text", D1,
        [vc("$.count", 3, 4), vc("$.enabled", False, True)], "PATCH_CONFLICT", "patch", start_of(D1, "enabled: ", "true"), "$.enabled")
    bad("conflict-in-a-list-element", "the element is found by its identity and its value differs", LIST, [vc('$.routes[{to="b"}].metric', 5, 6)],
        "PATCH_CONFLICT", "patch", start_of(LIST, "metric: ", "2"), '$.routes[{to="b"}].metric')
    # ---- refusals: the position is not there -------------------------------------------------------------------------------------------------------
    bad("target-absent-a-field-the-document-does-not-spell", "the field is in the schema, the document does not hold it: anchored at the enclosing structure", "name: alpha\n",
        [vc("$.count", 3, 4)], "PATCH_TARGET_ABSENT", "patch", 0, "$.count")
    bad("target-absent-a-list-element-of-another-identity", "the list holds no element of that identity: anchored at the list", LIST, [vc('$.routes[{to="z"}].metric', 1, 2)],
        "PATCH_TARGET_ABSENT", "patch", start_of(LIST, "  ", "- to: a"), '$.routes[{to="z"}].metric')
    bad("target-absent-comes-after-a-conflict-in-path-order", "the order of the checks is the order of the events: the conflict at $.count is named, not the absent $.name", "count: 3\n",
        [vc("$.count", 9, 10), vc("$.name", "a", "b")], "PATCH_CONFLICT", "patch", start_of("count: 3\n", "count: ", "3"), "$.count")
    bad("target-absent-comes-before-a-conflict-in-path-order", "the first failing event in the canonical order is named", "count: 3\nname: alpha\n",
        [vc("$.count", 3, 4), vc("$.enabled", True, False), vc("$.name", "x", "y")], "PATCH_TARGET_ABSENT", "patch", 0, "$.enabled")
    # ---- refusals: an event the first unit does not apply (the whole patch is refused) ---------------------------------------------------------------
    bad("unsupported-an-added-position", "insertion is a later unit", D1, [added("$.ip.gateway", "g1")], "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.ip.gateway")
    bad("unsupported-a-removed-position", "deletion is a later unit", D1, [removed("$.name", "alpha")], "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.name")
    bad("unsupported-a-declaration-change", "the five declaration fields of an expanded position are a later unit", EXP, [policy("$.name", "access", "inherit", ["OU=ops"])],
        "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.name")
    bad("unsupported-a-set-change", "a set has no addressable elements", "tags:\n  - a\n", [set_change("$.tags", ["b"], [])], "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.tags")
    bad("unsupported-a-value-change-of-an-opaque-value", "an opaque value is not a scalar of the first unit", "note: 0\n", [vc("$.note", 0, False)],
        "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.note")
    bad("unsupported-a-value-change-of-a-composite", "a composite is not a scalar", IP, [vc("$.ip", "x", "y")], "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.ip")
    bad("unsupported-a-value-change-of-a-key-component", "a changed identity is a removal plus an addition, never a value change", LIST, [vc('$.routes[{to="a"}].to', "a", "c")],
        "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", '$.routes[{to="a"}].to')
    bad("unsupported-with-a-supported-event-refuses-the-whole-patch", "the value change would apply, the set change is not applied: there is an error and no text", "count: 3\ntags:\n  - a\n",
        [vc("$.count", 3, 4), set_change("$.tags", ["b"], [])], "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.tags")
    bad("unsupported-is-named-before-a-conflict", "the diff as a whole is checked before the values: the unsupported event is the error, although a conflict comes first in path order",
        "count: 3\ntags:\n  - a\n", [vc("$.count", 9, 10), set_change("$.tags", ["b"], [])], "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.tags")
    # ---- refusals: the context and the version -----------------------------------------------------------------------------------------------------
    bad("context-does-not-match-the-schema", "the caller says the diff was made under another schema: the patch checks that the context matches the schema it was given", D1,
        [vc("$.name", "alpha", "beta")], "PATCH_CONTEXT_MISMATCH", "patch", "not_applicable", extra=["context: {synthetic: {tree: other}}"])
    bad("unknown-version-of-the-diff", "a diff of a version the patch does not know", D1, 'diff: {"canonical": "{\\"changes\\":[],\\"representation\\":[],\\"version\\":2}"}',
        "PATCH_CONTEXT_MISMATCH", "patch", "not_applicable")
    # ---- refusals: one path, one event (ADR 0013, the amendment of 2026-10-29; the diff is handed to the patch directly) -----------------------------------
    bad("duplicate-target-the-later-overwrites", "two events replace the same scalar with different values: both match A, and the later would silently win", D1,
        direct_field([vc("$.count", 3, 4), vc("$.count", 3, 5)]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.count")
    bad("duplicate-target-the-same-event-twice", "the same event twice is a duplicate too: it is not collapsed", D1,
        direct_field([vc("$.count", 3, 4), vc("$.count", 3, 4)]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.count")
    bad("duplicate-target-in-a-list-element", "the path names a list element by its identity", LIST,
        direct_field([vc('$.routes[{to="b"}].metric', 2, 7), vc('$.routes[{to="b"}].metric', 2, 8)]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", '$.routes[{to="b"}].metric')
    bad("duplicate-target-between-other-events", "the repeated path is not next to its first event; the later one is named", D1,
        direct_field([vc("$.count", 3, 4), vc("$.enabled", True, False), vc("$.count", 3, 5)]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.count")
    bad("duplicate-target-leaves-nothing-applied", "the first event would apply: there is an error and no text", D1,
        direct_field([vc("$.name", "alpha", "beta"), vc("$.name", "alpha", "gamma")]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.name")
    bad("duplicate-target-comes-before-the-conflict", "the repeated path also conflicts with A: row 3a is decided before the values are compared (row 6)", D1,
        direct_field([vc("$.count", 9, 4), vc("$.count", 9, 5)]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.count")
    bad("duplicate-target-comes-before-the-path-error", "the repeated path does not resolve against the schema: row 3a is decided before the paths are resolved (row 4)", D1,
        direct_field([vc("$.zzz", "a", "b"), vc("$.zzz", "a", "c")]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.zzz")
    bad("duplicate-target-comes-before-the-absent-target", "the repeated path is a field A does not hold: row 3a is decided before row 5", "name: alpha\n",
        direct_field([vc("$.count", 3, 4), vc("$.count", 3, 5)]), "PATCH_DUPLICATE_TARGET", "patch", "not_applicable", "$.count")
    bad("unsupported-event-comes-before-the-duplicate", "an event the first unit does not apply is row 3 and is decided over every event first: it is named, not the duplicate", D1,
        direct_field([vc("$.count", 3, 4), vc("$.count", 3, 5), vc("$.ip", "a", "b")]), "PATCH_UNSUPPORTED_EVENT", "patch", "not_applicable", "$.ip")
    bad("context-mismatch-comes-before-the-duplicate", "the context is row 2", D1, direct_field([vc("$.count", 3, 4), vc("$.count", 3, 5)]),
        "PATCH_CONTEXT_MISMATCH", "patch", "not_applicable", extra=["context: {synthetic: {tree: other}}"])
    bad("earlier-phase-validate-comes-before-the-duplicate", "A does not validate: the validate phase is first", "bogus: 1\n",
        direct_field([vc("$.count", 3, 4), vc("$.count", 3, 5)]), "UNKNOWN_FIELD", "validate", 0, "$.bogus")
    # ---- refusals of the earlier phases keep their own code and phase ---------------------------------------------------------------------------------
    bad("earlier-phase-a-parse-error", "A does not parse: the error of the parse phase, with its own code", "name: a\nname: b\n", [vc("$.name", "a", "b")], "DUPLICATE_KEY", "parse", 8)
    bad("earlier-phase-a-document-that-does-not-validate", "A does not validate: the violation of the validate phase, never a patch error", "bogus: 1\n", [vc("$.name", "a", "b")],
        "UNKNOWN_FIELD", "validate", 0, "$.bogus")
    bad("earlier-phase-validate-comes-before-the-context", "an invalid document and a context that does not match: the validate phase is first", "bogus: 1\n", [vc("$.name", "a", "b")],
        "UNKNOWN_FIELD", "validate", 0, "$.bogus", extra=["context: {synthetic: {tree: other}}"])
    bad("earlier-phase-a-path-the-schema-does-not-resolve", "the path of an event is not in the schema: the path error, not PATCH_TARGET_ABSENT", D1, [vc("$.zzz", "a", "b")],
        "UNKNOWN_FIELD", "path_resolve", "not_applicable", "$.zzz")
    # ---- refusals of the replacement itself keep the code and the phase of ADR 0012 --------------------------------------------------------------------
    bad("edit-a-block-scalar", "the scalar is a block scalar", "name: |\n  alpha\ncount: 3\n", [vc("$.name", "alpha\n", "beta")], "EDIT_BLOCK_SCALAR", "edit",
        start_of("name: |\n  alpha\ncount: 3\n", "name: ", "|"), "$.name")
    bad("edit-a-scalar-in-a-flow-collection", "the scalar is inside a flow collection", "ip: {gateway: g1, prefix: 24}\n", [vc("$.ip.gateway", "g1", "g2")], "EDIT_FLOW_CONTEXT", "edit",
        start_of("ip: {gateway: g1, prefix: 24}\n", "gateway: ", "g1"), "$.ip.gateway")
    bad("edit-a-multi-line-scalar", "the scalar continues on the next line", "name: alpha\n  beta\ncount: 3\n", [vc("$.name", "alpha beta", "gamma")], "EDIT_MULTILINE_SCALAR", "edit",
        start_of("name: alpha\n  beta\ncount: 3\n", "name: ", "alpha"), "$.name")
    bad("edit-a-plain-scalar-cannot-carry-the-new-value", "the style is never changed: `a: b` is not a plain scalar", D1, [vc("$.name", "alpha", "a: b")], "EDIT_STYLE_CONFLICT", "edit",
        start_of(D1, "name: ", "alpha"), "$.name")
    bad("edit-the-new-value-has-a-line-break", "a new value never holds a line break or a control character", D1, [vc("$.name", "alpha", "a\nb")], "EDIT_VALUE_NOT_REPRESENTABLE", "edit",
        start_of(D1, "name: ", "alpha"), "$.name")
    bad("edit-the-new-value-is-not-the-kind-of-the-position", "a string for an integer position", D1, [vc("$.count", 3, "three")], "EDIT_TYPE_MISMATCH", "edit",
        start_of(D1, "count: ", "3"), "$.count")
    bad("edit-leaves-nothing-applied", "the first event would apply, the replacement of the second is refused: there is an error and no text", D1,
        [vc("$.count", 3, 4), vc("$.name", "alpha", "a: b")], "EDIT_STYLE_CONFLICT", "edit", start_of(D1, "name: ", "alpha"), "$.name")
    return build()


def build():
    head = """# Patch cases (ADR 0013, P4-1). Written by hand from the rules of ADR 0013 and docs/P4-DIFF-PATCH.md; the tool tools/patch-vectors/author.py only does the byte
# arithmetic and tools/corpus_check.py re-checks it (PATCH_BYTES, PATCH_ROUNDTRIP, the diff rules of ADR 0008). Neither Rust nor Go runs these cases yet: the kind is
# NOT RUN until step P4-2. Do not edit this file by hand: change the tool and run it.
# A success holds the output bytes, `replaced` (the paths, in the canonical order of ADR 0008) and `spans` (the byte span of each replaced scalar before and after,
# half-open, a leading BOM counted). A failure holds one error and no text. `diff` is the diff of ADR 0008: the structured events and their canonical bytes (a diff of
# another version is given as its bytes alone). `context` is the context the caller passes (the schema tree the diff is said to have been made under); it is the schema
# of the case when absent. `round_trip` names a B (and optionally a scope) for the property of ADR 0013, section 4, which the patch itself cannot check.
# Conventions: every field of the trees is optional and non-nullable; `routes` is a list whose element identity is the key `to`; `note` is opaque; `tags` is a set.
corpus_format: 2
suite: patch
status: provisional
defaults: {adr: ADR-0013, grammar: %s}
trees:
  doc: {name: {scalar: string}, count: {scalar: integer}, enabled: {scalar: boolean}, ip: {composite: {gateway: {scalar: string}, prefix: {scalar: integer}}}, routes: {list: {key: [to], fields: {to: {scalar: string}, metric: {scalar: integer}}}}, tags: {set: string}, note: opaque}
  other: {name: {scalar: integer}}
cases:
""" % GRAMMAR
    return head + "\n".join(CASES) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = render()
    if a.check:
        with open(OUT, encoding="utf-8") as fh:
            if fh.read() != text:
                print("corpus/patch/vectors.yaml differs from what tools/patch-vectors/author.py writes")
                return 1
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
