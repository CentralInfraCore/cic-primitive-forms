#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Byte-level cases for the raw YAML and parser boundary differential (docs/P5-RAW-YAML-UNIT3-CONTRACT.md; this is Unit 3a: the generator and the construction yardstick).

    gen_raw_yaml.py [--out FILE] [--undetermined-out FILE] [--summary]

One JSON line per case: {"id", "class", "bytes_hex", "parse_expect", "allowed", "mapping_expect", "pin"}.

THE LABELS COME FROM NONE OF THE READERS. They are fixed by construction, in three separate fields that are never merged:

  parse_expect   {"result": "accept", "tree": typed tree} | {"result": "refuse", "class": "syntax" | "duplicate_key" | "any"} | {"result": "undetermined"}
                 | {"result": "pinned_divergence"}            ("any": a refusal of either class; used where the documents do not fix which of two refusals wins)
  allowed        true | false | "undetermined"                membership in P_allowed, from docs/P1-SUPPORTED-SUBSET.md, never from a parser
  mapping_expect null | {"expect": {three digests} | null, "refuse": reason | null, "field": member | null}      only where the tree is accepted

A text that is valid YAML in the subset whose VALUE the mapping refuses (the envelope "3", null) is parse_expect accept, allowed true, mapping_expect refuse: it is not a parse
refusal. A construction the repository documents no verdict for is "undetermined" (a committed list, tools/parity/undetermined_raw_yaml.json), never an assumed defect.

The record texts are written by a small emitter of THIS file, independent of the Forms emitter and of all parsers. The mapping expectations of the tree-level cases are those of the
Unit 1 yardstick (tools/parity/gen_digest_mapping.py). The reader tools/parity/raw_yaml_subset_ref.py is used by the tests of this generator, not by the proof.

