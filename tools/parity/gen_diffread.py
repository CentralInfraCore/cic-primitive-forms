#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the ReadDiff differential (ADR 0008 bytes, read by rust/src/diffread.rs and go/diffread.go; P4, after #107).

    gen_diffread.py OUT_JSONL [--count N] [--seed S]

Deterministic: the same seed gives the same file. Each line is {"id": n, "tag": "...", "hex": "..."}: the bytes handed to the reader, hex encoded, and the family
they come from (the comparison reports by family). The reader has to answer alike in both languages for every line: the same reason and the same offset, or the
same diff written back.

Where the inputs come from, and why not bytes alone: a byte flip in a canonical diff is a syntax error in six cases of ten and never reaches the checks that matter
(order, a path twice, an event that changes nothing, the shape of an event, the depth). So the lines are, in this order of weight:
  * the canonical strings of the corpus as they are;
  * diffs composed here from the events of the corpus (their real shapes and values) under paths drawn here, sorted the way ADR 0008 sorts, which are valid;
  * the same diffs after ONE edit of the structure (an event twice, two events swapped, a path twice with another kind, previous equal to next, a key dropped, added
    or renamed, a type tag changed, a version of another form, an entry of `representation` out of order or between equal forms), written canonically again so that
    the reader reaches the check the edit is about;
  * the same after one edit of the writing (white space, the order of the keys, a needless escape, `\\/`, a BOM, a trailing newline, text after the end);
  * numbers in every form the contract refuses or accepts (-0, 01, 1.0, 1e2, +1, 2^63, -2^63 - 1);
  * nesting past the depth the reader takes, in a value and in the document;
  * strings: escapes, lone surrogates, a pair, a control character, overlong and invalid UTF-8;
  * byte edits of any of the above (flip, delete, insert, truncate, copy, swap).
