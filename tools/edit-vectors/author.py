#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Authoring tool of corpus/edit/vectors.yaml (ADR 0012, step E1).

    author.py [--check]      write corpus/edit/vectors.yaml (or with --check: fail if the file differs from what this tool writes)

The EXPECTATIONS are written here by hand, from the rules of ADR 0012 and docs/P3B-SOURCE-PRESERVING-EDIT.md, and not taken from any
implementation: every case below states the source text, the existing token of the scalar, the new value and the exact text of the
token the rules say must be written. The tool only does the byte arithmetic that is tedious to do by hand (the offsets of the span, the
line and column of an error position, the output bytes as prefix + new token + suffix), and `tools/corpus_check.py` re-checks that
arithmetic independently (EDIT_BYTES, ERROR_LOCATION). Neither Rust nor Go runs these cases yet (steps E2 and E3).

Conventions of the suite (the trees are a notation, ADR 0005): every field is optional and non-nullable; a document of a positive case
validates against its tree; `routes` is a list whose element identity is the key `to`.
"""
import argparse
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT = os.path.join(ROOT, "corpus", "edit", "vectors.yaml")
GRAMMAR = "cic-primitives-73ae469-6a188078"
BOM = "﻿"


def q(s):
    return json.dumps(s, ensure_ascii=False)


def as_bytes(src):
    return src if isinstance(src, bytes) else src.encode("utf-8")


def source_field(src):
    """input_yaml for a text without CR and BOM, input_hex for anything else (a text field cannot spell those)."""
    b = as_bytes(src)
    try:
        text = b.decode("utf-8")
    except UnicodeDecodeError:
        return "input_hex: %s" % q(b.hex())
    if "\r" in text or text.startswith(BOM):
        return "input_hex: %s" % q(b.hex())
    return "input_yaml: %s" % q(text)


def out_field(b):
    try:
        text = b.decode("utf-8")
    except UnicodeDecodeError:
        return "output_hex: %s" % q(b.hex())
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
    return "{line: %d, column: %d, offset: %d}" % (line, col, off)


def start_of(src, prefix, token, nth=1):
    b, pt = as_bytes(src), (prefix + token).encode("utf-8")
    idx = -1
    for _ in range(nth):
        idx = b.index(pt, idx + 1)
    return idx + len(prefix.encode("utf-8"))


CASES = []


def add(cid, kind, polarity, desc, path, src=None, extra=(), expect="", tree="doc", status=None):
    lines = ["  - id: edit.%s" % cid, "    kind: %s" % kind, "    polarity: %s" % polarity, "    description: %s" % q(desc),
             "    schema: {synthetic: {tree: %s}}" % tree, "    path: %s" % q(path)]
    if src is not None:
        lines.append("    " + source_field(src))
    lines += ["    " + e for e in extra]
    if status:
        lines.append("    codes_status: %s" % status)
    lines.append("    expect: %s" % expect)
    CASES.append("\n".join(lines))


def span(cid, desc, path, src, prefix, token, style, nth=1):
    s = start_of(src, prefix, token, nth)
    e = s + len(token.encode("utf-8"))
    add(cid, "source_span", "positive", desc, path, src, expect="{span: {start: %d, end: %d, style: %s}}" % (s, e, style))


def error(cid, kind, desc, path, src, code, phase, at, logical_path=None, value=None, input_doc=None):
    """at: a byte offset of the anchor, or one of the states {state: not_applicable|unavailable}."""
    extra = []
    if value is not None:
        extra.append("value: %s" % value)
    if input_doc is not None:
        extra.append("input: %s" % input_doc)
    where = at if isinstance(at, str) else loc(as_bytes(src), at)
    e = "{code: %s, phase: %s, %ssource_location: %s}" % (code, phase, ("logical_path: %s, " % q(logical_path)) if logical_path else "", where)
    add(cid, kind, "negative", desc, path, src if input_doc is None else None, extra, "{errors: [%s]}" % e, status="stable")


def edit(cid, desc, path, src, prefix, token, value, new_token, nth=1):
    b = as_bytes(src)
    s = start_of(src, prefix, token, nth)
    e = s + len(token.encode("utf-8"))
    nt = new_token.encode("utf-8")
    out = b[:s] + nt + b[e:]
    expect = "{%s, span_before: {start: %d, end: %d}, span_after: {start: %d, end: %d}}" % (out_field(out), s, e, s, s + len(nt))
    add(cid, "edit", "positive", desc, path, src, ["value: %s" % value], expect)


D1 = "name: alpha\ncount: 3\nenabled: true\n"
EXP = ("name:\n  value: alpha\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n"
       "  conformance: implemented\ncount: 3\n")
LIST = "routes:\n  - to: a\n    metric: 1\n  - to: b\n    metric: 2\n"
LIST_REVERSED = "routes:\n  - to: b\n    metric: 2\n  - to: a\n    metric: 1\n"
LEL_B_TO = '$.routes[{to="b"}].to'
LEL_B_METRIC = '$.routes[{to="b"}].metric'
LEL_A_METRIC = '$.routes[{to="a"}].metric'
NOT_REP = "EDIT_VALUE_NOT_REPRESENTABLE"

# ---- source_span: where the scalar of a position is, positive ----------------------------------------------------------------------
span("span-plain", "a plain scalar: from its first character to its last", "$.name", D1, "name: ", "alpha", "plain")
span("span-single-quoted", "a single-quoted scalar: both quotes are in the span", "$.name", "name: 'alpha'\ncount: 3\n", "name: ", "'alpha'", "single_quoted")
span("span-double-quoted", "a double-quoted scalar: both quotes are in the span", "$.name", 'name: "alpha"\ncount: 3\n', "name: ", '"alpha"', "double_quoted")
span("span-trailing-comment", "the spaces and the comment after a plain scalar are not in the span", "$.name", "name: alpha   # the host\ncount: 3\n", "name: ", "alpha", "plain")
span("span-integer", "an integer is a plain scalar", "$.count", D1, "count: ", "3", "plain")
span("span-boolean-noncanonical", "a boolean spelled True is a plain scalar of four bytes", "$.enabled", "enabled: True\n", "enabled: ", "True", "plain")
span("span-nested-field", "a field of a composite, in block style", "$.ip.gateway", "ip:\n  gateway: g1\n  prefix: 24\n", "gateway: ", "g1", "plain")
span("span-expanded-value", "in expanded form the span is the value: scalar, not the wrapper", "$.name", EXP, "  value: ", "alpha", "plain")
span("span-list-element-by-identity", "a list element is found by its key, not by its index", LEL_B_METRIC, LIST, "metric: ", "2", "plain")
span("span-list-element-after-reorder", "the same path after the elements changed places: the span moves with the element", LEL_B_METRIC, LIST_REVERSED, "metric: ", "2", "plain")
span("span-equal-text-elsewhere", "the same text appears twice; the span is the addressed one", "$.ip.gateway", "name: abc\nip:\n  gateway: abc\n", "gateway: ", "abc", "plain")
span("span-no-final-newline", "a text without a final line break", "$.name", "name: alpha", "name: ", "alpha", "plain")
span("span-crlf", "CRLF line breaks: the span stops before the CR", "$.name", b"name: alpha\r\ncount: 3\r\n", "name: ", "alpha", "plain")
span("span-lone-cr", "a lone CR is a line break", "$.name", b"name: alpha\rcount: 3\r", "name: ", "alpha", "plain")
span("span-bom", "a leading byte order mark is counted in the offsets", "$.name", BOM + "name: alpha\n", "name: ", "alpha", "plain")
span("span-non-ascii-value", "the offsets are bytes: the value has multi-byte characters", "$.name", "name: tűrő\ncount: 3\n", "name: ", "tűrő", "plain")
span("span-after-non-ascii", "a scalar after multi-byte characters", "$.count", "name: tűrő\ncount: 3\n", "count: ", "3", "plain")

# ---- source_span: refusals ---------------------------------------------------------------------------------------------------------
BLK = "name: |\n  alpha\ncount: 3\n"
error("span-refuses-literal-block", "source_span", "a literal block scalar", "$.name", BLK, "EDIT_BLOCK_SCALAR", "edit", start_of(BLK, "name: ", "|"), "$.name")
FLD = "name: >\n  alpha\ncount: 3\n"
error("span-refuses-folded-block", "source_span", "a folded block scalar", "$.name", FLD, "EDIT_BLOCK_SCALAR", "edit", start_of(FLD, "name: ", ">"), "$.name")
ML = "name: alpha\n  beta\ncount: 3\n"
error("span-refuses-multiline-plain", "source_span", "a plain scalar that continues on the next line", "$.name", ML, "EDIT_MULTILINE_SCALAR", "edit", start_of(ML, "name: ", "alpha"), "$.name")
MQ = 'name: "alpha\n  beta"\ncount: 3\n'
error("span-refuses-multiline-quoted", "source_span", "a double-quoted scalar that continues on the next line", "$.name", MQ, "EDIT_MULTILINE_SCALAR", "edit", start_of(MQ, "name: ", '"alpha'), "$.name")
FLOW = "ip: {gateway: g1, prefix: 24}\n"
error("span-refuses-flow-context", "source_span", "a scalar inside a flow mapping", "$.ip.gateway", FLOW, "EDIT_FLOW_CONTEXT", "edit", start_of(FLOW, "gateway: ", "g1"), "$.ip.gateway")
FSEQ = "tags: [x, y]\n"
error("span-refuses-flow-set-element", "source_span", "the set of strings is not a scalar", "$.tags", FSEQ, "EDIT_NOT_A_SCALAR", "edit", start_of(FSEQ, "tags: ", "["), "$.tags")
BM = "ip:\n  gateway: g1\n  prefix: 24\n"
error("span-refuses-block-mapping", "source_span", "a composite position in block style: the anchor is its first key", "$.ip", BM, "EDIT_NOT_A_SCALAR", "edit", start_of(BM, "  ", "gateway"), "$.ip")
FM = "ip: {gateway: g1}\n"
error("span-refuses-flow-mapping", "source_span", "a composite position in flow style: the anchor is its opening brace", "$.ip", FM, "EDIT_NOT_A_SCALAR", "edit", start_of(FM, "ip: ", "{"), "$.ip")
ABS = "count: 3\n"
error("span-refuses-absent-field", "source_span", "the position has no text: an absent field is anchored at its parent", "$.name", ABS, "EDIT_NO_SOURCE_VALUE", "edit", 0, "$.name")
DUP = "name: a\nname: b\n"
error("span-refuses-a-parse-error", "source_span", "a refusal of the parse phase stays what it was", "$.name", DUP, "DUPLICATE_KEY", "parse", start_of(DUP, "name: a\n", "name"))
add("span-refuses-an-unknown-path", "source_span", "negative", "a refusal of the path phase stays what it was", "$.nope", "name: alpha\n",
    expect="{errors: [{code: UNKNOWN_FIELD, phase: path_resolve, logical_path: \"$.nope\", source_location: {state: not_applicable}}]}", status="stable")

# ---- edit: positive ----------------------------------------------------------------------------------------------------------------
edit("plain-longer", "a plain scalar replaced by a longer value; nothing else moves but the text after it", "$.name", D1, "name: ", "alpha", "{string: alphabet}", "alphabet")
edit("plain-shorter", "a plain scalar replaced by a shorter value", "$.name", D1, "name: ", "alpha", "{string: al}", "al")
edit("single-quoted-keeps-its-quotes", "a single-quoted scalar stays single-quoted; a quote in the value is doubled", "$.name", "name: 'alpha'\ncount: 3\n", "name: ", "'alpha'", "{string: \"it's\"}", "'it''s'")
edit("double-quoted-keeps-its-quotes", "a double-quoted scalar stays double-quoted; a quote and a backslash in the value are escaped", "$.name", 'name: "alpha"\ncount: 3\n', "name: ", '"alpha"', "{string: 'say \"hi\" \\'}", '"say \\"hi\\" \\\\"')
edit("trailing-comment-kept", "the spaces and the comment after the scalar are not touched", "$.name", "name: alpha   # the host\ncount: 3\n", "name: ", "alpha", "{string: beta}", "beta")
edit("comments-around-kept", "own-line comments above and between, and a trailing comment of another entry, are not touched", "$.count", "# top\nname: alpha # c\n# between\ncount: 3 # n\n", "count: ", "3", "{integer: 4}", "4")
edit("crlf-kept", "CRLF line breaks are not touched", "$.name", b"name: alpha\r\ncount: 3\r\n", "name: ", "alpha", "{string: beta}", "beta")
edit("lone-cr-kept", "lone CR line breaks are not touched", "$.name", b"name: alpha\rcount: 3\r", "name: ", "alpha", "{string: beta}", "beta")
edit("bom-kept", "a leading byte order mark is kept and counted in the offsets", "$.name", BOM + "name: alpha\n", "name: ", "alpha", "{string: beta}", "beta")
edit("non-ascii-new-value", "a value with multi-byte characters; the spans are bytes", "$.name", "name: tűrő\ncount: 3\n", "name: ", "tűrő", "{string: árvíz}", "árvíz")
edit("after-non-ascii", "a scalar after multi-byte characters", "$.count", "name: tűrő\ncount: 3\n", "count: ", "3", "{integer: 1000}", "1000")
edit("integer-negative", "an integer is written in decimal", "$.count", D1, "count: ", "3", "{integer: -5}", "-5")
edit("boolean", "a boolean is written lower-case", "$.enabled", D1, "enabled: ", "true", "{boolean: false}", "false")
edit("expanded-value-only", "in expanded form only the value: scalar changes, the Access members and the form stay", "$.name", EXP, "  value: ", "alpha", "{string: beta}", "beta")
edit("list-element-by-identity", "the metric of the element whose key is b", LEL_B_METRIC, LIST, "metric: ", "2", "{integer: 9}", "9")
edit("list-element-after-reorder", "the same edit when the elements changed places", LEL_B_METRIC, LIST_REVERSED, "metric: ", "2", "{integer: 9}", "9")
edit("equal-text-elsewhere-untouched", "the same text appears twice; only the addressed one changes", "$.ip.gateway", "name: abc\nip:\n  gateway: abc\n", "gateway: ", "abc", "{string: xyz}", "xyz")
edit("no-final-newline", "a text without a final line break stays without one", "$.name", "name: alpha", "name: ", "alpha", "{string: beta}", "beta")
edit("key-of-a-list-element", "changing a key to an unused value is an edit like any other", LEL_B_TO, LIST, "to: ", "b", "{string: c}", "c")
edit("noop-canonical", "the value does not change: the text is returned as it was", "$.enabled", D1, "enabled: ", "true", "{boolean: true}", "true")
edit("noop-keeps-a-noncanonical-spelling", "True is the same value as true: nothing is rewritten, the spelling stays", "$.enabled", "enabled: True\n", "enabled: ", "True", "{boolean: true}", "True")
edit("changed-writes-the-canonical-spelling", "a real change writes the canonical spelling of the new value", "$.enabled", "enabled: True\n", "enabled: ", "True", "{boolean: false}", "false")

# ---- edit: refusals, one case per code, and the order of the checks ---------------------------------------------------------------------
error("refuses-a-document-without-text", "edit", "the document was given as a parsed value: there is no text", "$.name", None, "EDIT_NO_SOURCE_TEXT", "edit", "{state: unavailable}", "$.name",
      value="{string: beta}", input_doc="{type: map, value: {name: alpha, count: 3}}")
error("refuses-a-composite", "edit", "the position holds a composite", "$.ip", BM, "EDIT_NOT_A_SCALAR", "edit", start_of(BM, "  ", "gateway"), "$.ip", value="{string: x}")
error("refuses-an-absent-field", "edit", "an absent field has no text: that would be an insertion", "$.name", ABS, "EDIT_NO_SOURCE_VALUE", "edit", 0, "$.name", value="{string: x}")
error("refuses-a-block-scalar", "edit", "a block scalar", "$.name", BLK, "EDIT_BLOCK_SCALAR", "edit", start_of(BLK, "name: ", "|"), "$.name", value="{string: beta}")
error("refuses-a-flow-scalar", "edit", "a scalar in a flow collection", "$.ip.gateway", FLOW, "EDIT_FLOW_CONTEXT", "edit", start_of(FLOW, "gateway: ", "g1"), "$.ip.gateway", value="{string: g2}")
error("refuses-a-multiline-scalar", "edit", "a scalar that continues on the next line", "$.name", ML, "EDIT_MULTILINE_SCALAR", "edit", start_of(ML, "name: ", "alpha"), "$.name", value="{string: beta}")
error("refuses-a-newline-in-the-value", "edit", "the new value holds a line break", "$.name", D1, NOT_REP, "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \"a\\nb\"}")
error("refuses-a-control-character-in-the-value", "edit", "the new value holds a control character", "$.name", D1, NOT_REP, "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \"a\\u0007b\"}")
DQ = 'name: "alpha"\ncount: 3\n'
error("refuses-a-control-character-even-when-quoted", "edit", "a double-quoted scalar could escape it, but the first unit writes no control character", "$.name", DQ, NOT_REP, "edit", start_of(DQ, "name: ", '"alpha"'), "$.name", value="{string: \"a\\u0007b\"}")
error("refuses-a-string-for-an-integer", "edit", "the value is not the kind of the position", "$.count", D1, "EDIT_TYPE_MISMATCH", "edit", start_of(D1, "count: ", "3"), "$.count", value="{string: three}")
error("refuses-null-for-a-string", "edit", "null where the tree has a non-nullable string", "$.name", D1, "EDIT_TYPE_MISMATCH", "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{\"null\": true}")
error("refuses-plain-for-a-colon-space", "edit", "a plain scalar cannot carry ': '", "$.name", D1, "EDIT_STYLE_CONFLICT", "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \"a: b\"}")
error("refuses-plain-for-a-leading-space", "edit", "a plain scalar cannot start with a space", "$.name", D1, "EDIT_STYLE_CONFLICT", "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \" a\"}")
error("refuses-plain-for-a-string-that-reads-as-a-boolean", "edit", "a plain string true would read back as a boolean", "$.name", D1, "EDIT_STYLE_CONFLICT", "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \"true\"}")
error("refuses-plain-for-a-string-that-reads-as-an-integer", "edit", "a plain string 123 would read back as an integer", "$.name", D1, "EDIT_STYLE_CONFLICT", "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \"123\"}")
error("refuses-plain-for-an-indicator", "edit", "a plain scalar cannot start with an indicator", "$.name", D1, "EDIT_STYLE_CONFLICT", "edit", start_of(D1, "name: ", "alpha"), "$.name", value="{string: \"-a\"}")
error("refuses-a-result-that-does-not-validate", "edit", "a changed key makes two elements share one identity", LEL_B_TO, LIST, "EDIT_INVALID_RESULT", "edit", start_of(LIST, "to: ", "b"), LEL_B_TO, value="{string: a}")
error("order-block-before-type", "edit", "a block scalar and the wrong kind of value: the block scalar is reported (check 4 before 8)", "$.name", BLK, "EDIT_BLOCK_SCALAR", "edit", start_of(BLK, "name: ", "|"), "$.name", value="{integer: 5}")
error("order-not-a-scalar-before-representable", "edit", "a composite and a value with a line break: not a scalar is reported (check 2 before 7)", "$.ip", BM, "EDIT_NOT_A_SCALAR", "edit", start_of(BM, "  ", "gateway"), "$.ip", value="{string: \"a\\nb\"}")
error("order-representable-before-type", "edit", "a line break and the wrong kind of value: not representable is reported (check 7 before 8)", "$.count", D1, NOT_REP, "edit", start_of(D1, "count: ", "3"), "$.count", value="{string: \"a\\nb\"}")
error("order-type-before-style", "edit", "the wrong kind and a style conflict: the type is reported (check 8 before 9)", "$.count", D1, "EDIT_TYPE_MISMATCH", "edit", start_of(D1, "count: ", "3"), "$.count", value="{string: \"a: b\"}")
error("order-parse-before-everything", "edit", "a duplicate key is a parse error whatever the path and the value are", "$.name", DUP, "DUPLICATE_KEY", "parse", start_of(DUP, "name: a\n", "name"), value="{string: x}")

HEADER = """# Source-preserving edit cases (ADR 0012, P3b). Written by hand from the rules of the ADR and docs/P3B-SOURCE-PRESERVING-EDIT.md; the tool
# tools/edit-vectors/author.py only does the byte arithmetic and tools/corpus_check.py re-checks it (EDIT_BYTES, ERROR_LOCATION). Neither Rust nor Go
# runs these cases yet: the kinds are NOT RUN until steps E2 (source_span) and E3 (edit). Do not edit this file by hand: change the tool and run it.
# Offsets are bytes of the original UTF-8 input, a leading BOM included; a span is half-open [start, end).
# Conventions: every field of the trees is optional and non-nullable; the document of a positive case validates against its tree; `routes` is a list whose
# element identity is the key `to`. Not covered here for lack of notation in the trees (no nullable, no default): editing a null token, a default-origin value, an
# empty value `key:`, and the validation errors of EDIT_INVALID_RESULT beyond the code. Anchors, aliases and tags are refused by the parser (UNSUPPORTED_SYNTAX is not
# a code of the corpus); their vectors are the parser's.
corpus_format: 2
suite: edit
status: provisional
defaults: {adr: ADR-0012, grammar: %s}
trees:
  doc: {name: {scalar: string}, count: {scalar: integer}, enabled: {scalar: boolean}, ip: {composite: {gateway: {scalar: string}, prefix: {scalar: integer}}}, routes: {list: {key: [to], fields: {to: {scalar: string}, metric: {scalar: integer}}}}, tags: {set: string}}
cases:
""" % GRAMMAR


def render():
    return HEADER + "\n".join(c for c in CASES if c) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = render()
    if a.check:
        with open(OUT, encoding="utf-8") as fh:
            if fh.read() != text:
                print("corpus/edit/vectors.yaml differs from what tools/edit-vectors/author.py writes")
                return 1
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %d cases" % len([c for c in CASES if c]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
