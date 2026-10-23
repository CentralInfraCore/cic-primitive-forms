#!/usr/bin/env python3
"""Structural checker for the shared conformance corpus (ADR 0005).

This tool checks the SHAPE of the fixtures: envelope, required fields, unique
ids, resolvable references, typed expectations. It deliberately contains no
recognition, validation, collapse or expand logic. A green run therefore says
nothing about Rust or Go behaviour; implementation conformance is reported as
NOT RUN until the shared runner of P1 exists.

Usage:
    corpus_check.py [--root DIR]            check, then run the seeded-fault self-test
    corpus_check.py --no-selftest [--root]  check only
    corpus_check.py --selftest              self-test only
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import tempfile

import yaml

import grammar_identity as gid

FORMAT_VERSION = 2
TYPES = ("null", "boolean", "integer", "float", "string", "list", "map")

RULES = {
    "YAML_SYNTAX": "file is not parseable YAML",
    "DUPLICATE_YAML_KEY": "a mapping repeats a key",
    "YAML_ANCHOR": "anchor or alias, which fixtures do not use (they must read the same in every loader)",
    "AMBIGUOUS_SCALAR": "unquoted scalar that YAML 1.1 and 1.2 read differently",
    "UNEXPECTED_FILE": "YAML file in the corpus that is not pins, codes, the accounting or a suite",
    "ACCOUNTING": "the accounting file does not list exactly the kinds of the corpus with their number of cases",
    "ENVELOPE": "missing, unknown or inconsistent file-level field",
    "FORMAT_VERSION": "corpus_format is not the supported version",
    "PIN_INVALID": "malformed pin (commit, sha256, repo, path, file set, order, name)",
    "PIN_DIGEST": "manifest_sha256 differs from the digest recomputed from the pinned file hashes",
    "MANIFEST_FIXTURE": "malformed grammar_manifest case input",
    "MANIFEST_CONSISTENCY": "grammar_manifest expectation disagrees with the digest recomputed from the files",
    "UPSTREAM_VERIFY": "the CI workflow does not unconditionally reproduce the grammar identity from upstream",
    "CODE_REGISTRY": "malformed code registry entry",
    "CASE_FIELD": "missing or unknown case field",
    "ID_FORMAT": "case id is not <suite>.<slug>",
    "ID_DUPLICATE": "case id is not unique across the corpus",
    "KIND_UNKNOWN": "case kind is not in the closed set",
    "INPUT_PRESENCE": "input and absent are not used as exactly one of the pair",
    "REF_DANGLING": "adr, grammar or schema pin reference does not resolve",
    "SCHEMA_FIXTURE": "malformed schema reference or synthetic fixture",
    "POLARITY": "polarity disagrees with the expectation",
    "CODE_UNKNOWN": "error code is not in the code registry",
    "CODE_STATUS": "codes_status missing or different from the registry",
    "CODE_UNUSED": "registry code that no case uses",
    "TYPED_VALUE": "malformed typed value",
    "TYPED_MISMATCH": "typed value's declared type differs from its actual YAML type",
    "EXPECT_FIELD": "missing or unknown expectation field for this kind",
    "PATH_SEGMENTS": "malformed path segment list",
    "STABILITY_VARIANTS": "path_stability variants missing, malformed or not different",
    "PATH_LOCATOR": "malformed document locator",
    "ERROR_SHAPE": "malformed error object in an expectation",
    "ERROR_PHASE": "error phase not allowed for the code or the case kind",
    "ERROR_LOCATION": "source location or path offset missing, wrong state or inconsistent with the text",
    "ERROR_ORDER": "error list not orderable or mixing phases",
    "ORIGIN_CONTEXT": "origin and attestation inconsistent with each other or with the expected context error",
    "BASIS": "conformance basis outside the closed set",
    "PHASE_UNUSED": "registry lists a phase for a code that no case exercises",
    "DIFF_INPUT": "malformed diff operands, scope, context or stability variants",
    "DIFF_EVENT": "malformed, empty or redundant diff event",
    "DIFF_ORDER": "diff events or representation entries not in the ADR 0008 order",
    "DIFF_CANONICAL": "canonical bytes malformed, not canonical or disagreeing with the events",
    "EDIT_INPUT": "malformed input, path or value of a source_span or edit case",
    "EDIT_BYTES": "an edit or source_span expectation whose spans or bytes disagree with the input (ADR 0012: untouched bytes, span bounds, boundaries, one line)",
}


class Violation:
    def __init__(self, rule, where, msg):
        self.rule, self.where, self.msg = rule, where, msg

    def __str__(self):
        return "%s %s: %s" % (self.rule, self.where, self.msg)


class LoadError(Exception):
    def __init__(self, rule, msg):
        super().__init__(msg)
        self.rule, self.msg = rule, msg


# --- strict YAML loading ---------------------------------------------------

class StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys and YAML 1.1 only spellings."""


_AMBIGUOUS = re.compile(
    r"^(?:y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF"
    r"|[-+]?0[0-7_]+|[-+]?[0-9][0-9_]*_[0-9_]*|0x[0-9a-fA-F_]+|0b[01_]+"
    r"|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?"
    r"|\d{4}-\d{2}-\d{2}.*)$"
)