Parameters (Unit 3a plan, contract D7): seed 20261104; 40 valid records x 12 styles; every Unit 1 record case in 2 styles; 100 seeded pairs; resource limits for the readers
(contract section 3) are in LIMITS and are part of the report of the later units.
"""
import argparse
import copy
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import gen_digest_mapping as g1  # noqa: E402

DEFAULT_SEED = 20261104
PARAMS = {"seed": DEFAULT_SEED, "valid_records": 40, "pairs": 100}
LIMITS = {"case_timeout_seconds": 10, "memory_bytes": 6 * 1024 ** 3, "max_case_bytes": 2 * 1024 * 1024, "max_input_bytes": 16 * 1024 * 1024}
KNOWN_TXT = os.path.join(ROOT, "testdata", "p1", "parity-known.txt")


class Raw(str):
    """Text written as it is: a scalar the subset refuses or reads in a way a Python value cannot say."""


class Block:
    """A block scalar of the record: indicator '|', '|-', '>' or '>-'; the tree value is what YAML 1.2 gives for it."""

    def __init__(self, indicator, text):
        self.indicator, self.text = indicator, text


def typed(v):
    if isinstance(v, Block):
        return {"k": "str", "v": v.text + ("" if v.indicator.endswith("-") else "\n")}
    if isinstance(v, Raw):
        return raw_typed(str(v))
    if isinstance(v, dict):
        return {"k": "map", "v": [[k, typed(x)] for k, x in v.items()]}
    if isinstance(v, list):
        return {"k": "seq", "v": [typed(x) for x in v]}
    if v is None:
        return {"k": "null", "v": None}
    if isinstance(v, bool):
        return {"k": "bool", "v": v}
    if isinstance(v, int):
        return {"k": "int", "v": v}
    if isinstance(v, str):
        return {"k": "str", "v": v}
    raise TypeError(type(v))


def raw_typed(text):
    """The documented reading of a plain scalar written by hand (docs/P1-SUPPORTED-SUBSET.md, 'Supported subset')."""
    if text in ("", "null", "~"):
        return {"k": "null", "v": None}
    if text in ("true", "True", "TRUE"):
        return {"k": "bool", "v": True}
    if text in ("false", "False", "FALSE"):
        return {"k": "bool", "v": False}
    t = text[1:] if text[:1] in "+-" else text
    if t.isdigit() and not (len(t) > 1 and t[0] == "0"):
        return {"k": "int", "v": int(text)}
    return {"k": "str", "v": text}


# ---- the emitter ---------------------------------------------------------------------------------------------------------------------------------------------------

IDENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
WORDS = {"null", "true", "false", "yes", "no", "on", "off", "y", "n", "~", "Null", "NULL", "True", "TRUE", "False", "FALSE"}
B64 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")


class Unrepresentable(Exception):
    pass


def plain_ok(s, flow):
    if s == "" or s in WORDS or s[0].isdigit() and not flow:
        return False
    if s[0] in IDENT and all(c in IDENT or c in "./-" for c in s) and not s[0].isdigit() and not flow:
        return True
    if flow:
        return all(c in IDENT for c in s) and not s[0].isdigit()
    if len(s) == 44 and s.endswith("=") and all(c in B64 for c in s[:-1]):
        return not s[0].isdigit()                     # the real record has values that start with '+' or '/'; a digit start is quoted
    return False


def quote(s, how):
    if any(ord(c) < 32 or ord(c) > 126 for c in s):
        raise Unrepresentable(s)
    if how == "single":
        return "'" + s.replace("'", "''") + "'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Style:
    def __init__(self, name, indent=2, strings="plain", keys="plain", seq_indent=0, comments=False, blank=False, flow_all=False, flow_paths=(), docstart=False):
        self.name, self.indent, self.strings, self.keys, self.seq_indent = name, indent, strings, keys, seq_indent
        self.comments, self.blank, self.flow_all, self.flow_paths, self.docstart = comments, blank, flow_all, set(flow_paths), docstart


STYLES = [
    Style("block-2"), Style("block-4", indent=4), Style("block-single", strings="single"), Style("block-double", strings="double"),
    Style("block-seq-indented", seq_indent=2), Style("block-comments", comments=True), Style("block-blank-lines", blank=True),
    Style("block-quoted-keys", keys="double", strings="single"), Style("flow-all", flow_all=True, strings="double"),
    Style("mixed-flow-release-provenance", flow_paths=[("release",), ("provenance",)], strings="double"),
    Style("block-docstart", docstart=True), Style("block-flow-files", flow_paths=[("artifacts", "files")], strings="single"),
]


def scalar_text(v, style, flow):
    if isinstance(v, Raw):
        return str(v)
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return Raw(repr(v)) and repr(v)
    if isinstance(v, str):
        if style.strings == "plain" and plain_ok(v, flow):
            return v
        return quote(v, "single" if style.strings == "single" else "double")
    raise TypeError(type(v))


def key_text(k, style):
    if style.keys == "double" or not (k and all(c in IDENT for c in k)):
        return quote(k, "double")
    return k


def flow_text(v, style, extras, path):
    if isinstance(v, dict):
        items = [key_text(k, style) + ": " + flow_text(x, style, extras, path + (k,)) for k, x in v.items()]
        items += [rk + ": " + rv for rk, rv in extras.get(path, [])]
        return "{" + ", ".join(items) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(flow_text(x, style, extras, path + (i,)) for i, x in enumerate(v)) + "]"
    if isinstance(v, Block):
        raise Unrepresentable("block scalar in flow")
    return scalar_text(v, style, True)


def block_lines(v, col, style, extras, path):
    """The lines of a mapping value `v` written at column `col`."""
    pad = " " * col
    out = []
    items = [(k, x) for k, x in v.items()]
    for n, (k, x) in enumerate(items):
        kt = key_text(k, style)
        sub = path + (k,)
        if style.blank and n and not path:
            out.append("")
        if isinstance(x, Block):
            out.append(pad + kt + ": " + x.indicator)
            out += [pad + " " * style.indent + line for line in x.text.split("\n")]
        elif (style.flow_all or sub in style.flow_paths) and isinstance(x, (dict, list)):
            out.append(pad + kt + ": " + flow_text(x, style, extras, sub))
        elif isinstance(x, dict) and x:
            out.append(pad + kt + ":")
            out += block_lines(x, col + style.indent, style, extras, sub)
        elif isinstance(x, list) and x:
            out.append(pad + kt + ":")
            out += list_lines(x, col + style.seq_indent, style, extras, sub)
        elif isinstance(x, dict):
            out.append(pad + kt + ": {}")
        elif isinstance(x, list):
            out.append(pad + kt + ": []")
        else:
            line = pad + kt + ":" + ("" if isinstance(x, Raw) and str(x) == "" else " " + scalar_text(x, style, False))
            if style.comments and n % 2 == 0:
                line += "  # the value of " + k.replace("\n", " ") if all(32 <= ord(c) < 127 for c in k) else ""
            out.append(line)
    for rk, rv in extras.get(path, []):
        if rk == "RAW":
            out += [pad + line for line in rv.split("\n")]
        else:
            out.append(pad + rk + ": " + rv)
    return out


def list_lines(v, col, style, extras, path):
    pad = " " * col
    out = []
    for i, x in enumerate(v):
        sub = path + (i,)
        if isinstance(x, dict) and x and not (style.flow_all or sub in style.flow_paths):
            inner = block_lines(x, col + 2, style, extras, sub)
            inner[0] = pad + "- " + inner[0].lstrip(" ")
            out += inner
        elif isinstance(x, (dict, list)):
            out.append(pad + "- " + flow_text(x, style, extras, sub))
        else:
            out.append(pad + "- " + scalar_text(x, style, False))
    return out


def emit(record, style, extras=None, header=""):
    """-> text. extras: {path: [(raw key, raw value)]} appended to the mapping at that path (or ("RAW", lines))."""
    extras = extras or {}
    lines = []
    if style.comments:
        lines.append("# a release record, written for the raw YAML differential")
    if style.docstart:
        lines.append("---")
    if style.flow_all:
        lines.append(flow_text(record, style, extras, ()))
    else:
        lines += block_lines(record, 0, style, extras, ())
    return header + "\n".join(lines) + "\n"


# ---- the cases -----------------------------------------------------------------------------------------------------------------------------------------------------

class Cases:
    def __init__(self):
        self.rows, self.undetermined, self.skipped = [], [], []
        self.counter = {}

    def add(self, class_, data, parse_expect, allowed, mapping_expect=None, pin=None, why=None):
        n = self.counter[class_] = self.counter.get(class_, 0) + 1
        id_ = "%s-%05d" % (class_.replace(":", "-"), n)
        if parse_expect.get("result") == "refuse" and mapping_expect is not None:
            raise AssertionError("a refused text has no mapping expectation: " + id_)
        if allowed == "undetermined" and parse_expect["result"] == "undetermined":
            self.undetermined.append({"id": id_, "construction": class_, "why": why or "the documents decide nothing about it"})
        self.rows.append({"id": id_, "class": class_, "bytes_hex": data.hex(), "parse_expect": parse_expect, "allowed": allowed, "mapping_expect": mapping_expect, "pin": pin})


def mapping_of(expect, refuse, field):
    return {"expect": expect, "refuse": refuse, "field": field}


def accept(tree):
    return {"result": "accept", "tree": typed(tree)}


def refuse(cls):
    return {"result": "refuse", "class": cls}


UNDETERMINED = {"result": "undetermined"}


def base_record():
    return copy.deepcopy(g1.shared.REAL_RECORD), dict(g1.shared.REAL_PIN)


def has_float(v):
    if isinstance(v, float):
        return True
    if isinstance(v, dict):
        return any(has_float(x) for x in v.values())
    if isinstance(v, list):
        return any(has_float(x) for x in v)
    return False


def wrap_floats(v):
    if isinstance(v, float):
        return Raw(repr(v))
    if isinstance(v, dict):
        return {k: wrap_floats(x) for k, x in v.items()}
    if isinstance(v, list):
        return [wrap_floats(x) for x in v]
    return v


def build_valid(cs, rng):
    """Valid records, in every style: parse accept, allowed true, the mapping of Unit 1's yardstick."""
    valid = [c for c in g1.generate(DEFAULT_SEED) if c["class"] == "valid" or c["class"] == "base"]
    rng.shuffle(valid)
    for c in valid[: PARAMS["valid_records"]]:
        for st in STYLES:
            try:
                text = emit(c["record"], st)
            except Unrepresentable:
                cs.skipped.append((c["id"], st.name))
                continue
            cs.add("built:" + st.name, text.encode(), accept(c["record"]), True, mapping_of(c["expect"], None, None), c["pin"])


