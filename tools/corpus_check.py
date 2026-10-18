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

FORMAT_VERSION = 1
TYPES = ("null", "boolean", "integer", "float", "string", "list", "map")

RULES = {
    "YAML_SYNTAX": "file is not parseable YAML",
    "DUPLICATE_YAML_KEY": "a mapping repeats a key",
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
INPUT_FORMS = {  # kind -> (allowed input fields, rule on them)
    "recognize": "input|input_yaml",
    "validate": "input|absent",
    "roundtrip": "input|absent",
    "collapse": "input",
    "expand": "input",
    "equality": "ab",
}
KIND_FIELDS = {
    "recognize": {"input", "input_yaml"},
    "validate": {"input", "absent"},
    "roundtrip": {"input", "absent"},
    "collapse": {"input", "authored_expanded"},
    "expand": {"input"},
    "equality": {"a", "b"},
}
COMMON_FIELDS = {"id", "kind", "polarity", "description", "adr", "grammar",
                 "schema", "codes_status", "expect"}
REQUIRED_COMMON = ("id", "kind", "polarity", "schema", "expect")
EXPECT_KEYS = {
    "recognize": ({"class", "elements", "validate", "opaque_value", "phase", "code"}, None),
    "validate": ({"valid", "error", "stays_absent", "default_candidate", "path",
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
    """Codes live only at expect.error, expect.code and expect.validate.code.

    Typed values (`fields`, `preserved`, `expanded`, `collapsed`) are data and are
    never searched: a map that happens to hold `code` or `error` keys is not a code.
    """
    out = []
    for key in ("error", "code"):
        if key in expect:
            out.append(expect[key])
    v = expect.get("validate")
    if isinstance(v, dict) and "code" in v:
        out.append(v["code"])
    return out


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


def check_schema(kind, schema, pins, where, vs):
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
    if kind == "validate":
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
        if not isinstance(f, dict) or any(v not in PRESENCE for v in f.values()):
            vs.append(Violation("SCHEMA_FIXTURE", where, "shape.fields maps names to mandatory|optional"))


def check_case(case, suite, defaults, pins, codes, adr_numbers, where, vs, used_codes):
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
        check_schema(kind, case["schema"], pins, where + ".schema", vs)
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
        found = collect_codes(expect)
        if not all(isinstance(x, str) for x in found):
            vs.append(Violation("EXPECT_FIELD", where, "error and code expectations must be strings"))
            found = [x for x in found if isinstance(x, str)]
        used_codes.update(found)
        negative = bool(found)
        if case.get("polarity") in ("positive", "negative") and (case["polarity"] == "negative") != negative:
            vs.append(Violation("POLARITY", where, "polarity %s but the expectation %s an error code"
                                % (case["polarity"], "carries" if negative else "carries no")))
        for code in found:
            if code not in codes:
                vs.append(Violation("CODE_UNKNOWN", where, "code %s not in codes.yaml" % code))
        statuses = {codes[c] for c in found if c in codes}
        if found:
            if case.get("codes_status") not in statuses or len(statuses) != 1:
                vs.append(Violation("CODE_STATUS", where, "codes_status %r, registry says %s"
                                    % (case.get("codes_status"), sorted(statuses))))
        elif "codes_status" in case:
            vs.append(Violation("CODE_STATUS", where, "codes_status on a case without codes"))
    elif kind and "expect" in case:
        vs.append(Violation("EXPECT_FIELD", where, "expect is not a mapping"))
    return cid


def check_inputs(kind, case, where, vs):
    has = lambda f: f in case
    if kind == "recognize":
        ok = has("input") != has("input_yaml")
    elif kind in ("validate", "roundtrip"):
        ok = has("input") != has("absent") and (case.get("absent", True) is True)
    elif kind in ("collapse", "expand"):
        ok = has("input")
    else:
        ok = has("a") and has("b")
    if not ok:
        vs.append(Violation("INPUT_PRESENCE", where, "kind %s takes %s" % (kind, INPUT_FORMS[kind])))
    if kind == "recognize" and has("input_yaml") and not isinstance(case["input_yaml"], str):
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
    for c, st in doc["codes"].items():
        if not re.match(r"^[A-Z][A-Z0-9_]*$", c) or st not in ("provisional", "stable"):
            vs.append(Violation("CODE_REGISTRY", "%s %s" % (where, c), "name is UPPER_SNAKE, status provisional|stable"))
    return dict(doc["codes"])


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
        if not isinstance(doc, dict) or set(doc) != need:
            vs.append(Violation("ENVELOPE", rel, "keys must be %s" % sorted(need)))
            continue
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
            cid = check_case(case, suite, doc["defaults"], pins, codes, adr_numbers, rel, vs, used)
            stats["cases"] += 1
            if isinstance(case, dict):
                if case.get("polarity") in ("positive", "negative"):
                    stats[case["polarity"]] += 1
            if isinstance(cid, str):
                if cid in ids:
                    vs.append(Violation("ID_DUPLICATE", rel, "%s also in %s" % (cid, ids[cid])))
                ids[cid] = rel
    for c in sorted(set(codes) - used):
        vs.append(Violation("CODE_UNUSED", "corpus/codes.yaml", "%s is used by no case" % c))
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
            raise SystemExit("self-test: fault %s: %r occurs %d times in %s (must be exactly once)"
                             % (fault["id"], op["old"], text.count(op["old"]), op["file"]))
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
            apply_fault(fault, tmp)
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