"""
import argparse
import copy
import glob
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(HERE, ".."))
import corpus_check as cc  # noqa: E402  (the order of ADR 0008 and the parse of a path: one definition)
import yaml  # noqa: E402


def canon(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def corpus_diffs():
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, "corpus", "*", "vectors.yaml"))):
        with open(f, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        for c in doc["cases"]:
            e = c.get("expect")
            if c["kind"] in ("diff", "diff_stability") and isinstance(e, dict) and "canonical" in e:
                out.append(e["canonical"])
            if c["kind"] == "patch" and isinstance(c.get("diff"), dict) and "canonical" in c["diff"]:
                out.append(c["diff"]["canonical"])
    return sorted(set(out))


SEGMENTS = ["a", "b", "ip", "ports", "routes", "tags", "nics", "x1", "name", "é", "ü"]


def draw_path(r):
    parts = ["$"]
    for _ in range(r.choice([1, 1, 2, 2, 3])):
        parts.append("." + r.choice(SEGMENTS))
        if r.random() < 0.25:
            if r.random() < 0.5:
                parts.append("[{k=%s}]" % json.dumps(r.choice(["x", "y", "a b", "é", "q\\\"r"]), ensure_ascii=False))
            else:
                parts.append("[{n=%d}]" % r.choice([0, 1, 7, -3, 12]))
    return "".join(parts)


def valid_path(p):
    try:
        cc.parse_logical_path(p)
        return True
    except ValueError:
        return False


def order_key(p):
    return cc.path_order_key(cc.parse_logical_path(p))


def compose(r, pool, nevents):
    """A valid diff: events from the pool under drawn paths, one per (path, kind), in the order of ADR 0008."""
    events, seen = [], set()
    for _ in range(nevents * 3):
        if len(events) == nevents:
            break
        e = copy.deepcopy(r.choice(pool))
        e["path"] = draw_path(r)
        if not valid_path(e["path"]) or (e["path"], e["kind"]) in seen:
            continue
        seen.add((e["path"], e["kind"]))
        events.append(e)
    events.sort(key=lambda e: (order_key(e["path"]), e["kind"].encode()))
    reprs = {}
    for _ in range(r.choice([0, 0, 1, 2])):
        p = draw_path(r)
        if valid_path(p):
            prev = r.choice(["short", "expanded"])
            reprs[p] = {"kind": "representation_change", "path": p, "previous": prev, "next": "expanded" if prev == "short" else "short"}
    return {"version": 1, "changes": events, "representation": sorted(reprs.values(), key=lambda x: order_key(x["path"]))}


def structure_edit(r, d, pool):
    """One edit of the structure of a diff, as a document: the reader is meant to meet the check this edit is about."""
    d = copy.deepcopy(d)
    ch, rp = d["changes"], d["representation"]
    ops = ["dup_event", "swap", "same_path_other_kind", "no_change", "drop_key", "add_key", "rename_key", "tag", "version", "rp_swap", "rp_same_form",
           "rp_dup", "rp_path", "path_noncanon", "root_path", "kind", "empty_kind", "top_key", "top_drop", "null_value", "set_dup", "set_overlap", "set_order"]
    op = r.choice(ops)
    if op == "dup_event" and ch:
        i = r.randrange(len(ch)); ch.insert(i, copy.deepcopy(ch[i]))
    elif op == "swap" and len(ch) > 1:
        i = r.randrange(len(ch) - 1); ch[i], ch[i + 1] = ch[i + 1], ch[i]
    elif op == "same_path_other_kind" and ch:
        e = copy.deepcopy(r.choice(pool)); e["path"] = r.choice(ch)["path"]; ch.append(e)
        ch.sort(key=lambda x: (order_key(x["path"]), x["kind"].encode()))
    elif op == "no_change":
        vc = [e for e in ch if e["kind"] == "value_change"]
        if vc:
            e = r.choice(vc); e["next"] = copy.deepcopy(e["previous"])
        else:
            ch.append({"kind": "value_change", "path": draw_path(r), "previous": 1, "next": 1})
    elif op == "drop_key" and ch:
        e = r.choice(ch); k = r.choice(sorted(e)); del e[k]
    elif op == "add_key" and ch:
        r.choice(ch)[r.choice(["extra", "Kind", "previous", "next", "added"])] = r.choice([1, "x", None, [], {}])
    elif op == "rename_key" and ch:
        e = r.choice(ch); k = r.choice(sorted(e)); e[k + "_"] = e.pop(k)
    elif op == "tag" and ch:
        e = r.choice(ch)
        for k in ("previous", "next", "added", "removed"):
            if k in e:
                e[k] = r.choice([1, "x", True, None, [], {}, [1, "a"], 1.5]); break
    elif op == "version":
        d["version"] = r.choice([0, 2, -1, "1", True, None, [1], 2 ** 63 - 1, 2 ** 63, 1.0, 100])
    elif op == "rp_swap" and len(rp) > 1:
        rp[0], rp[1] = rp[1], rp[0]
    elif op == "rp_same_form":
        p = draw_path(r)
        if valid_path(p):
            rp.append({"kind": "representation_change", "path": p, "previous": "short", "next": "short"})
    elif op == "rp_dup" and rp:
        rp.append(copy.deepcopy(rp[0]))
    elif op == "rp_path":
        rp.append({"kind": "representation_change", "path": r.choice(["$", "a", "$.", "$.a.", "$[0]", "$.a[{k=1}]", ""]), "previous": "short", "next": "expanded"})
    elif op == "path_noncanon" and ch:
        ch[r.randrange(len(ch))]["path"] = r.choice(["$.a[{n=01}]", "$.a[{n=+7}]", "$.a[{n=-0}]", "$.a[{k=\"\\u0061\"}]", "$.a[{k=\"a\\/b\"}]", "$.a.b ", " $.a", "$.a..b", "$.a[{}]", "$.a[]"])
    elif op == "root_path" and ch:
        ch[r.randrange(len(ch))]["path"] = "$"
    elif op == "kind" and ch:
        ch[r.randrange(len(ch))]["kind"] = r.choice(["Added", "change", "representation_change", "", "value_change "])
    elif op == "empty_kind":
        ch.append({"kind": "", "path": draw_path(r)})
    elif op == "top_key":
        d[r.choice(["extra", "Changes", "version "])] = r.choice([1, [], {}, None])
    elif op == "top_drop":
        del d[r.choice(sorted(d))]
    elif op == "null_value":
        d[r.choice(["changes", "representation"])] = None
    elif op in ("set_dup", "set_overlap", "set_order"):
        sc = [e for e in ch if e["kind"] == "set_change" and isinstance(e.get("added"), list)]
        if sc:
            e = r.choice(sc)
            if op == "set_dup":
                e["added"] = [1, 1]
            elif op == "set_overlap":
                e["added"], e["removed"] = [2], [2]
            else:
                e["added"] = [3, 1]
    return d


def write_edit(r, text):
    """One edit of the writing of a canonical diff."""
    b = text.encode("utf-8")
    ops = ["space", "newline", "pretty", "keyorder", "escape", "slash", "bom", "trailing", "after", "upper_hex", "tab", "colon_space"]
    op = r.choice(ops)
    if op == "space":
        p = r.randrange(len(text) + 1); return (text[:p] + " " + text[p:]).encode()
    if op == "newline":
        return b + b"\n"
    if op == "pretty":
        return json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False).encode()
    if op == "keyorder":
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False).encode()  # insertion order: the reader's own order may differ
    if op == "escape":
        return text.replace("a", "\\u0061", 1).encode()
    if op == "slash":
        return text.replace("$", "\\/", 1).encode() if r.random() < 0.5 else text.replace("$.", "$\\u002e", 1).encode()
    if op == "bom":
        return b"\xef\xbb\xbf" + b
    if op == "trailing":
        return b + r.choice([b" ", b"\t", b"\r\n", b"\x00"])
    if op == "after":
        return b + r.choice([b"{}", b"x", b",", b"]"])
    if op == "upper_hex":
        return text.replace("\\u00", "\\u00", 1).replace("é", "\\u00E9", 1).encode()
    if op == "tab":
        return text.replace(",", ",\t", 1).encode()
    return text.replace(":", ": ", 1).encode()


def number_cases(r):
    forms = ["-0", "01", "00", "1.0", "1e2", "1E2", "+1", "0x10", "9223372036854775807", "9223372036854775808", "-9223372036854775808", "-9223372036854775809",
             "1.5", "-1.5", "0.0", "1e999", "NaN", "Infinity", "-", "1.", ".5", "00.5", "12345678901234567890"]
    out = []
    for f in forms:
        for tpl in ('{"changes":[{"kind":"value_change","next":%s,"path":"$.a","previous":1}],"representation":[],"version":1}',
                    '{"changes":[{"kind":"value_change","next":2,"path":"$.a[{n=%s}]","previous":1}],"representation":[],"version":1}',
                    '{"changes":[],"representation":[],"version":%s}',
                    '{"changes":[{"kind":"set_change","added":[%s],"path":"$.a","removed":[]}],"representation":[],"version":1}'):
            out.append((tpl % f).encode())
    return out


def depth_cases(r):
    out = []
    for n in (1, 30, 62, 63, 64, 65, 66, 70, 200, 5000):
        arr = "[" * n + "]" * n
        obj = '{"a":' * n + "1" + "}" * n
        out.append(('{"changes":[{"kind":"value_change","next":%s,"path":"$.a","previous":1}],"representation":[],"version":1}' % arr).encode())
        out.append(('{"changes":[{"kind":"value_change","next":%s,"path":"$.a","previous":1}],"representation":[],"version":1}' % obj).encode())
        out.append((arr).encode())
        out.append(('{"changes":' + arr + ',"representation":[],"version":1}').encode())
        out.append(('{"changes":[],"representation":[],"version":1,"x":' + obj + "}").encode())
    return out


def string_cases(r):
    vals = ['"a\\ud800"', '"\\udc00a"', '"\\ud83d\\ude00"', '"\\ud83d"', '"\\u0000"', '"\\u001f"', '"\\u007f"', '"\\u0080"', '"\\"', '"\\x41"', '"a\tb"', '"a\nb"',
            '"\\n\\r\\t\\b\\f"', '"\\u000a"', '"\\u2028"', '"é"', '"\\u00e9"', '"😀"', '"\\ud83d\\ude00\\ud83d\\ude00"', '"\\\\"', '"\\/"', '""', '"' + "x" * 70000 + '"']
    out = []
    for v in vals:
        out.append(('{"changes":[{"kind":"value_change","next":%s,"path":"$.a","previous":"p"}],"representation":[],"version":1}' % v).encode("utf-8"))
    for raw in (b'\xff', b'\xc0\x80', b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'\xe2\x82', b'\xef\xbb\xbf', b'\xef\xbb\xbf\xef\xbb\xbf', b'\x00', b'\xc3\xa9', b'\xf0\x9f\x98\x80'):
        out.append(b'{"changes":[{"kind":"value_change","next":"' + raw + b'","path":"$.a","previous":"p"}],"representation":[],"version":1}')
        out.append(raw)
        out.append(raw + b'{"changes":[],"representation":[],"version":1}')
    return out


def byte_edit(r, b):
    b = bytearray(b)
    if not b:
        return bytes(b)
    k = r.randrange(7)
    p = r.randrange(len(b))
    if k == 0:
        b[p] ^= 1 << r.randrange(8)
    elif k == 1:
        del b[p]
    elif k == 2:
        b.insert(p, r.choice(b'{}[]",:0123456789-.eE\\ \n'))
    elif k == 3:
        b = b[:p]
    elif k == 4:
        q = r.randrange(len(b)); b[p:p] = b[q:q + r.randrange(1, 30)]
    elif k == 5:
        b[p] = r.randrange(256)
    else:
        q = r.randrange(len(b)); b[p], b[q] = b[q], b[p]
    return bytes(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20261029)
    args = ap.parse_args()
    r = random.Random(args.seed)
    seeds = corpus_diffs()
    pool = []
    for s in seeds:
        pool += [e for e in json.loads(s)["changes"] if isinstance(e, dict) and "kind" in e]
    lines = []

    def add(tag, b):
        lines.append((tag, b if isinstance(b, bytes) else b.encode("utf-8")))

    for s in seeds:
        add("corpus", s)
    for b in number_cases(r):
        add("number", b)
    for b in depth_cases(r):
        add("depth", b)
    for b in string_cases(r):
        add("string", b)
    add("empty", b"")
    for raw in (b" ", b"\n", b"null", b"[]", b"{}", b"1", b'"x"', b"{", b"}", b'{"changes":[],"representation":[],"version":1', b'{"changes":[],"representation":[],"version":1}}'):
        add("tiny", raw)
    while len(lines) < args.count:
        roll = r.random()
        if roll < 0.15:
            add("composed", canon(compose(r, pool, r.choice([0, 1, 2, 3, 5, 8]))))
        elif roll < 0.55:
            d = compose(r, pool, r.choice([1, 2, 3, 5]))
            add("structure", canon(structure_edit(r, d, pool)))
        elif roll < 0.70:
            d = compose(r, pool, r.choice([1, 2, 3]))
            add("writing", write_edit(r, canon(d)))
        elif roll < 0.80:
            add("writing_corpus", write_edit(r, r.choice(seeds)))
        else:
            base = r.choice(seeds).encode() if r.random() < 0.5 else canon(structure_edit(r, compose(r, pool, 2), pool)).encode()
            for _ in range(r.choice([1, 1, 2, 3])):
                base = byte_edit(r, base)
            add("bytes", base)
    with open(args.out, "w", encoding="utf-8") as f:
        for i, (tag, b) in enumerate(lines):
            f.write(json.dumps({"id": i, "tag": tag, "hex": b.hex()}) + "\n")
    print("%d inputs from %d corpus diffs and %d pooled events" % (len(lines), len(seeds), len(pool)))


if __name__ == "__main__":
    main()