def build_tree_level(cs):
    """Every record case of Unit 1 (defects and pairs) as YAML: the text is valid, the mapping judges the value. A float is written plain, which the subset refuses."""
    for c in g1.generate(DEFAULT_SEED):
        if c["kind"] != "map" or c["class"] in ("valid", "base"):
            continue
        for st in (STYLES[0], STYLES[8]):
            rec = c["record"]
            floats = has_float(rec)
            try:
                text = emit(wrap_floats(rec) if floats else rec, st)
            except Unrepresentable:
                cs.skipped.append((c["id"], st.name))
                continue
            if floats:
                cs.add("tree-float:" + st.name, text.encode(), refuse("syntax"), False)
            else:
                cs.add("tree:" + st.name, text.encode(), accept(rec), True, mapping_of(None, c["refuse"], c["field"]), c["pin"])


DUP_PATHS = [(), ("release",), ("provenance",), ("artifacts",), ("artifacts", "files", 0), ("artifacts", "files", 1), ("artifacts", "files", 2)]
SYNTAX_MEMBERS = [("x", "&a 1"), ("y", "*a"), ("z", "!!str 1"), ("z", "!!int 1"), ("<<", "{}"), ("w", "&b [1]"), ("v", "!custom x")]
RAW_NONSCALAR_KEY = ("RAW", "? [a]\n: b")
FLOATISH = ["3.0", "3e0", ".5", "5.", "1e3", ".inf", ".nan", "0x3", "0o3", "03", "99999999999999999999", "+3.0", "-.inf"]


