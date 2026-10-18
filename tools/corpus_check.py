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
import os
import re
import shutil
import sys
import tempfile

import yaml

FORMAT_VERSION = 2
TYPES = ("null", "boolean", "integer", "float", "string", "list", "map")

RULES = {
    "YAML_SYNTAX": "file is not parseable YAML",
    "DUPLICATE_YAML_KEY": "a mapping repeats a key",
    "YAML_ANCHOR": "anchor or alias, which fixtures do not use (they must read the same in every loader)",
    "AMBIGUOUS_SCALAR": "unquoted scalar that YAML 1.1 and 1.2 read differently",
    "UNEXPECTED_FILE": "YAML file in the corpus that is not pins, codes or a suite",
    "ENVELOPE": "missing, unknown or inconsistent file-level field",
    "FORMAT_VERSION": "corpus_format is not the supported version",
    "PIN_INVALID": "malformed pin (commit, sha256, repo, path)",
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
    "PHASE_UNUSED": "registry lists a phase for a code that no case exercises",
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
    "path_parse": "path",
    "path_format": "segments",
    "path_resolve": "path",
    "path_of": "document, at",
    "path_stability": "variants",  # kind -> (allowed input fields, rule on them)
    "recognize": "input|input_yaml",
    "validate": "input|absent|input_yaml",
    "roundtrip": "input|absent",
    "collapse": "input",
    "expand": "input",
    "equality": "ab",
}
KIND_FIELDS = {
    "path_parse": {"path"},
    "path_format": {"segments"},
    "path_resolve": {"path"},
    "path_of": {"document", "at"},
    "path_stability": {"variants"},
    "recognize": {"input", "input_yaml"},
    "validate": {"input", "absent", "input_yaml"},
    "roundtrip": {"input", "absent"},
    "collapse": {"input", "authored_expanded"},
    "expand": {"input"},
    "equality": {"a", "b"},
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
    "path_parse": ({"segments", "errors"}, None),
    "path_format": ({"text", "errors"}, None),
    "path_resolve": ({"target", "primitive_position", "errors"}, None),
    "path_of": ({"path", "errors"}, None),
    "path_stability": ({"path", "errors"}, None),
    "recognize": ({"class", "elements", "errors", "opaque_value"}, None),
    "validate": ({"valid", "errors", "stays_absent", "default_candidate",
                  "kind", "is_inherit", "preserved", "upstream_conflict"}, None),
    "roundtrip": ({"expanded", "collapsed", "fields"}, None),
    "collapse": ({"collapse", "fields"}, {"collapse"}),
    "expand": ({"fields"}, {"fields"}),
    "equality": ({"equal"}, {"equal"}),
}
PRESENCE = ("mandatory", "optional")
SHAPE_KEYS = {"type", "presence", "nullable", "default", "min_items", "fields"}
SLOTS = ("inherit_key", "inherit_policy", "access_modify")


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


PHASES = ("parse", "recognize", "validate", "path_parse", "path_format", "path_resolve", "path_of")
ANCHORS = ("second_key", "key", "value", "value_or_key", "mapping", "sequence", "parent", "path_text", "none")
KIND_PHASES = {
    "recognize": {"parse", "recognize", "validate"},
    "validate": {"parse", "recognize", "validate"},
    "path_parse": {"path_parse"},
    "path_format": {"path_format"},
    "path_resolve": {"path_parse", "path_resolve"},
    "path_of": {"path_of"},
    "path_stability": {"path_of"},
}
SINGLE_ERROR_PHASES = {"parse", "path_parse", "path_format", "path_resolve", "path_of"}
ERROR_KEYS = {"code", "phase", "logical_path", "source_location", "path_offset"}


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
        has_text = "input_yaml" in case
        if phase in ("path_parse", "path_format", "path_resolve") or kind == "path_stability":
            want = "not_applicable"
        elif phase == "path_of":
            want = "unavailable"
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
            elif has_text and isinstance(case["input_yaml"], str):
                p = location_problem(case["input_yaml"], sl)
                if p:
                    vs.append(Violation("ERROR_LOCATION", ew, p))
        elif sl != {"state": want}:
            vs.append(Violation("ERROR_LOCATION", ew, "source_location must be {state: %s} here (never null, never absent)" % want))
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
                    if phase == "path_resolve" and isinstance(case.get("path"), str):
                        try:
                            csegs = parse_logical_path(case["path"])
                        except ValueError:
                            csegs = None
                        if csegs is None or csegs[:len(lsegs)] != lsegs or not lsegs:
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
    if kind in ("path_parse", "path_format"):
        if syn != {"lexical": True}:
            vs.append(Violation("SCHEMA_FIXTURE", where, "lexical path kinds take synthetic: {lexical: true}"))
    elif kind.startswith("path_"):
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
    expect = case.get("expect")
    if kind and isinstance(expect, dict):
        allowed, required = EXPECT_KEYS[kind]
        if not set(expect) <= allowed or not expect:
            vs.append(Violation("EXPECT_FIELD", where, "expect keys %s not within %s" % (sorted(expect), sorted(allowed))))
        for r in sorted(required or ()):
            if r not in expect:
                vs.append(Violation("EXPECT_FIELD", where, "expect.%s is required for %s" % (r, kind)))
        check_expect_types(kind, expect, where + ".expect", vs)
        if kind == "recognize":
            check_recognize_expect(case, expect, where + ".expect", vs)
        if kind.startswith("path_"):
            check_path_expect(kind, case, expect, where + ".expect", vs)
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


def check_inputs(kind, case, where, vs):
    has = lambda f: f in case
    if kind.startswith("path_"):
        check_path_inputs(kind, case, where, vs)
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
        if not isinstance(g, dict) or set(g) != {"repo", "ref", "commit"} or not HEX40.match(str(g["commit"])):
            vs.append(Violation("PIN_INVALID", "%s grammar.%s" % (where, name), "needs repo, ref, 40-hex commit"))
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

    ids, used = {}, set()
    allowed = {"corpus/pins.yaml", "corpus/codes.yaml"}
    for path in sorted(glob.glob(os.path.join(cdir, "**", "*.y*ml"), recursive=True)):
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        if rel in allowed:
            continue
        if not (len(parts) == 3 and parts[2] == "vectors.yaml"):
            vs.append(Violation("UNEXPECTED_FILE", rel, "only pins.yaml, codes.yaml and <suite>/vectors.yaml belong here"))
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
            if isinstance(case, dict):
                if case.get("polarity") in ("positive", "negative"):
                    stats[case["polarity"]] += 1
            if isinstance(cid, str):
                if cid in ids:
                    vs.append(Violation("ID_DUPLICATE", rel, "%s also in %s" % (cid, ids[cid])))
                ids[cid] = rel
    used_codes = {code for code, _ in used}
    for c in sorted(set(codes) - used_codes):
        vs.append(Violation("CODE_UNUSED", "corpus/codes.yaml", "%s is used by no case" % c))
    for c in sorted(codes):
        for p in sorted(codes[c]["phases"]):
            if c in used_codes and (c, p) not in used:
                vs.append(Violation("PHASE_UNUSED", "corpus/codes.yaml", "%s is listed for phase %s but no case exercises it" % (c, p)))
    return vs, stats


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