def _reset_resolvers():
    StrictLoader.yaml_implicit_resolvers = {}
    add = StrictLoader.add_implicit_resolver
    add("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))
    add("tag:yaml.org,2002:null", re.compile(r"^(?:null|~|)$"), ["n", "~", ""])
    add("tag:yaml.org,2002:int", re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"), list("-+0123456789"))
    add("tag:yaml.org,2002:float",
        re.compile(r"^[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$"),
        list("-+0123456789"))
    add("!ambiguous", _AMBIGUOUS, list("yYnNoO-+0123456789"))


_reset_resolvers()


def _ambiguous(loader, node):
    raise LoadError("AMBIGUOUS_SCALAR",
                    "unquoted scalar %r reads differently in YAML 1.1 and 1.2; quote it"
                    % node.value)


def _mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise LoadError("YAML_SYNTAX", "non-string mapping key %r" % (key,))
        if key in seen:
            raise LoadError("DUPLICATE_YAML_KEY", "duplicate key %r" % key)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


def _no_anchors(self, parent, index):
    if self.check_event(yaml.events.AliasEvent):
        raise LoadError("YAML_ANCHOR", "alias %r" % self.peek_event().anchor)
    if self.peek_event().anchor is not None:
        raise LoadError("YAML_ANCHOR", "anchor %r" % self.peek_event().anchor)
    return yaml.composer.Composer.compose_node(self, parent, index)


StrictLoader.compose_node = _no_anchors
StrictLoader.add_constructor("!ambiguous", _ambiguous)
StrictLoader.add_constructor("tag:yaml.org,2002:map", _mapping)


def load_file(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        return yaml.load(text, Loader=StrictLoader)
    except LoadError:
        raise
    except yaml.YAMLError as exc:
        raise LoadError("YAML_SYNTAX", str(exc).replace("\n", " "))


def yaml_type(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    return "map"


# --- structure checks ------------------------------------------------------

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ADR_REF = re.compile(r"^ADR-(\d{4})$")
INPUT_FORMS = {
    "grammar_manifest": "files, declared",
    "path_parse": "path",
    "path_format": "segments",
    "path_resolve": "path",
    "path_of": "document, at",
    "path_stability": "variants",
    "basis": "input, origin context, optional via",
    "diff": "a|a_yaml, b|b_yaml, optional scope, schema_b",
    "diff_stability": "variants, optional scope, schema_b",  # kind -> (allowed input fields, rule on them)
    "recognize": "input|input_yaml",
    "validate": "input|absent|input_yaml",
    "roundtrip": "input|absent",
    "collapse": "input",
    "expand": "input",
    "equality": "ab",
    "source_span": "input_yaml|input_hex, path",
    "edit": "input_yaml|input_hex|input (only for EDIT_NO_SOURCE_TEXT), path, value, path_after (optional: the address after an edit that changes it)",
}
KIND_FIELDS = {
    "grammar_manifest": {"files", "declared"},
    "path_parse": {"path"},
    "path_format": {"segments"},
    "path_resolve": {"path"},
    "path_of": {"document", "at"},
    "path_stability": {"variants"},
    "diff": {"a", "a_yaml", "b", "b_yaml", "scope", "schema_b"},
    "diff_stability": {"variants", "scope", "schema_b"},
    "recognize": {"input", "input_yaml"},
    "validate": {"input", "absent", "input_yaml", "origin", "attestation", "attested_positions"},
    "basis": {"input", "origin", "attestation", "attested_positions", "via"},
    "roundtrip": {"input", "absent"},
    "collapse": {"input", "authored_expanded", "basis"},
    "expand": {"input"},
    "equality": {"a", "b"},
    "source_span": {"input_yaml", "input_hex", "path"},
    "edit": {"input_yaml", "input_hex", "input", "path", "value", "path_after"},
}
COMMON_FIELDS = {"id", "kind", "polarity", "description", "adr", "grammar",
                 "schema", "codes_status", "expect"}
REQUIRED_COMMON = ("id", "kind", "polarity", "schema", "expect")
PATH_SUCCESS = {  # kind -> expectation key of the success outcome
    "path_parse": "segments", "path_format": "text", "path_resolve": "target",
    "path_of": "path", "path_stability": "path",
}
TARGETS = ("root", "scalar", "composite", "list", "item", "set", "opaque")
EXPECT_KEYS = {
    "grammar_manifest": ({"valid", "errors"}, None),
    "path_parse": ({"segments", "errors"}, None),
    "path_format": ({"text", "errors"}, None),
    "path_resolve": ({"target", "primitive_position", "errors"}, None),
    "path_of": ({"path", "errors"}, None),
    "path_stability": ({"path", "errors"}, None),
    "diff": ({"changes", "representation", "canonical", "errors"}, None),
    "diff_stability": ({"changes", "representation", "canonical", "errors"}, None),
    "recognize": ({"class", "elements", "errors", "opaque_value"}, None),
    "validate": ({"valid", "errors", "basis", "stays_absent", "default_candidate",
                  "kind", "is_inherit", "preserved", "upstream_conflict"}, None),
    "roundtrip": ({"expanded", "collapsed", "fields"}, None),
    "collapse": ({"collapse", "fields"}, {"collapse"}),
    "expand": ({"fields"}, {"fields"}),
    "basis": ({"bases", "errors"}, None),
    "equality": ({"equal"}, {"equal"}),
    "source_span": ({"span", "errors"}, None),
    "edit": ({"output_yaml", "output_hex", "span_before", "span_after", "errors"}, None),
}
PRESENCE = ("mandatory", "optional")
SHAPE_KEYS = {"type", "presence", "nullable", "default", "min_items", "fields", "elements"}
SLOTS = ("inherit_key", "inherit_policy", "access_modify", "access_node")


def valid_position(p):
    if p in ("scalar", "opaque"):
        return True
    if isinstance(p, dict) and len(p) == 1:
        (k, v), = p.items()
        if k == "structured":
            return isinstance(v, list) and all(isinstance(x, str) and x for x in v)
        if k == "list_of":
            return valid_record(v)
    return False


def valid_record(r):
    """A list element is a structural record, not a position (ADR 0002); its fields are."""
    if isinstance(r, dict) and set(r) == {"record"} and isinstance(r["record"], dict) and r["record"]:
        return all(valid_position(p) for p in r["record"].values())
    return False


def collect_codes(expect):
    """Codes live only at expect.errors[*].code.

    Typed values (`fields`, `preserved`, `expanded`, `collapsed`) are data and are
    never searched: a map that happens to hold `code` or `error` keys is not a code.
    """
    errs = expect.get("errors")
    if not isinstance(errs, list):
        return []
    return [e["code"] for e in errs if isinstance(e, dict) and "code" in e]


PHASES = ("parse", "recognize", "validate", "path_parse", "path_format", "path_resolve", "path_of", "diff", "edit")
ANCHORS = ("second_key", "key", "value", "value_or_key", "mapping", "sequence", "parent", "path_text", "none")
KIND_PHASES = {
    "recognize": {"parse", "recognize", "validate"},
    "validate": {"parse", "recognize", "validate"},
    "path_parse": {"path_parse"},
    "path_format": {"path_format"},
    "path_resolve": {"path_parse", "path_resolve"},
    "path_of": {"path_of"},
    "path_stability": {"path_of"},
    "diff": {"recognize", "validate", "path_parse", "path_resolve", "path_of", "diff"},
    "diff_stability": {"recognize", "validate", "path_parse", "path_resolve", "path_of", "diff"},
    "basis": {"recognize", "validate", "path_parse", "path_resolve"},
    "grammar_manifest": {"validate"},
    # an earlier phase may refuse the document or the path; the phase edit is the last (ADR 0012, section 4)
    "source_span": {"parse", "path_parse", "path_resolve", "edit"},
    "edit": {"parse", "recognize", "validate", "path_parse", "path_resolve", "edit"},
}
EDIT_KINDS = ("source_span", "edit")
DIFF_KINDS = ("diff", "diff_stability")
SINGLE_ERROR_PHASES = {"parse", "path_parse", "path_format", "path_resolve", "path_of", "edit"}
ERROR_KEYS = {"code", "phase", "logical_path", "source_location", "path_offset", "operand"}


_NAME_ESC = set("\\.[]{}=,")


def parse_logical_path(text):
    """Canonical ADR 0006 path text -> typed segments, or ValueError.

    Used to check the *structure* of an expectation (is this a canonical path, is it a
    segment prefix of that one, in which order do these come). It knows nothing of
    schemas and decides nothing about a document.
    """
    if not isinstance(text, str) or not text.startswith("$"):
        raise ValueError("no root marker")
    i, segs = 1, []

    def name(i, stop):
        out = []
        while i < len(text) and text[i] not in stop:
            ch = text[i]
            if ch == "\\":
                if i + 1 >= len(text) or text[i + 1] not in _NAME_ESC:
                    raise ValueError("bad escape at %d" % i)
                out.append(text[i + 1])
                i += 2
                continue
            if ch in _NAME_ESC or ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("unescaped %r at %d" % (ch, i))
            out.append(ch)
            i += 1
        if not out:
            raise ValueError("empty name at %d" % i)
        return "".join(out), i

    while i < len(text):
        if text[i] == ".":
            n, i = name(i + 1, ".[")
            segs.append(("field", n))
        elif text[i] == "[" and segs and segs[-1][0] == "field" and text[i + 1:i + 2] == "{":
            i += 2
            comps = []
            while True:
                n, i = name(i, "=")
                i += 1
                if i < len(text) and text[i] == '"':
                    i += 1
                    val = []
                    while True:
                        if i >= len(text):
                            raise ValueError("unterminated string")
                        ch = text[i]
                        if ch == '"':
                            i += 1
                            break
                        if ch == "\\":
                            d = text[i + 1:i + 2]
                            esc = {'"': '"', "\\": "\\", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
                            if d in esc:
                                val.append(esc[d])
                                i += 2
                            elif d == "u" and re.fullmatch(r"00[01][0-9a-f]", text[i + 2:i + 6]) \
                                    and int(text[i + 2:i + 6], 16) not in (8, 9, 10, 12, 13):
                                val.append(chr(int(text[i + 2:i + 6], 16)))
                                i += 6
                            else:
                                raise ValueError("non-canonical escape at %d" % i)
                            continue
                        if ord(ch) < 0x20:
                            raise ValueError("control character in string")
                        val.append(ch)
                        i += 1
                    comps.append((n, "string", "".join(val)))
                else:
                    m = re.match(r"-?(?:0|[1-9][0-9]*)", text[i:])
                    if not m or m.group() == "-0":
                        raise ValueError("bad integer at %d" % i)
                    v = int(m.group())
                    if not -2 ** 63 <= v < 2 ** 63:
                        raise ValueError("integer out of range")
                    i += len(m.group())
                    comps.append((n, "integer", v))
                if text[i:i + 1] == ",":
                    i += 1
                    continue
                if text[i:i + 2] == "}]":
                    i += 2
                    break
                raise ValueError("bad selector at %d" % i)
            if len({c[0] for c in comps}) != len(comps):
                raise ValueError("duplicate component")
            segs.append(("item", tuple(comps)))
        else:
            raise ValueError("unexpected %r at %d" % (text[i:i + 1], i))
    return segs


def path_order_key(segs):
    """Segment-wise order of ADR 0007: prefix first, names by UTF-8 bytes, integer keys numerically."""
    key = []
    for kind, v in segs:
        if kind == "field":
            key.append((0, v.encode("utf-8")))
        else:
            key.append((1, tuple((n.encode("utf-8"), (0, x) if k == "integer" else (1, x.encode("utf-8")))
                                 for n, k, x in v)))
    return key


def location_problem(text, loc):
    """Do line, column (code points, 1-based) and offset (UTF-8 bytes, 0-based) agree on this text?"""
    data = text.encode("utf-8")
    off = loc["offset"]
    if off > len(data):
        return "offset %d is beyond the %d bytes of the text" % (off, len(data))
    if off < len(data) and (data[off] & 0xC0) == 0x80:
        return "offset %d is inside a multi-byte character" % off
    if off < len(data) and data[off:off + 1] == b"\n" and data[:off].endswith(b"\r"):
        return "offset %d is inside a CRLF line break" % off
    prefix = data[:off].decode("utf-8")
    if prefix.startswith("\ufeff"):
        prefix = prefix[1:]  # a leading BOM is no column
    line, col, i = 1, 1, 0
    while i < len(prefix):
        ch = prefix[i]
        if ch == "\r" and i + 1 < len(prefix) and prefix[i + 1] == "\n":
            i += 1
            ch = "\n"
        if ch in "\r\n":
            line, col = line + 1, 1
        else:
            col += 1
        i += 1
    if (loc["line"], loc["column"]) != (line, col):
        return "line %d column %d, but offset %d is line %d column %d" % (loc["line"], loc["column"], off, line, col)
    return None


HEXBYTES = re.compile(r"^(?:[0-9a-f]{2})+$")
SPAN_STYLES = ("plain", "single_quoted", "double_quoted")
EDIT_VALUE_TYPES = {"string": str, "integer": int, "boolean": bool}


def case_bytes(case):
    """The source of a source_span or edit case as bytes (text or hex), or None."""
    if isinstance(case.get("input_yaml"), str):
        return case["input_yaml"].encode("utf-8")
    if isinstance(case.get("input_hex"), str) and HEXBYTES.match(case["input_hex"]):
        return bytes.fromhex(case["input_hex"])
    return None


def case_text(case):
    """The source as text for the position check of an error location; None when it is not valid UTF-8."""
    b = case_bytes(case)
    try:
        return None if b is None else b.decode("utf-8")
    except UnicodeDecodeError:
        return None


def boundary_problem(data, off, what):
    """An offset is a boundary of the text: inside the bytes, not inside a multi-byte character, not between the CR and the LF of a CRLF."""
    if not isinstance(off, int) or isinstance(off, bool) or off < 0 or off > len(data):
        return "%s %r is not an offset inside the %d bytes" % (what, off, len(data))
    if off < len(data) and (data[off] & 0xC0) == 0x80:
        return "%s %d is inside a multi-byte character" % (what, off)
    if off < len(data) and data[off:off + 1] == b"\n" and data[:off].endswith(b"\r"):
        return "%s %d is inside a CRLF line break" % (what, off)
    return None


def span_problem(data, span, what):
    """A span {start, end}: half-open byte offsets on boundaries, the text of one line (no line break inside)."""
    if not isinstance(span, dict) or not {"start", "end"} <= set(span):
        return "%s must hold start and end" % what
    for k in ("start", "end"):
        p = boundary_problem(data, span[k], "%s.%s" % (what, k))
        if p:
            return p
    if span["start"] >= span["end"]:
        return "%s is empty or reversed: a scalar span has at least one byte" % what
    if re.search(rb"[\r\n]", data[span["start"]:span["end"]]):
        return "%s holds a line break: this unit edits single-line scalars" % what
    return None


def check_edit_inputs(kind, case, where, vs):
    forms = [f for f in ("input_yaml", "input_hex", "input") if f in case]
    allowed = 3 if kind == "edit" else 2
    if len(forms) != 1 or (forms[0] == "input" and kind != "edit"):
        vs.append(Violation("EDIT_INPUT", where, "exactly one of input_yaml, input_hex%s" % (" and (edit only) input" if kind == "edit" else "")))
    if "input_yaml" in case and not isinstance(case["input_yaml"], str):
        vs.append(Violation("EDIT_INPUT", where, "input_yaml must be a string"))
    if "input_hex" in case:
        h = case["input_hex"]
        if not isinstance(h, str) or not HEXBYTES.match(h):
            vs.append(Violation("EDIT_INPUT", where, "input_hex is lower-case hex of whole bytes"))
    if "input" in case:
        check_typed(case["input"], where + ".input", vs)   # a parsed document: the case where there is no source text
    p = case.get("path")
    if not isinstance(p, str):
        vs.append(Violation("EDIT_INPUT", where, "path must be a canonical logical path text"))
    else:
        try:
            parse_logical_path(p)
        except ValueError as exc:
            vs.append(Violation("EDIT_INPUT", where, "path is not a canonical logical path: %s" % exc))
    if "path_after" in case:   # where the scalar is addressed after an edit that changes its address (the key of a list element): the address the runners read back at
        pa = case["path_after"]
        if not isinstance(pa, str):
            vs.append(Violation("EDIT_INPUT", where, "path_after must be a canonical logical path text"))
        else:
            try:
                parse_logical_path(pa)
            except ValueError as exc:
                vs.append(Violation("EDIT_INPUT", where, "path_after is not a canonical logical path: %s" % exc))
            else:
                if pa == p:
                    vs.append(Violation("EDIT_INPUT", where, "path_after is present only where the edit changes the address (it differs from path)"))
    if kind == "edit":
        v = case.get("value")
        ok = isinstance(v, dict) and len(v) == 1
        if ok:
            (t, x), = v.items()
            ok = (t == "null" and x is True) or (t in EDIT_VALUE_TYPES and type(x) is EDIT_VALUE_TYPES[t])
        if not ok:
            vs.append(Violation("EDIT_INPUT", where, "value is {string: s}, {integer: n}, {boolean: b} or {null: true}"))


def check_edit_expect(kind, case, expect, where, vs):
    if ("errors" in expect) == any(k in expect for k in ("span", "output_yaml", "output_hex", "span_before", "span_after")):
        vs.append(Violation("EXPECT_FIELD", where, "exactly one of the success keys and errors"))
        return
    if "errors" in expect:
        if kind == "edit" and "input" in case:
            if [e.get("code") for e in expect["errors"] if isinstance(e, dict)] != ["EDIT_NO_SOURCE_TEXT"]:
                vs.append(Violation("EDIT_INPUT", where, "a document without source text is refused with EDIT_NO_SOURCE_TEXT only"))
        elif any(isinstance(e, dict) and e.get("code") == "EDIT_NO_SOURCE_TEXT" for e in expect["errors"]):
            vs.append(Violation("EDIT_INPUT", where, "EDIT_NO_SOURCE_TEXT belongs to a case whose input is a parsed document"))
        return
    data = case_bytes(case)
    if data is None:
        vs.append(Violation("EDIT_BYTES", where, "a success needs the source text of the case"))
        return
    if kind == "source_span":
        sp = expect["span"]
        if not isinstance(sp, dict) or set(sp) != {"start", "end", "style"} or sp.get("style") not in SPAN_STYLES:
            vs.append(Violation("EDIT_BYTES", where, "span is {start, end, style} with style one of %s" % (SPAN_STYLES,)))
            return
        p = span_problem(data, sp, where + ".span")
        if p:
            vs.append(Violation("EDIT_BYTES", where, p))
        return
    # edit: the edited bytes, and the two spans, must obey guarantee 1 of ADR 0012 (the untouched bytes) by arithmetic alone
    if sum(k in expect for k in ("output_yaml", "output_hex")) != 1 or not {"span_before", "span_after"} <= set(expect):
        vs.append(Violation("EDIT_BYTES", where, "an edit success holds exactly one of output_yaml/output_hex, and span_before and span_after"))
        return
    if "output_yaml" in expect:
        if not isinstance(expect["output_yaml"], str):
            vs.append(Violation("EDIT_BYTES", where, "output_yaml must be a string"))
            return
        out = expect["output_yaml"].encode("utf-8")
    else:
        h = expect["output_hex"]
        if not isinstance(h, str) or not HEXBYTES.match(h):
            vs.append(Violation("EDIT_BYTES", where, "output_hex is lower-case hex of whole bytes"))
            return
        out = bytes.fromhex(h)
    before, after = expect["span_before"], expect["span_after"]
    p = span_problem(data, before, where + ".span_before") or span_problem(out, after, where + ".span_after")
    if p:
        vs.append(Violation("EDIT_BYTES", where, p))
        return
    if before["start"] != after["start"]:
        vs.append(Violation("EDIT_BYTES", where, "the span starts at the same byte before and after"))
    tail = len(data) - before["end"]
    if out[:before["start"]] != data[:before["start"]]:
        vs.append(Violation("EDIT_BYTES", where, "the bytes before the span are not the original's"))
    if out[len(out) - tail:] != data[before["end"]:] or after["end"] != len(out) - tail:
        vs.append(Violation("EDIT_BYTES", where, "the bytes after the span are not the original's, or span_after does not end where they start"))
    if "value" in case and (data == out) != (data[before["start"]:before["end"]] == out[after["start"]:after["end"]]):
        vs.append(Violation("EDIT_BYTES", where, "inconsistent no-op"))


def check_errors(kind, case, expect, codes, where, vs, used_pairs):
    errs = expect.get("errors")
    w = where + ".errors"
    if not isinstance(errs, list) or not errs:
        vs.append(Violation("ERROR_SHAPE", w, "errors must be a non-empty list"))
        return
    phases = set()
    for i, e in enumerate(errs):
        ew = "%s[%d]" % (w, i)
        if not isinstance(e, dict) or not {"code", "phase", "source_location"} <= set(e) or not set(e) <= ERROR_KEYS:
            vs.append(Violation("ERROR_SHAPE", ew, "an error holds code, phase, source_location and optionally "
                                "logical_path, path_offset (message is not a conformance criterion)"))
            continue
        code, phase = e["code"], e["phase"]
        phases.add(phase)
        if phase not in PHASES or phase not in KIND_PHASES[kind]:
            vs.append(Violation("ERROR_PHASE", ew, "phase %r is not allowed in a %s case" % (phase, kind)))
            continue
        if code in codes and phase not in codes[code]["phases"]:
            vs.append(Violation("ERROR_PHASE", ew, "%s is not reported in phase %s (registry: %s)"
                                % (code, phase, sorted(codes[code]["phases"]))))
        used_pairs.add((code, phase))
        # source location: exactly one of three states, fixed by the phase and the input form
        sl = e["source_location"]
        src_text = case_text(case)
        has_text = "input_yaml" in case or "input_hex" in case
        if phase in ("path_parse", "path_format", "path_resolve", "diff") or kind in ("path_stability", "diff_stability"):
            want = "not_applicable"
        elif phase == "path_of":
            want = "unavailable"
        elif code in codes and codes[code]["phases"].get(phase) == "none":
            want = "not_applicable"
        elif phase == "parse":
            want = "location"
            if not has_text:
                vs.append(Violation("ERROR_LOCATION", ew, "a parse error needs input_yaml"))
        else:
            want = "location" if has_text else "unavailable"
        if want == "location":
            ok = (isinstance(sl, dict) and set(sl) == {"line", "column", "offset"}
                  and all(yaml_type(sl[k]) == "integer" for k in sl)
                  and sl["line"] >= 1 and sl["column"] >= 1 and sl["offset"] >= 0)
            if not ok:
                vs.append(Violation("ERROR_LOCATION", ew, "source_location must be {line>=1, column>=1, offset>=0}"))
            elif has_text and isinstance(src_text, str):
                p = location_problem(src_text, sl)
                if p:
                    vs.append(Violation("ERROR_LOCATION", ew, p))
        elif sl != {"state": want}:
            vs.append(Violation("ERROR_LOCATION", ew, "source_location must be {state: %s} here (never null, never absent)" % want))
        # operand: which view of a two-operand operation the error is about
        if "operand" in e:
            if kind not in DIFF_KINDS or e["operand"] not in ("a", "b") or phase in ("path_parse", "path_resolve"):
                vs.append(Violation("ERROR_SHAPE", ew, "operand a|b belongs to errors about one view of a diff"))
        elif kind in DIFF_KINDS and phase in ("recognize", "validate", "path_of"):
            vs.append(Violation("ERROR_SHAPE", ew, "an error found while reading one view of a diff names its operand"))
        # path offset: the text of the path only, never a YAML coordinate
        if phase == "path_parse":
            po = e.get("path_offset")
            text = case.get("path")
            if yaml_type(po) != "integer" or po < 0 or (isinstance(text, str) and po > len(text)):
                vs.append(Violation("ERROR_LOCATION", ew, "path_offset must be an integer in 0..len(path) in code points"))
        elif "path_offset" in e:
            vs.append(Violation("ERROR_LOCATION", ew, "path_offset belongs to path_parse errors only"))
        # logical path: canonical text, only where it is determinable
        lp = e.get("logical_path")
        if "logical_path" in e:
            if phase in ("parse", "path_parse", "path_format"):
                vs.append(Violation("ERROR_SHAPE", ew, "no logical_path in phase %s: it cannot be determined" % phase))
            else:
                try:
                    lsegs = parse_logical_path(lp)
                except ValueError as exc:
                    vs.append(Violation("ERROR_SHAPE", ew, "logical_path is not a canonical path: %s" % exc))
                else:
                    if phase == "path_resolve" and (isinstance(case.get("path"), str) or isinstance(case.get("scope"), list)):
                        texts = [case["path"]] if isinstance(case.get("path"), str) else list(case["scope"])
                        ok = False
                        for t in texts:
                            try:
                                cs = parse_logical_path(t)
                            except ValueError:
                                continue
                            ok = ok or (bool(lsegs) and cs[:len(lsegs)] == lsegs)
                        csegs = [] if ok else None
                        if csegs is None:
                            vs.append(Violation("ERROR_SHAPE", ew,
                                                "logical_path must be a whole-segment prefix of the resolved path"))
        elif phase == "path_resolve":
            vs.append(Violation("ERROR_SHAPE", ew, "a resolution error names the logical path it stopped at"))
    if len(phases) > 1:
        vs.append(Violation("ERROR_ORDER", w, "one response holds errors of one phase only (phases run in order and stop)"))
    if len(errs) > 1:
        if phases & SINGLE_ERROR_PHASES:
            vs.append(Violation("ERROR_ORDER", w, "phase %s reports exactly one error" % sorted(phases & SINGLE_ERROR_PHASES)))
        keys = [(e.get("logical_path"), e.get("code")) for e in errs if isinstance(e, dict)]
        if any(k[0] is None for k in keys) or len(set(keys)) != len(keys):
            vs.append(Violation("ERROR_ORDER", w, "several errors need a logical_path each and distinct (path, code) pairs"))
        else:
            try:
                order = [(path_order_key(parse_logical_path(p)), c.encode("utf-8")) for p, c in keys]
            except ValueError:
                order = None  # a malformed logical_path is reported as ERROR_SHAPE above
            if order is not None and order != sorted(order):
                vs.append(Violation("ERROR_ORDER", w,
                                    "errors are not in the order of ADR 0007 (logical path segment-wise, then code)"))


def check_typed(tv, where, vs):
    """tv must be {type: absent} or {type: T, value: V} with V's real type == T."""
    if not isinstance(tv, dict) or "type" not in tv:
        vs.append(Violation("TYPED_VALUE", where, "expected a typed value {type, value}"))
        return
    t = tv["type"]
    if t == "absent":
        if set(tv) != {"type"}:
            vs.append(Violation("TYPED_VALUE", where, "type absent carries no value key"))
        return
    if t not in TYPES:
        vs.append(Violation("TYPED_VALUE", where, "unknown type %r" % (t,)))
        return
    if set(tv) != {"type", "value"}:
        vs.append(Violation("TYPED_VALUE", where, "typed value needs exactly type and value"))
        return
    actual = yaml_type(tv["value"])
    if actual != t:
        vs.append(Violation("TYPED_MISMATCH", where,
                            "declared %s but the value is %s" % (t, actual)))


def check_typed_fields(m, where, vs):
    if not isinstance(m, dict) or not m:
        vs.append(Violation("TYPED_VALUE", where, "expected a non-empty map of typed values"))
        return
    for k, tv in m.items():
        check_typed(tv, "%s.%s" % (where, k), vs)


CLASSES = ("short", "expanded", "error")


def check_recognize_expect(case, expect, where, vs):
    if "class" in expect and expect["class"] not in CLASSES:
        vs.append(Violation("EXPECT_FIELD", where + ".class", "class must be one of %s" % (CLASSES,)))
    if "elements" not in expect:
        return
    els = expect["elements"]
    w = where + ".elements"
    if not isinstance(els, list) or not els:
        vs.append(Violation("EXPECT_FIELD", w, "elements must be a non-empty list"))
        return
    pos = case.get("schema", {}).get("synthetic", {}).get("position") if isinstance(case.get("schema"), dict) else None
    fields = None
    if isinstance(pos, dict) and "list_of" in pos and valid_record(pos["list_of"]):
        fields = set(pos["list_of"]["record"])
    else:
        vs.append(Violation("EXPECT_FIELD", w, "elements needs a list_of record position"))
    if isinstance(case.get("input"), list) and len(case["input"]) != len(els):
        vs.append(Violation("EXPECT_FIELD", w, "%d elements expected for %d input elements"
                            % (len(els), len(case["input"]))))
    for i, el in enumerate(els):
        if not isinstance(el, dict) or not el or any(c not in CLASSES for c in el.values()):
            vs.append(Violation("EXPECT_FIELD", "%s[%d]" % (w, i), "must map field names to one of %s" % (CLASSES,)))
        elif fields is not None and set(el) != fields:
            vs.append(Violation("EXPECT_FIELD", "%s[%d]" % (w, i), "fields %s differ from the record %s"
                                % (sorted(el), sorted(fields))))


def native_value(tv):
    return tv["value"]


def native_event(e):
    out = {}
    for k, v in e.items():
        if k in ("previous", "next", "added", "removed") and isinstance(v, dict) and "type" in v:
            out[k] = native_value(v)
        elif k == "changed":
            out[k] = {f: {"previous": native_value(x["previous"]), "next": native_value(x["next"])} for f, x in v.items()}
        else:
            out[k] = v
    return out


def _no_dup_pairs(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate key %r" % k)
        d[k] = v
    return d


INT64 = (-2 ** 63, 2 ** 63 - 1)


def number_problems(v):
    """Floats and integers outside signed 64 bits, anywhere in a value (ADR 0008)."""
    out = []
    if isinstance(v, float):
        out.append("float %r" % v)
    elif isinstance(v, int) and not isinstance(v, bool) and not INT64[0] <= v <= INT64[1]:
        out.append("integer %d outside signed 64 bits" % v)
    elif isinstance(v, list):
        for x in v:
            out += number_problems(x)
    elif isinstance(v, dict):
        for x in v.values():
            out += number_problems(x)
    return out


def set_element_key(v):
    """ADR 0008 element order: null, booleans, integers, then strings by UTF-8 bytes."""
    if v is None:
        return (0, 0)
    if isinstance(v, bool):
        return (1, int(v))
    if isinstance(v, int):
        return (2, v)
    if isinstance(v, str):
        return (3, v.encode("utf-8"))
    raise TypeError("set elements are scalars")


def check_diff_expect(expect, where, vs):
    if "errors" in expect:
        if set(expect) != {"errors"}:
            vs.append(Violation("EXPECT_FIELD", where, "an error expectation carries nothing else"))
        return
    if set(expect) != {"changes", "representation", "canonical"}:
        vs.append(Violation("EXPECT_FIELD", where, "a diff expectation holds changes, representation and canonical (or errors)"))
        return
    ch, rp = expect["changes"], expect["representation"]
    if not isinstance(ch, list) or not isinstance(rp, list):
        vs.append(Violation("DIFF_EVENT", where, "changes and representation are lists"))
        return
    keyed, paths = [], []
    for i, e in enumerate(ch):
        w = "%s.changes[%d]" % (where, i)
        if not isinstance(e, dict) or e.get("kind") not in EVENT_FIELDS or set(e) != EVENT_FIELDS[e["kind"]][0]:
            vs.append(Violation("DIFF_EVENT", w, "event kind in %s with exactly its fields" % sorted(EVENT_FIELDS)))
            continue
        try:
            segs = parse_logical_path(e["path"])
        except ValueError as exc:
            vs.append(Violation("DIFF_EVENT", w, "path is not a canonical logical path: %s" % exc))
            continue
        k = e["kind"]
        if k in ("added", "removed"):
            tv = e["next" if k == "added" else "previous"]
            check_typed(tv, w, vs)
            if isinstance(tv, dict) and tv.get("type") not in ("map",):
                vs.append(Violation("DIFF_EVENT", w, "an appearing or disappearing node carries its expanded fragment, a map"))
        elif k == "value_change":
            check_typed(e["previous"], w + ".previous", vs)
            check_typed(e["next"], w + ".next", vs)
            if strict_eq(e["previous"], e["next"]):
                vs.append(Violation("DIFF_EVENT", w, "previous and next are identical, the event describes no change"))
        elif k == "policy_declaration_change":
            chg = e["changed"]
            if not isinstance(chg, dict) or not chg or not set(chg) <= set(POLICY_FIELDS):
                vs.append(Violation("DIFF_EVENT", w, "changed maps a non-empty subset of %s" % (POLICY_FIELDS,)))
            else:
                for f, x in chg.items():
                    if not isinstance(x, dict) or set(x) != {"previous", "next"}:
                        vs.append(Violation("DIFF_EVENT", "%s.changed.%s" % (w, f), "previous and next"))
                        continue
                    check_typed(x["previous"], "%s.changed.%s.previous" % (w, f), vs)
                    check_typed(x["next"], "%s.changed.%s.next" % (w, f), vs)
                    if strict_eq(x["previous"], x["next"]):
                        vs.append(Violation("DIFF_EVENT", "%s.changed.%s" % (w, f), "unchanged field listed as changed"))
        else:  # set_change
            ok = True
            for side in ("added", "removed"):
                tv = e[side]
                check_typed(tv, "%s.%s" % (w, side), vs)
                if not (isinstance(tv, dict) and tv.get("type") == "list"):
                    ok = False
            if ok:
                a, r = e["added"]["value"], e["removed"]["value"]
                if not a and not r:
                    vs.append(Violation("DIFF_EVENT", w, "a set_change with nothing added or removed describes no change"))
                if any(strict_eq(x, y) for x in a for y in r):
                    vs.append(Violation("DIFF_EVENT", w, "an element is both added and removed"))
                for lst in (a, r):
                    if any(strict_eq(lst[i], lst[j]) for i in range(len(lst)) for j in range(i + 1, len(lst))):
                        vs.append(Violation("DIFF_EVENT", w, "a set lists an element twice"))
        for p in number_problems(e):
            vs.append(Violation("DIFF_EVENT", w, "%s: no canonical form (ADR 0008)" % p))
        if k == "set_change" and isinstance(e.get("added"), dict) and isinstance(e.get("removed"), dict):
            for side in ("added", "removed"):
                lst = e[side].get("value")
                if isinstance(lst, list):
                    try:
                        if [set_element_key(x) for x in lst] != sorted(set_element_key(x) for x in lst):
                            vs.append(Violation("DIFF_ORDER", "%s.%s" % (w, side),
                                                "set elements are sorted: null, booleans, integers, strings by UTF-8 bytes"))
                    except TypeError:
                        vs.append(Violation("DIFF_EVENT", "%s.%s" % (w, side), "set elements are scalars"))
        keyed.append((path_order_key(segs), k.encode("utf-8"), segs, k))
    # one event for an appearing or disappearing node, none below it
    for pk, kk, segs, k in keyed:
        if k in ("added", "removed"):
            for pk2, kk2, segs2, k2 in keyed:
                if len(segs2) > len(segs) and segs2[:len(segs)] == segs:
                    vs.append(Violation("DIFF_EVENT", where, "event below %s node %s is redundant" % (k, segs)))
    order = [(a, b) for a, b, _, _ in keyed]
    if len(set(map(repr, order))) != len(order) or order != sorted(order):
        vs.append(Violation("DIFF_ORDER", where + ".changes", "events are ordered by logical path (segment-wise), then kind bytes, one per pair"))
    rkeys = []
    for i, r in enumerate(rp):
        w = "%s.representation[%d]" % (where, i)
        if not (isinstance(r, dict) and set(r) == {"kind", "path", "previous", "next"} and r["kind"] == "representation_change"
                and r["previous"] in ("short", "expanded") and r["next"] in ("short", "expanded") and r["previous"] != r["next"]):
            vs.append(Violation("DIFF_EVENT", w, "representation_change between short and expanded, different forms"))
            continue
        try:
            rkeys.append(path_order_key(parse_logical_path(r["path"])))
        except ValueError as exc:
            vs.append(Violation("DIFF_EVENT", w, "path is not a canonical logical path: %s" % exc))
    if len(set(map(repr, rkeys))) != len(rkeys) or rkeys != sorted(rkeys):
        vs.append(Violation("DIFF_ORDER", where + ".representation", "entries are ordered by logical path, one per path"))
    # canonical bytes: well formed, canonical, and the same content as the structured expectation
    text = expect["canonical"]
    if not isinstance(text, str):
        vs.append(Violation("DIFF_CANONICAL", where, "canonical is a string"))
        return
    try:
        parsed = json.loads(text, object_pairs_hook=_no_dup_pairs, parse_float=lambda s: (_ for _ in ()).throw(ValueError("float")))
    except ValueError as exc:
        vs.append(Violation("DIFF_CANONICAL", where, "canonical is not strict JSON without floats: %s" % exc))
        return
    for p in number_problems(parsed):
        vs.append(Violation("DIFF_CANONICAL", where, "%s: no canonical form (ADR 0008)" % p))
    again = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if again != text:
        vs.append(Violation("DIFF_CANONICAL", where, "not the canonical writing (sorted keys, no whitespace, UTF-8, short escapes)"))
    try:
        want = {"version": 1, "changes": [native_event(e) for e in ch], "representation": rp}
    except (KeyError, TypeError):
        return  # malformed events are reported above
    if not strict_eq(parsed, want):
        vs.append(Violation("DIFF_CANONICAL", where, "canonical bytes disagree with the structured events"))


def check_path_expect(kind, case, expect, where, vs):
    ok_key = PATH_SUCCESS[kind]
    if (ok_key in expect) == ("errors" in expect):
        vs.append(Violation("EXPECT_FIELD", where, "exactly one of %s and errors" % ok_key))
        return
    if "errors" in expect:
        if set(expect) != {"errors"}:
            vs.append(Violation("EXPECT_FIELD", where, "an error expectation carries nothing else"))
        return
    if kind == "path_parse":
        check_segments(expect["segments"], where + ".segments", vs, True)
    elif kind == "path_resolve":
        if expect.get("target") not in TARGETS or not isinstance(expect.get("primitive_position"), bool):
            vs.append(Violation("EXPECT_FIELD", where, "target in %s and boolean primitive_position" % (TARGETS,)))
        elif expect["primitive_position"] != (expect["target"] not in ("root", "item")):
            vs.append(Violation("EXPECT_FIELD", where, "only root and item are not primitive positions"))
    elif not isinstance(expect[ok_key], str):
        vs.append(Violation("EXPECT_FIELD", where, "%s must be a string" % ok_key))
    if kind != "path_resolve" and set(expect) != {ok_key}:
        vs.append(Violation("EXPECT_FIELD", where, "only %s is allowed" % ok_key))


def check_expect_types(kind, expect, where, vs):
    if kind == "validate" and "preserved" in expect:
        check_typed(expect["preserved"], where + ".preserved", vs)
    if kind == "roundtrip":
        for side in ("expanded", "collapsed"):
            if side in expect:
                check_typed(expect[side], "%s.%s" % (where, side), vs)
        if "fields" in expect:
            check_typed_fields(expect["fields"], where + ".fields", vs)
    if kind in ("collapse", "expand") and "fields" in expect:
        check_typed_fields(expect["fields"], where + ".fields", vs)


SCALAR_TYPES = ("string", "integer", "boolean", "number", "bytes")


def tree_problems(tree):
    """Problems of a synthetic schema tree (a notation, not a schema)."""
    out = []

    def fields(f, ctx):
        if not isinstance(f, dict) or not f:
            out.append("%s: fields must be a non-empty map" % ctx)
            return {}
        for n, node in f.items():
            walk(node, "%s.%s" % (ctx, n))
        return f

    def keyed(spec, ctx):
        fs = fields(spec.get("fields"), ctx)
        key = spec.get("key")
        if not isinstance(key, list) or not key or len(set(key)) != len(key) or not all(
                isinstance(k, str) and k in fs and isinstance(fs[k], dict) and "scalar" in fs[k] for k in key):
            out.append("%s: key must list distinct scalar fields of the record" % ctx)

    def walk(node, ctx):
        if node == "opaque":
            return
        if not isinstance(node, dict) or len(node) != 1:
            out.append("%s: node is opaque or a one-key map" % ctx)
            return
        (k, v), = node.items()
        if k in ("scalar", "set"):
            if v not in SCALAR_TYPES:
                out.append("%s: %s type must be one of %s" % (ctx, k, SCALAR_TYPES))
        elif k == "composite":
            fields(v, ctx)
        elif k == "list":
            if not isinstance(v, dict):
                out.append("%s: list spec must be a map" % ctx)
            elif set(v) == {"choice"}:
                cases = v["choice"]
                if not isinstance(cases, list) or len(cases) < 2:
                    out.append("%s: choice needs at least two cases" % ctx)
                    return
                names, keysets = set(), set()
                for c in cases:
                    if not isinstance(c, dict) or set(c) != {"name", "key", "fields"}:
                        out.append("%s: case needs name, key, fields" % ctx)
                        continue
                    keyed(c, "%s.%s" % (ctx, c.get("name")))
                    ks = frozenset(c["key"]) if isinstance(c.get("key"), list) else None
                    if c["name"] in names or ks in keysets:
                        out.append("%s: cases must differ in name and in key set (grammar C17)" % ctx)
                    names.add(c["name"])
                    keysets.add(ks)
            elif set(v) == {"key", "fields"}:
                keyed(v, ctx)
            else:
                out.append("%s: list is {key, fields} or {choice}" % ctx)
        else:
            out.append("%s: unknown node kind %r" % (ctx, k))

    fields(tree, "tree")
    return out


def check_schema(kind, schema, pins, where, vs, trees=None):
    if not isinstance(schema, dict) or len(schema) != 1:
        vs.append(Violation("SCHEMA_FIXTURE", where, "schema is exactly one of synthetic or pin"))
        return
    if "pin" in schema:
        if schema["pin"] not in pins.get("schemas", {}):
            vs.append(Violation("REF_DANGLING", where, "schema pin %r not in pins.yaml" % (schema["pin"],)))
        return
    syn = schema.get("synthetic")
    if not isinstance(syn, dict):
        vs.append(Violation("SCHEMA_FIXTURE", where, "schema is exactly one of synthetic or pin"))
        return
    if kind in ("path_parse", "path_format", "grammar_manifest"):
        if syn != {"lexical": True}:
            vs.append(Violation("SCHEMA_FIXTURE", where, "kinds without a schema take synthetic: {lexical: true}"))
    elif kind.startswith("path_") or kind in DIFF_KINDS or kind in EDIT_KINDS or kind == "basis":
        if set(syn) != {"tree"}:
            vs.append(Violation("SCHEMA_FIXTURE", where, "path kind needs synthetic.tree"))
        elif isinstance(syn["tree"], str):
            if syn["tree"] not in (trees or {}):
                vs.append(Violation("REF_DANGLING", where, "tree %r not in the suite's trees" % syn["tree"]))
        else:
            for p in tree_problems(syn["tree"]):
                vs.append(Violation("SCHEMA_FIXTURE", where, p))
    elif kind == "validate":
        if set(syn) == {"slot"}:
            if syn["slot"] not in SLOTS:
                vs.append(Violation("SCHEMA_FIXTURE", where, "unknown slot %r" % (syn["slot"],)))
        elif set(syn) == {"shape"}:
            check_shape(syn["shape"], where, vs)
        else:
            vs.append(Violation("SCHEMA_FIXTURE", where, "validate needs synthetic.slot or synthetic.shape"))
    else:
        if set(syn) != {"position"} or not valid_position(syn["position"]):
            vs.append(Violation("SCHEMA_FIXTURE", where, "needs synthetic.position with a valid position"))


def check_shape(shape, where, vs):
    if not isinstance(shape, dict) or not set(shape) <= SHAPE_KEYS or "type" not in shape \
            or shape.get("presence") not in PRESENCE:
        vs.append(Violation("SCHEMA_FIXTURE", where, "shape needs type and presence, keys in %s" % sorted(SHAPE_KEYS)))
        return
    if not isinstance(shape["type"], str):
        vs.append(Violation("SCHEMA_FIXTURE", where, "shape.type must be a string"))
    if "nullable" in shape and not isinstance(shape["nullable"], bool):
        vs.append(Violation("SCHEMA_FIXTURE", where, "shape.nullable must be a boolean"))
    if "min_items" in shape and yaml_type(shape["min_items"]) != "integer":
        vs.append(Violation("SCHEMA_FIXTURE", where, "shape.min_items must be an integer"))
    if "elements" in shape:
        check_elements(shape, where, vs)
    if "fields" in shape:
        f = shape["fields"]
        if not isinstance(f, dict) or not f:
            vs.append(Violation("SCHEMA_FIXTURE", where, "shape.fields maps names to mandatory|optional or a nested shape"))
            return
        for v in f.values():
            if isinstance(v, dict):
                check_shape(dict({"type": "composite"}, **v), where, vs)
            elif v not in PRESENCE:
                vs.append(Violation("SCHEMA_FIXTURE", where, "shape.fields maps names to mandatory|optional or a nested shape"))


def check_elements(shape, where, vs):
    """The element structure of a list (ADR 0002): one case with key and fields, or item_choice cases."""
    el = shape["elements"]
    bad = "shape.elements is {key: [names], fields: {...}} or {cases: [{name, key, fields}, ...]}, on a type: list"
    if shape.get("type") != "list" or not isinstance(el, dict):
        vs.append(Violation("SCHEMA_FIXTURE", where, bad))
        return
    cases = [el] if set(el) == {"key", "fields"} else el.get("cases") if set(el) == {"cases"} else None
    if not isinstance(cases, list) or not cases or (set(el) == {"cases"} and len(cases) < 2):
        vs.append(Violation("SCHEMA_FIXTURE", where, bad))
        return
    for c in cases:
        ok = isinstance(c, dict) and set(c) <= {"name", "key", "fields"} and {"key", "fields"} <= set(c)
        ok = ok and isinstance(c["key"], list) and c["key"] and all(isinstance(k, str) for k in c["key"])
        ok = ok and isinstance(c["fields"], dict) and c["fields"] and set(c["key"]) <= set(c["fields"])
        ok = ok and all(isinstance(v, dict) and "type" in v for v in c["fields"].values())
        if not ok:
            vs.append(Violation("SCHEMA_FIXTURE", where, bad))
            return
        for v in c["fields"].values():
            check_shape(v, where, vs)


def check_case(case, suite, defaults, pins, codes, adr_numbers, where, vs, used_codes, trees=None):
    if not isinstance(case, dict):
        vs.append(Violation("CASE_FIELD", where, "case is not a mapping"))
        return None
    cid = case.get("id")
    where = "%s[%s]" % (where, cid)
    for f in REQUIRED_COMMON:
        if f not in case:
            vs.append(Violation("CASE_FIELD", where, "missing %s" % f))
    kind = case.get("kind")
    if "kind" in case and kind not in KIND_FIELDS:
        vs.append(Violation("KIND_UNKNOWN", where, "kind %r" % (kind,)))
        kind = None
    if kind:
        unknown = set(case) - COMMON_FIELDS - KIND_FIELDS[kind]
        if unknown:
            vs.append(Violation("CASE_FIELD", where, "unknown fields %s for kind %s" % (sorted(unknown), kind)))
        check_inputs(kind, case, where, vs)
    if isinstance(cid, str):
        if not (SLUG.match(cid.split(".", 1)[-1]) and cid.startswith(suite + ".")):
            vs.append(Violation("ID_FORMAT", where, "id must be %s.<slug>" % suite))
    elif "id" in case:
        vs.append(Violation("ID_FORMAT", where, "id is not a string"))
    if case.get("polarity") not in ("positive", "negative"):
        vs.append(Violation("POLARITY", where, "polarity must be positive or negative"))
    eff = dict(defaults)
    eff.update({k: case[k] for k in ("adr", "grammar") if k in case})
    for f in ("adr", "grammar"):
        if f not in eff:
            vs.append(Violation("CASE_FIELD", where, "no %s on the case or in defaults" % f))
    m = ADR_REF.match(str(eff.get("adr", "")))
    if "adr" in eff and (not m or m.group(1) not in adr_numbers):
        vs.append(Violation("REF_DANGLING", where, "adr %r has no docs/adr file" % (eff["adr"],)))
    if "grammar" in eff and eff["grammar"] not in pins.get("grammar", {}):
        vs.append(Violation("REF_DANGLING", where, "grammar pin %r not in pins.yaml" % (eff["grammar"],)))
    if kind and "schema" in case:
        check_schema(kind, case["schema"], pins, where + ".schema", vs, trees)
    if kind in ("validate", "basis") or {"origin", "attestation", "attested_positions"} & set(case):
        check_origin(case, where, vs)
    if kind == "collapse" and "basis" in case and case["basis"] not in BASES:
        vs.append(Violation("BASIS", where, "the companion basis of the position is one of %s (absent means default)" % (BASES,)))
    if kind == "basis" and case.get("via", "expand") != "expand":
        vs.append(Violation("INPUT_PRESENCE", where, "via is expand or absent"))
    if kind in DIFF_KINDS and "schema_b" in case:
        check_schema(kind, case["schema_b"], pins, where + ".schema_b", vs, trees)
    expect = case.get("expect")
    if kind and isinstance(expect, dict):
        allowed, required = EXPECT_KEYS[kind]
        if not set(expect) <= allowed or not expect:
            vs.append(Violation("EXPECT_FIELD", where, "expect keys %s not within %s" % (sorted(expect), sorted(allowed))))
        for r in sorted(required or ()):
            if r not in expect:
                vs.append(Violation("EXPECT_FIELD", where, "expect.%s is required for %s" % (r, kind)))
        check_expect_types(kind, expect, where + ".expect", vs)
        if "basis" in expect and expect["basis"] not in BASES:
            vs.append(Violation("BASIS", where + ".expect", "basis is one of %s" % (BASES,)))
        if kind == "basis" and "bases" in expect:
            bs = expect["bases"]
            if not isinstance(bs, dict) or not bs:
                vs.append(Violation("BASIS", where + ".expect.bases", "bases maps position paths to bases"))
            else:
                for p, b in bs.items():
                    try:
                        ok_path = bool(parse_logical_path(p))
                    except ValueError:
                        ok_path = False
                    if not ok_path or b not in BASES:
                        vs.append(Violation("BASIS", "%s.expect.bases[%s]" % (where, p), "a canonical position path and one of %s" % (BASES,)))
        if kind == "basis" and ("bases" in expect) == ("errors" in expect):
            vs.append(Violation("EXPECT_FIELD", where + ".expect", "exactly one of bases and errors"))
        if kind == "recognize":
            check_recognize_expect(case, expect, where + ".expect", vs)
        if kind.startswith("path_"):
            check_path_expect(kind, case, expect, where + ".expect", vs)
        if kind in DIFF_KINDS:
            check_diff_expect(expect, where + ".expect", vs)
        if kind in EDIT_KINDS:
            check_edit_expect(kind, case, expect, where + ".expect", vs)
        if "errors" in expect:
            check_errors(kind, case, expect, codes, where + ".expect", vs, used_codes)
        found = [x for x in collect_codes(expect) if isinstance(x, str)]
        negative = "errors" in expect
        if case.get("polarity") in ("positive", "negative") and (case["polarity"] == "negative") != negative:
            vs.append(Violation("POLARITY", where, "polarity %s but the expectation %s an error code"
                                % (case["polarity"], "carries" if negative else "carries no")))
        for code in found:
            if code not in codes:
                vs.append(Violation("CODE_UNKNOWN", where, "code %s not in codes.yaml" % code))
        statuses = {codes[c]["status"] for c in found if c in codes}
        if found:
            if case.get("codes_status") not in statuses or len(statuses) != 1:
                vs.append(Violation("CODE_STATUS", where, "codes_status %r, registry says %s"
                                    % (case.get("codes_status"), sorted(statuses))))
        elif "codes_status" in case:
            vs.append(Violation("CODE_STATUS", where, "codes_status on a case without codes"))
    elif kind and "expect" in case:
        vs.append(Violation("EXPECT_FIELD", where, "expect is not a mapping"))
    return cid


def strict_eq(a, b):
    """Equality that never mixes 0 with false, 1 with true, or a list with a map."""
    if yaml_type(a) != yaml_type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_eq(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_eq(x, y) for x, y in zip(a, b))
    return a == b


def valid_locator(at):
    return isinstance(at, list) and all(
        (isinstance(s, str) and s) or (yaml_type(s) == "integer" and s >= 0) for s in at)


def check_segments(segs, where, vs, strict_names):
    """Segment list of the typed path model: Field(name) and ListItem(keys), item after field."""
    if not isinstance(segs, list):
        vs.append(Violation("PATH_SEGMENTS", where, "segments must be a list"))
        return
    prev = None
    for i, s in enumerate(segs):
        w = "%s[%d]" % (where, i)
        if isinstance(s, dict) and set(s) == {"field"} and isinstance(s["field"], str):
            if strict_names and (not s["field"] or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in s["field"])):
                vs.append(Violation("PATH_SEGMENTS", w, "field name must be non-empty without control characters"))
            prev = "field"
        elif isinstance(s, dict) and set(s) == {"item"} and isinstance(s["item"], list) and s["item"]:
            if prev != "field":
                vs.append(Violation("PATH_SEGMENTS", w, "an item selector must directly follow a field"))
            names = []
            for j, comp in enumerate(s["item"]):
                if not (isinstance(comp, dict) and set(comp) == {"name", "key"} and isinstance(comp["name"], str)):
                    vs.append(Violation("PATH_SEGMENTS", "%s.item[%d]" % (w, j), "component is {name, key}"))
                    continue
                names.append(comp["name"])
                check_typed(comp["key"], "%s.item[%d].key" % (w, j), vs)
                if strict_names and isinstance(comp["key"], dict) and comp["key"].get("type") not in ("string", "integer"):
                    vs.append(Violation("PATH_SEGMENTS", "%s.item[%d].key" % (w, j), "a parsed key is a string or an integer"))
                if strict_names and (not comp["name"] or any(
                        ord(ch) < 0x20 or ord(ch) == 0x7f for ch in comp["name"])):
                    vs.append(Violation("PATH_SEGMENTS", "%s.item[%d]" % (w, j),
                                        "component name must be non-empty without control characters"))
            if len(set(names)) != len(names):
                vs.append(Violation("PATH_SEGMENTS", w, "duplicate component names"))
            prev = "item"
        else:
            vs.append(Violation("PATH_SEGMENTS", w, "segment is {field: name} or {item: [{name, key}]}"))
            prev = None


def check_path_inputs(kind, case, where, vs):
    def need(*names):
        extra = KIND_FIELDS[kind] - set(names)
        if any(n not in case for n in names) or extra & set(case) - set(names):
            vs.append(Violation("INPUT_PRESENCE", where, "kind %s takes exactly %s" % (kind, INPUT_FORMS[kind])))
            return False
        return True
    if kind in ("path_parse", "path_resolve"):
        if need("path") and not isinstance(case["path"], str):
            vs.append(Violation("INPUT_PRESENCE", where, "path must be a string"))
    elif kind == "path_format":
        if need("segments"):
            check_segments(case["segments"], where + ".segments", vs, False)
    elif kind == "path_of":
        if need("document", "at") and not valid_locator(case["at"]):
            vs.append(Violation("PATH_LOCATOR", where, "at is a list of field names and non-negative integer indices"))
    elif kind == "path_stability":
        if not need("variants"):
            return
        var = case["variants"]
        def body(v):  # a variant holds a parsed document or raw document_yaml text, never both
            return v.get("document") if "document" in v else v.get("document_yaml")
        if not isinstance(var, list) or len(var) < 2 or not all(
                isinstance(v, dict) and set(v) in ({"document", "at"}, {"document_yaml", "at"})
                and valid_locator(v["at"]) and ("document" in v or isinstance(v["document_yaml"], str)) for v in var):
            vs.append(Violation("STABILITY_VARIANTS", where,
                                "variants: at least two {document | document_yaml, at} with a valid locator"))
            return
        for i in range(len(var)):
            for j in range(i + 1, len(var)):
                if ("document" in var[i]) == ("document" in var[j]) and strict_eq(body(var[i]), body(var[j])):
                    vs.append(Violation("STABILITY_VARIANTS", where,
                                        "variants %d and %d have identical documents, the stability case would prove nothing" % (i, j)))


POLICY_FIELDS = ("access", "modify", "inherit", "default_injection", "conformance")
EVENT_FIELDS = {
    "added": ({"kind", "path", "next"}, "next"),
    "removed": ({"kind", "path", "previous"}, "previous"),
    "value_change": ({"kind", "path", "previous", "next"}, None),
    "policy_declaration_change": ({"kind", "path", "changed"}, None),
    "set_change": ({"kind", "path", "added", "removed"}, None),
}


def operand_form(d, side, where, vs):
    """One operand of a diff: a parsed document or raw YAML text, exactly one of them."""
    has_doc, has_text = side in d, side + "_yaml" in d
    if has_doc == has_text:
        vs.append(Violation("DIFF_INPUT", where, "operand %s is exactly one of %s and %s_yaml" % (side, side, side)))
        return None
    if has_text:
        if not isinstance(d[side + "_yaml"], str):
            vs.append(Violation("DIFF_INPUT", where, "%s_yaml must be a string" % side))
        return ("text", d[side + "_yaml"])
    return ("doc", d[side])


def check_diff_inputs(kind, case, where, vs):
    extra = set(case) & {"a", "a_yaml", "b", "b_yaml", "variants"}
    if kind == "diff":
        if "variants" in case:
            vs.append(Violation("DIFF_INPUT", where, "variants belong to diff_stability"))
        operand_form(case, "a", where, vs)
        operand_form(case, "b", where, vs)
    else:
        if extra - {"variants"}:
            vs.append(Violation("DIFF_INPUT", where, "diff_stability takes variants only"))
        var = case.get("variants")
        if not isinstance(var, list) or len(var) < 2 or not all(isinstance(v, dict) for v in var):
            vs.append(Violation("DIFF_INPUT", where, "variants: at least two {a|a_yaml, b|b_yaml}"))
        else:
            pairs = []
            for i, v in enumerate(var):
                if not set(v) <= {"a", "a_yaml", "b", "b_yaml"}:
                    vs.append(Violation("DIFF_INPUT", "%s.variants[%d]" % (where, i), "variant holds a|a_yaml and b|b_yaml only"))
                pairs.append((operand_form(v, "a", where, vs), operand_form(v, "b", where, vs)))
            for i in range(len(pairs)):
                for j in range(i + 1, len(pairs)):
                    if all(x is not None for x in pairs[i] + pairs[j]) and all(
                            x[0] == y[0] and strict_eq(x[1], y[1]) for x, y in zip(pairs[i], pairs[j])):
                        vs.append(Violation("DIFF_INPUT", where, "variants %d and %d are identical, the stability case would prove nothing" % (i, j)))
    if "scope" in case:
        sc = case["scope"]
        if not isinstance(sc, list) or not sc:
            vs.append(Violation("DIFF_INPUT", where, "scope is a non-empty list of canonical logical paths"))
        else:
            for t in sc:
                try:
                    parse_logical_path(t)
                except ValueError as exc:
                    vs.append(Violation("DIFF_INPUT", where, "scope entry %r is not a canonical path: %s" % (t, exc)))
    if "schema_b" in case:
        if case["schema_b"] == case.get("schema"):
            vs.append(Violation("DIFF_INPUT", where, "schema_b equal to schema is no context mismatch"))


BASES = ("default", "authored_claim", "binding_attested")


def check_origin(case, where, vs):
    """origin authored | materialized; attestation and attested_positions exactly when materialized (ADR 0010)."""
    origin = case.get("origin", "authored")
    att = case.get("attestation")
    pos = case.get("attested_positions")
    bad = origin not in ("authored", "materialized") or ("attestation" in case and not (isinstance(att, str) and att.strip()))
    if "attested_positions" in case:
        if not isinstance(pos, list):
            bad = True
        else:
            for p in pos:
                try:
                    segs = parse_logical_path(p)
                except ValueError:
                    bad = True
                else:
                    if not segs:
                        bad = True
            if len(set(pos)) != len(pos):
                bad = True
    if bad:
        vs.append(Violation("ORIGIN_CONTEXT", where, "origin authored|materialized, attestation a non-empty reference, "
                            "attested_positions a list of distinct canonical position paths"))
        return
    given = ("attestation" in case, "attested_positions" in case)
    consistent = given == ((origin == "materialized"),) * 2
    errs = case.get("expect", {}).get("errors") if isinstance(case.get("expect"), dict) else None
    codes = [e.get("code") for e in errs if isinstance(e, dict)] if isinstance(errs, list) else []
    if consistent and "ORIGIN_ATTESTATION_MISMATCH" in codes:
        vs.append(Violation("ORIGIN_CONTEXT", where, "a consistent context cannot give ORIGIN_ATTESTATION_MISMATCH"))
    if not consistent and codes != ["ORIGIN_ATTESTATION_MISMATCH"]:
        vs.append(Violation("ORIGIN_CONTEXT", where, "origin %s with attestation=%s attested_positions=%s must expect exactly "
                            "ORIGIN_ATTESTATION_MISMATCH" % (origin, given[0], given[1])))


def check_manifest_input(case, where, vs):
    fl = case.get("files")
    if not (isinstance(fl, list) and fl and all(isinstance(f, dict) and set(f) == {"path", "sha256"} and isinstance(f["path"], str)
                                               and f["path"] and HEX64.match(str(f["sha256"])) for f in fl)) \
            or "declared" not in case or not HEX64.match(str(case["declared"])):
        vs.append(Violation("MANIFEST_FIXTURE", where, "files is a non-empty list of {path, sha256 (64-hex)}, declared a 64-hex digest"))
        return
    if len({f["path"] for f in fl}) != len(fl):
        vs.append(Violation("MANIFEST_FIXTURE", where, "a path is listed twice"))
        return
    same = gid.manifest_digest([(f["path"], f["sha256"]) for f in fl]) == case["declared"]
    exp = case.get("expect", {})
    codes_ = [e.get("code") for e in exp.get("errors", []) if isinstance(e, dict)] if isinstance(exp, dict) and isinstance(exp.get("errors"), list) else []
    if same and codes_:
        vs.append(Violation("MANIFEST_CONSISTENCY", where, "the declared digest matches the files, so no GRAMMAR_DIGEST_MISMATCH can be expected"))
    if not same and codes_ != ["GRAMMAR_DIGEST_MISMATCH"]:
        vs.append(Violation("MANIFEST_CONSISTENCY", where, "the declared digest differs from the files, so GRAMMAR_DIGEST_MISMATCH is the only valid expectation"))


def check_inputs(kind, case, where, vs):
    has = lambda f: f in case
    if kind.startswith("path_"):
        check_path_inputs(kind, case, where, vs)
        return
    if kind in DIFF_KINDS:
        check_diff_inputs(kind, case, where, vs)
        return
    if kind in EDIT_KINDS:
        check_edit_inputs(kind, case, where, vs)
        return
    if kind == "grammar_manifest":
        check_manifest_input(case, where, vs)
        return
    if kind == "basis":
        if "input" not in case:
            vs.append(Violation("INPUT_PRESENCE", where, "kind basis takes input"))
        return
    if kind == "recognize":
        ok = has("input") != has("input_yaml")
    elif kind == "validate":
        ok = sum(map(has, ("input", "absent", "input_yaml"))) == 1 and (case.get("absent", True) is True)
    elif kind == "roundtrip":
        ok = has("input") != has("absent") and (case.get("absent", True) is True)
    elif kind in ("collapse", "expand"):
        ok = has("input")
    else:
        ok = has("a") and has("b")
    if not ok:
        vs.append(Violation("INPUT_PRESENCE", where, "kind %s takes %s" % (kind, INPUT_FORMS[kind])))
    if kind in ("recognize", "validate") and has("input_yaml") and not isinstance(case["input_yaml"], str):
        vs.append(Violation("INPUT_PRESENCE", where, "input_yaml must be a string"))
    if kind == "collapse" and not isinstance(case.get("authored_expanded"), bool):
        vs.append(Violation("INPUT_PRESENCE", where, "collapse needs boolean authored_expanded"))


def check_pins(doc, vs):
    where = "corpus/pins.yaml"
    if not isinstance(doc, dict) or set(doc) != {"corpus_format", "grammar", "schemas"}:
        vs.append(Violation("ENVELOPE", where, "keys must be corpus_format, grammar, schemas"))
        return {}
    version(doc, where, vs)
    for name, g in (doc["grammar"] or {}).items():
        w = "%s grammar.%s" % (where, name)
        if not isinstance(g, dict) or set(g) != {"repo", "ref", "commit", "files", "manifest_sha256"} \
                or not isinstance(g["repo"], str) or not HEX40.match(str(g["commit"])) or not HEX64.match(str(g["manifest_sha256"])):
            vs.append(Violation("PIN_INVALID", w, "needs repo, ref, 40-hex commit, files, 64-hex manifest_sha256"))
            continue
        fl = g["files"]
        ok = isinstance(fl, list) and all(isinstance(f, dict) and set(f) == {"path", "sha256"} and isinstance(f["path"], str)
                                          and HEX64.match(str(f["sha256"])) for f in fl)
        if not ok:
            vs.append(Violation("PIN_INVALID", w, "files is a list of {path, sha256 (64-hex)}"))
            continue
        paths = [f["path"] for f in fl]
        if len(set(paths)) != len(paths):
            vs.append(Violation("PIN_INVALID", w, "a path is listed twice"))
        elif sorted(paths) != sorted(gid.GRAMMAR_FILES):
            vs.append(Violation("PIN_INVALID", w, "the file set must be exactly the accepted one (ADR 0002): %s" % list(gid.GRAMMAR_FILES)))
        elif paths != [e[0] for e in gid.sorted_by_path([(p, "") for p in paths])]:
            vs.append(Violation("PIN_INVALID", w, "files are sorted by the UTF-8 bytes of the path"))
        else:
            digest = gid.manifest_digest([(f["path"], f["sha256"]) for f in fl])
            if digest != g["manifest_sha256"]:
                vs.append(Violation("PIN_DIGEST", w, "manifest_sha256 is %s but the files give %s" % (g["manifest_sha256"], digest)))
            if name != gid.pin_name(g["repo"], g["commit"], g["manifest_sha256"]):
                vs.append(Violation("PIN_INVALID", w, "the pin name must carry the identity: %s"
                                    % gid.pin_name(g["repo"], g["commit"], g["manifest_sha256"])))
    for name, s in (doc["schemas"] or {}).items():
        if not isinstance(s, dict) or set(s) != {"repo", "commit", "path", "sha256"} \
                or not HEX40.match(str(s["commit"])) or not HEX64.match(str(s["sha256"])):
            vs.append(Violation("PIN_INVALID", "%s schemas.%s" % (where, name),
                                "needs repo, 40-hex commit, path, 64-hex sha256"))
    return {"grammar": doc["grammar"] or {}, "schemas": doc["schemas"] or {}}


def version(doc, where, vs):
    if doc.get("corpus_format") != FORMAT_VERSION or isinstance(doc.get("corpus_format"), bool):
        vs.append(Violation("FORMAT_VERSION", where, "corpus_format must be %d" % FORMAT_VERSION))


def check_codes(doc, vs):
    where = "corpus/codes.yaml"
    if not isinstance(doc, dict) or set(doc) != {"corpus_format", "codes"} or not isinstance(doc["codes"], dict):
        vs.append(Violation("ENVELOPE", where, "keys must be corpus_format, codes"))
        return {}
    version(doc, where, vs)
    out = {}
    for c, e in doc["codes"].items():
        w = "%s %s" % (where, c)
        if not re.match(r"^[A-Z][A-Z0-9_]*$", c):
            vs.append(Violation("CODE_REGISTRY", w, "name is UPPER_SNAKE"))
            continue
        if not isinstance(e, dict) or set(e) != {"status", "phases", "meaning", "trigger"}:
            vs.append(Violation("CODE_REGISTRY", w, "entry holds status, phases, meaning, trigger"))
            continue
        if e["status"] not in ("provisional", "stable"):
            vs.append(Violation("CODE_REGISTRY", w, "status is provisional or stable"))
        ph = e["phases"]
        if not isinstance(ph, dict) or not ph or any(p not in PHASES or a not in ANCHORS for p, a in ph.items()):
            vs.append(Violation("CODE_REGISTRY", w, "phases maps phases %s to anchors %s" % (PHASES, ANCHORS)))
            continue
        for p, a in ph.items():
            if p in ("path_parse",) and a != "path_text":
                vs.append(Violation("CODE_REGISTRY", w, "a path_parse error is anchored in the path text"))
            if p in ("path_resolve", "path_of", "path_format") and a != "none":
                vs.append(Violation("CODE_REGISTRY", w, "phase %s has no source anchor" % p))
        for k in ("meaning", "trigger"):
            if not isinstance(e[k], str) or not e[k].strip():
                vs.append(Violation("CODE_REGISTRY", w, "%s must be a non-empty text" % k))
        out[c] = e
    return out


WORKFLOW_PIN_RUN = 'echo "commit=$(python3 tools/grammar_identity.py commit --pins corpus/pins.yaml)" >> "$GITHUB_OUTPUT"'
WORKFLOW_VERIFY_RUN = "python3 tools/grammar_identity.py verify --pins corpus/pins.yaml --repo upstream/cic-primitives"
WORKFLOW_CHECKOUT_WITH = {"repository": "CentralInfraCore/cic-primitives",
                          "ref": "${{ steps.grammar_pin.outputs.commit }}", "path": "upstream/cic-primitives"}


def check_workflow(root, pins, vs):
    """The workflow that runs the gate must unconditionally reproduce the grammar identity from upstream.

    The three steps are compared whole, not by a fragment of their text: a command that merely
    mentions the verifier (an echo, a comment, a prefix that exits) is not the verifier.
    """
    where = ".github/workflows/corpus.yml"
    try:
        with open(os.path.join(root, where), encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        job = wf["jobs"]["corpus"]
        steps = job["steps"]
        assert isinstance(steps, list)
    except (OSError, KeyError, TypeError, AssertionError, yaml.YAMLError):
        vs.append(Violation("UPSTREAM_VERIFY", where, "workflow with jobs.corpus.steps not found"))
        return
    problems = []
    triggers = wf.get(True, wf.get("on"))  # PyYAML reads the key `on` as a boolean
    if not (isinstance(triggers, dict) and "pull_request" in triggers and "push" in triggers):
        problems.append("the workflow must run on pull_request and on push")
    for scope, name in ((wf, "workflow"), (job, "job")):
        for k in ("env", "defaults", "if", "continue-on-error"):
            if k in scope:
                problems.append("the %s must not set %s: it could change what the identity steps do" % (name, k))
    def find(pred):
        return next((k for k, st in enumerate(steps) if isinstance(st, dict) and pred(st)), None)
    ip = find(lambda st: st.get("id") == "grammar_pin")
    ic = find(lambda st: isinstance(st.get("with"), dict) and st["with"].get("repository") == "CentralInfraCore/cic-primitives")
    iv = find(lambda st: "grammar_identity.py verify" in str(st.get("run", "")))
    if ip is None or str(steps[ip].get("run", "")).strip() != WORKFLOW_PIN_RUN or set(steps[ip]) - {"id", "name", "run"}:
        problems.append("the pin step must be exactly: %s" % WORKFLOW_PIN_RUN)
    if ic is None or steps[ic].get("with") != WORKFLOW_CHECKOUT_WITH or not str(steps[ic].get("uses", "")).startswith("actions/checkout@") \
            or set(steps[ic]) - {"uses", "with", "name"}:
        problems.append("the upstream checkout must be actions/checkout with exactly %s" % WORKFLOW_CHECKOUT_WITH)
    if iv is None or str(steps[iv].get("run", "")).strip() != WORKFLOW_VERIFY_RUN or set(steps[iv]) - {"run", "name"}:
        problems.append("the verification step must be exactly: %s" % WORKFLOW_VERIFY_RUN)
    if None not in (ip, ic, iv) and not ip < ic < iv:
        problems.append("the order must be pin step, upstream checkout, verification")
    if iv is not None and any(isinstance(st, dict) and "grammar_identity.py verify" in str(st.get("run", "")) for k, st in enumerate(steps) if k != iv):
        problems.append("the verification command must appear once")
    for pr in problems:
        vs.append(Violation("UPSTREAM_VERIFY", where, pr))


def check_corpus(root):
    vs, stats = [], {"suites": 0, "cases": 0, "positive": 0, "negative": 0}
    cdir = os.path.join(root, "corpus")
    adr_numbers = {os.path.basename(p)[:4] for p in glob.glob(os.path.join(root, "docs", "adr", "[0-9]*.md"))}

    def load(rel):
        try:
            return load_file(os.path.join(root, rel))
        except LoadError as exc:
            vs.append(Violation(exc.rule, rel, exc.msg))
        except OSError as exc:
            vs.append(Violation("ENVELOPE", rel, "cannot read: %s" % exc))
        return None

    pins = check_pins(load("corpus/pins.yaml"), vs) if os.path.exists(os.path.join(cdir, "pins.yaml")) else {}
    if not pins:
        vs.append(Violation("ENVELOPE", "corpus/pins.yaml", "missing or invalid"))
    codes = {}
    if os.path.exists(os.path.join(cdir, "codes.yaml")):
        codes = check_codes(load("corpus/codes.yaml"), vs)
    else:
        vs.append(Violation("ENVELOPE", "corpus/codes.yaml", "missing"))

    check_workflow(root, pins, vs)
    ids, used = {}, set()
    allowed = {"corpus/pins.yaml", "corpus/codes.yaml", "corpus/accounting.yaml"}
    counts = {}                                                    # (suite, kind) -> the number of cases the corpus has
    for path in sorted(glob.glob(os.path.join(cdir, "**", "*.y*ml"), recursive=True)):
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        if rel in allowed:
            continue
        if not (len(parts) == 3 and parts[2] == "vectors.yaml"):
            vs.append(Violation("UNEXPECTED_FILE", rel, "only pins.yaml, codes.yaml, accounting.yaml and <suite>/vectors.yaml belong here"))
            continue
        doc = load(rel)
        if doc is None:
            continue
        suite = parts[1]
        stats["suites"] += 1
        need = {"corpus_format", "suite", "status", "defaults", "cases"}
        if not isinstance(doc, dict) or not need <= set(doc) <= need | {"trees"}:
            vs.append(Violation("ENVELOPE", rel, "keys must be %s, optionally trees" % sorted(need)))
            continue
        trees = doc.get("trees", {})
        if not isinstance(trees, dict):
            vs.append(Violation("ENVELOPE", rel, "trees must be a map"))
            trees = {}
        for tname, tree in trees.items():
            for p in tree_problems(tree):
                vs.append(Violation("SCHEMA_FIXTURE", "%s trees.%s" % (rel, tname), p))
        version(doc, rel, vs)
        if doc["suite"] != suite:
            vs.append(Violation("ENVELOPE", rel, "suite %r differs from directory %r" % (doc["suite"], suite)))
        if doc["status"] not in ("provisional", "stable"):
            vs.append(Violation("ENVELOPE", rel, "status must be provisional or stable"))
        if not isinstance(doc["defaults"], dict) or not set(doc["defaults"]) <= {"adr", "grammar"}:
            vs.append(Violation("ENVELOPE", rel, "defaults may hold only adr and grammar"))
            continue
        if not isinstance(doc["cases"], list) or not doc["cases"]:
            vs.append(Violation("ENVELOPE", rel, "cases must be a non-empty list"))
            continue
        for case in doc["cases"]:
            cid = check_case(case, suite, doc["defaults"], pins, codes, adr_numbers, rel, vs, used, trees)
            stats["cases"] += 1
            if isinstance(case, dict) and isinstance(case.get("kind"), str):
                counts[(suite, case["kind"])] = counts.get((suite, case["kind"]), 0) + 1
            if isinstance(case, dict):
                if case.get("polarity") in ("positive", "negative"):
                    stats[case["polarity"]] += 1
            if isinstance(cid, str):
                if cid in ids:
                    vs.append(Violation("ID_DUPLICATE", rel, "%s also in %s" % (cid, ids[cid])))
                ids[cid] = rel
    if os.path.exists(os.path.join(cdir, "accounting.yaml")):
        check_accounting(load("corpus/accounting.yaml"), counts, vs)
    else:
        vs.append(Violation("ENVELOPE", "corpus/accounting.yaml", "missing"))
    used_codes = {code for code, _ in used}
    for c in sorted(set(codes) - used_codes):
        vs.append(Violation("CODE_UNUSED", "corpus/codes.yaml", "%s is used by no case" % c))
    for c in sorted(codes):
        for p in sorted(codes[c]["phases"]):
            if c in used_codes and (c, p) not in used:
                vs.append(Violation("PHASE_UNUSED", "corpus/codes.yaml", "%s is listed for phase %s but no case exercises it" % (c, p)))
    return vs, stats


def check_accounting(doc, counts, vs):
    """corpus/accounting.yaml is written by hand, in advance: the runners compare their counts with it. Here it must list exactly the kinds of the corpus,
    with as many cases as the corpus has; what is run and what is not is the runners' to confirm, not this checker's."""
    where = "corpus/accounting.yaml"
    if not isinstance(doc, dict) or set(doc) != {"format", "kinds"} or not isinstance(doc.get("kinds"), dict):
        vs.append(Violation("ACCOUNTING", where, "keys must be format and kinds (a map)"))
        return
    if doc["format"] != "cic-accounting/1":
        vs.append(Violation("ACCOUNTING", where, "format must be cic-accounting/1"))
    for key, v in doc["kinds"].items():
        if not isinstance(v, dict) or not v or not set(v) <= {"run", "not_run"} or any(type(n) is not int or n < 0 for n in v.values()):
            vs.append(Violation("ACCOUNTING", where, "%s: a map of run and / or not_run, non-negative integers" % key))
            continue
        suite, _, kind = str(key).partition("/")
        have = counts.get((suite, kind))
        listed = v.get("run", 0) + v.get("not_run", 0)
        if have is None:
            vs.append(Violation("ACCOUNTING", where, "%s: no case of that kind in that suite" % key))
        elif have != listed:
            vs.append(Violation("ACCOUNTING", where, "%s: the accounting lists %d cases, the corpus has %d" % (key, listed, have)))
    for (suite, kind), n in sorted(counts.items()):
        if "%s/%s" % (suite, kind) not in doc["kinds"]:
            vs.append(Violation("ACCOUNTING", where, "%s/%s (%d cases) is a kind of the corpus that the accounting does not list" % (suite, kind, n)))


# --- seeded-fault self-test --------------------------------------------------

def apply_fault(fault, root):
    for op in fault["ops"]:
        path = os.path.join(root, op["file"])
        if "add" in op:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(op["add"])
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if text.count(op["old"]) != 1:
            raise ValueError("%r occurs %d times in %s (must be exactly once)"
                             % (op["old"][:70], text.count(op["old"]), op["file"]))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(op["old"], op["new"]))


def loader_invariants():
    """The strict loader must keep the YAML meaning the corpus relies on (ADR 0003)."""
    def load(text):
        return yaml.load(text, Loader=StrictLoader)
    checks = [
        ("empty scalar is null", load("a:\n") == {"a": None}),
        ("empty scalar equals explicit null", load("a:\n") == load("a: null\n")),
        ("tilde is null", load("a: ~\n") == {"a": None}),
        ("empty string stays a string", load("a: ''\n") == {"a": ""}),
        ("0 is an integer, not a boolean", yaml_type(load("a: 0\n")["a"]) == "integer"),
        ("false is a boolean", yaml_type(load("a: false\n")["a"]) == "boolean"),
        ("quoted 0 is a string", yaml_type(load("a: '0'\n")["a"]) == "string"),
        ("empty flow list is a list", load("a: []\n") == {"a": []}),
        ("empty flow map is a map", load("a: {}\n") == {"a": {}}),
        ("strict equality: 0 is not false", not strict_eq(0, False)),
        ("strict equality: 1 is not true", not strict_eq(1, True)),
        ("strict equality: [] is not {}", not strict_eq([], {})),
        ("strict equality: null is not ''", not strict_eq(None, "")),
        ("strict equality: nested", strict_eq({"a": [0, None]}, {"a": [0, None]})
         and not strict_eq({"a": [0]}, {"a": [False]})),
    ]
    k = lambda t: path_order_key(parse_logical_path(t))
    checks += [
        ("order: prefix before extension", k("$.a") < k("$.a.x")),
        ("order: segments, not text ('$.a.x' before '$.a b')", k("$.a.x") < k("$.a b") and "$.a b" < "$.a.x"),
        ("order: names by UTF-8 bytes", k("$.b") < k("$.\u00e9")),
        ("order: integer keys numerically", k("$.l[{k=2}]") < k("$.l[{k=10}]") and "$.l[{k=10}]" < "$.l[{k=2}]"),
        ("path: canonical text accepted", parse_logical_path('$.l[{name="a,b"}].x') is not None),
        ("path: whole segment prefix only", parse_logical_path("$.nope")[:len(parse_logical_path("$.no"))]
         != parse_logical_path("$.no")),
    ]
    bad = [name for name, ok in checks if not ok]
    for name in bad:
        print("  loader invariant broken: %s" % name)
    return 1 if bad else 0


def selftest(root):
    if loader_invariants():
        return 1
    spec_path = os.path.join(root, "tools", "corpus_faults.yaml")
    spec = load_file(spec_path)
    base, _ = check_corpus(root)
    if base:
        print("self-test: the unmodified corpus is not clean, faults would prove nothing")
        return 1
    covered, failed = set(), 0
    for fault in spec["faults"]:
        tmp = tempfile.mkdtemp(prefix="corpus-fault-")
        try:
            shutil.copytree(os.path.join(root, "corpus"), os.path.join(tmp, "corpus"))
            shutil.copytree(os.path.join(root, "docs", "adr"), os.path.join(tmp, "docs", "adr"))
            shutil.copytree(os.path.join(root, ".github", "workflows"), os.path.join(tmp, ".github", "workflows"))
            try:
                apply_fault(fault, tmp)
            except ValueError as exc:
                failed += 1
                print("  STALE %-30s %s" % (fault["id"], exc))
                continue
            found, _ = check_corpus(tmp)
        finally:
            shutil.rmtree(tmp)
        rules = {v.rule for v in found}
        if fault["rule"] in rules:
            covered.add(fault["rule"])
            print("  red   %-30s -> %s" % (fault["id"], fault["rule"]))
        else:
            failed += 1
            print("  GREEN %-30s expected %s, got %s" % (fault["id"], fault["rule"], sorted(rules) or "nothing"))
    for rule in sorted(set(RULES) - covered):
        failed += 1
        print("  rule %s has no fault that turns it red" % rule)
    print("self-test: %d faults, %d rules covered, %s"
          % (len(spec["faults"]), len(covered), "FAILED" if failed else "every rule can fail"))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--no-selftest", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    rc = 0
    if not args.selftest:
        vs, st = check_corpus(root)
        for v in vs:
            print(v)
        print("corpus: %d suites, %d cases (%d positive, %d negative), %d violations"
              % (st["suites"], st["cases"], st["positive"], st["negative"], len(vs)))
        rc = 1 if vs else 0
    if not args.no_selftest and rc == 0:
        rc = selftest(root)
    if rc == 0:
        print("STRUCTURAL CHECK ONLY: implementation conformance NOT RUN (rust: none, go: none)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