def first_key(record, path):
    cur = record
    for p in path:
        cur = cur[p]
    return next(iter(cur))


def dup_extras(record, path, spelling, other):
    k = first_key(record, path)
    cur = record
    for p in path:
        cur = cur[p]
    val = cur[k]
    if isinstance(val, (dict, list)):
        scalars = [kk for kk, vv in cur.items() if not isinstance(vv, (dict, list))]
        if scalars:
            k, val = scalars[0], cur[scalars[0]]
    text = {"plain": k, "double": '"%s"' % k, "single": "'%s'" % k}[spelling]
    if isinstance(val, list):
        raw = "[]"
    elif isinstance(val, dict):
        raw = "{}"
    else:
        raw = "null" if val is None else ("true" if val is True else ("false" if val is False else (str(val) if isinstance(val, int) else '"%s"' % val)))
    return {path: [(text, raw if not other else '"other"')]}


def build_text_level(cs, rng):
    record, pin = base_record()
    exp = g1.shared.REAL_EXPECT
    base = STYLES[0]
    # duplicate keys, everywhere they can be, in three spellings, with the same and another value, in block and flow
    for path in DUP_PATHS:
        for spelling in ("plain", "double", "single"):
            for other in (False, True):
                ex = dup_extras(record, path, spelling, other)
                cs.add("dup", emit(record, base, ex).encode(), refuse("duplicate_key"), False)
                if path in ((), ("release",), ("provenance",), ("artifacts", "files", 0)):
                    fl = Style("flow", flow_paths=[path] if path else (), flow_all=not path, strings="double")
                    cs.add("dup-flow", emit(record, fl, ex).encode(), refuse("duplicate_key"), False)
    # a duplicate in a member the mapping does not read
    cs.add("dup-unread", emit(record, base, {(): [("note", '"a"'), ("note", '"b"')]}).encode(), refuse("duplicate_key"), False)
    # syntax injections at three places
    for path in ((), ("release",), ("provenance",)):
        for k, v in SYNTAX_MEMBERS:
            cs.add("syntax-injected", emit(record, base, {path: [(k, v)]}).encode(), refuse("syntax"), False)
        cs.add("syntax-nonscalar-key", emit(record, base, {path: [RAW_NONSCALAR_KEY]}).encode(), refuse("syntax"), False)
    # plain scalars the subset refuses, at the two envelopes and at a digest
    for who in ("release", "provenance"):
        for f in FLOATISH:
            r = copy.deepcopy(record)
            r[who]["envelope"] = Raw(f)
            cs.add("syntax-scalar", emit(r, base).encode(), refuse("syntax"), False)
    # text-level defects the subset names
    text = emit(record, base)
    defects = {
        "tab-after-value": text.replace("envelope: 3", "envelope: 3\t", 1),
        "tab-indent": text.replace("  envelope", "\tenvelope", 1),
        "nul": "# \x00\n" + text,
        "ctrl-01": "# \x01\n" + text,
        "ctrl-0b": "# \x0b\n" + text,
        "ctrl-7f": "# \x7f\n" + text,
        "nel-85": "# \u0085\n" + text,
        "ls-2028": "# \u2028\n" + text,
        "ps-2029": "# \u2029\n" + text,
        "bom-inside": "# \ufeff\n" + text,
        "directive": "%YAML 1.2\n---\n" + text,
        "document-end": text + "...\n",
        "second-marker": "\n".join(text.split("\n")[:3] + ["---"] + text.split("\n")[3:]),
        "second-document": text + "---\n" + text,
    }
    for name, t in defects.items():
        cs.add("syntax-text:" + name, t.encode("utf-8"), refuse("syntax"), False)
    # limits: clearly over (the exact boundary is undetermined, below)
    cs.add("limit-over:size", (text + "#" * ((1 << 20) + 64) + "\n").encode(), refuse("syntax"), False)
    cs.add("limit-over:depth", (text + "deep:\n" + "".join(" " * (2 * (i + 1)) + "d:\n" for i in range(200)) + " " * 402 + "e: 1\n").encode(), refuse("syntax"), False)
    cs.add("limit-over:nodes", (text + "wide: [" + ",".join("0" for _ in range(150000)) + "]\n").encode(), refuse("syntax"), False)
    # value variations: valid text, the mapping judges
    wrong = lambda who: mapping_of(None, "envelope_not_3", who + ".envelope")
    variants = [("3", None), ("+3", None), ('"3"', "s"), ("'3'", "s"), ("null", "x"), ("~", "x"), ("", "x"), ("true", "x"), ("True", "x"), ("FALSE", "x"), ("-3", "x"), ("2", "x"), ("4", "x")]
    for who in ("release", "provenance"):
        for raw, kind in variants:
            r = copy.deepcopy(record)
            r[who]["envelope"] = Raw(raw)
            ok = kind is None
            if kind == "s":
                t = emit(r, base)
                tree = copy.deepcopy(record)
                tree[who]["envelope"] = "3"
                cs.add("value:envelope", t.encode(), accept(tree), True, wrong(who), pin)
                continue
            tree = r
            cs.add("value:envelope", emit(r, base).encode(), accept(tree), True, mapping_of(exp, None, None) if ok else wrong(who), pin)
    # a digest written as a block scalar
    for who, member in (("release", "build_hash"), ("provenance", "grammar_sha256")):
        value = record[who][member]
        for ind in ("|-", ">-", "|", ">"):
            r = copy.deepcopy(record)
            r[who][member] = Block(ind, value)
            strip = ind.endswith("-")
            cs.add("value:block-digest", emit(r, base).encode(), accept(r), True,
                   mapping_of(exp, None, None) if strip else mapping_of(None, "base64_shape", "%s.%s" % (who, member)), pin)
    # undetermined: the documents decide nothing about these constructions
    und = {
        "missing-final-newline": text.rstrip("\n").encode(),
        "crlf": text.replace("\n", "\r\n").encode(),
        "lone-cr": text.replace("\n", "\r").encode(),
        "leading-bom": b"\xef\xbb\xbf" + text.encode(),
        "nbsp-in-comment": ("# \u00a0\n" + text).encode(),
        "zero-width-in-comment": ("# \u200b\n" + text).encode(),
        "utf16-le-bom": b"\xff\xfe" + text.encode("utf-16-le"),
        "utf16-be-bom": b"\xfe\xff" + text.encode("utf-16-be"),
        "invalid-utf8-in-comment": b"# \xff\xfe\n" + text.encode(),
        "overlong-utf8-in-comment": b"# \xc0\x80\n" + text.encode(),
        "empty-document": b"",
        "only-a-comment": b"# nothing\n",
    }
    for name, data in und.items():
        cs.add("undetermined:" + name, data, UNDETERMINED, "undetermined", why="no rule in docs/P1-SUPPORTED-SUBSET.md decides it")
    for raw in ("Null", "NULL", "yes", "no", "on", "off", "y", "n", "Yes", "NO"):
        r = copy.deepcopy(record)
        r["release"]["envelope"] = Raw(raw)
        cs.add("undetermined:plain-" + raw.lower(), emit(r, base).encode(), UNDETERMINED, "undetermined", why="a plain spelling YAML 1.1 reads as null or a boolean; the subset says every other plain scalar is a string, the documents do not list this one")
    cs.add("undetermined:size-at-limit", (text + "#" * ((1 << 20) - len(text.encode()) - 1) + "\n").encode(), UNDETERMINED, "undetermined", why="exactly 1 MiB: the limit text says 'more than 1 MiB'")
    cs.add("undetermined:depth-64", (text + "".join(" " * (2 * i) + "d:\n" for i in range(64)) + " " * 128 + "e: 1\n").encode(), UNDETERMINED, "undetermined", why="depth 64: the table lists 'depth 64' without saying whether the limit is at or above it")
    # pairs: one duplicate key and one syntax defect; the documents fix which refusal wins only through 'a syntax error anywhere wins', so either refusal is accepted
    for _ in range(PARAMS["pairs"]):
        path = rng.choice(DUP_PATHS)
        ex = dup_extras(record, path, rng.choice(["plain", "double", "single"]), rng.random() < 0.5)
        k, v = rng.choice(SYNTAX_MEMBERS)
        ex.setdefault((), []).append((k, v))
        cs.add("pair", emit(record, base, ex).encode(), refuse("any"), False)


def build_pinned(cs):
    """The inputs the Rust and Go parsers are known to read differently (testdata/p1/parity-known.txt): they are required to be outside P_parity, hence not admitted."""
    if not os.path.exists(KNOWN_TXT):
        return
    with open(KNOWN_TXT, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cs.add("pinned", bytes.fromhex(line.split()[0]), {"result": "pinned_divergence"}, "undetermined", why="a pinned Rust/Go parser divergence: its allowed label is set independently")


def generate(seed=DEFAULT_SEED):
    rng = random.Random(seed)
    cs = Cases()
    build_valid(cs, rng)
    build_tree_level(cs)
    build_text_level(cs, rng)
    build_pinned(cs)
    return cs


def listing(cs):
    """The committed list: the constructions the documents do not decide, and the Unit 1 record cases that cannot be written as plain text by this emitter (a newline or another
    non-printable character in a string would need an escape, and whether the parsers support the escape is not documented)."""
    return {"undetermined": cs.undetermined, "skipped_unrepresentable": sorted([list(x) for x in cs.skipped])}


def render(rows):
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in rows)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out")
    ap.add_argument("--undetermined-out")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    cs = generate(a.seed)
    text = render(cs.rows)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    if a.undetermined_out:
        open(a.undetermined_out, "w", encoding="utf-8").write(json.dumps(listing(cs), indent=1, sort_keys=True) + "\n")
    if a.summary or not a.out:
        by = {}
        for r in cs.rows:
            by[r["class"].split(":")[0]] = by.get(r["class"].split(":")[0], 0) + 1
        print("seed %d: %d cases, %d bytes, sha256 %s, %d undetermined, %d skipped as unrepresentable" % (a.seed, len(cs.rows), len(text), hashlib.sha256(text.encode()).hexdigest(), len(cs.undetermined), len(cs.skipped)), file=sys.stderr)
        print("  " + ", ".join("%s %d" % kv for kv in sorted(by.items())), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
