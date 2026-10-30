#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the inputs of the canonical-bytes differential (P5-1; rust/src/canonical.rs and go/canonical.go, engine section A).

    gen_canonical.py OUT_JSONL [--count N] [--seed S]

Deterministic. Each line is {"id", "tag", "spec", "want"}: a value in the spec form below, the family it comes from, and what a reference written here (and nowhere
near either implementation) says it is: ["ok", canonical bytes, digest] or ["err", reason]. Both languages build the value from the spec and have to answer exactly
that, and each other.

The spec form carries what JSON cannot: null, a bool and a string are themselves; {"i": "123"} is an integer (as a decimal string, so no language rounds it);
{"u": "1.5"} is a value this library does not hold (a float or an integer beyond 64 bits); a list is a sequence; {"m": [[key, value], ...]} is a map with its entries
in the given order, a key twice included.

The reference refuses the first fault in the order the writers meet it: a sequence in its order, a map by its repeated key (checked before any member is written),
then its members in the order of the sorted keys, the key before the value. An integer outside 64 bits in "i" is a fault of the generator and is not produced.
Families: strings (escapes, controls, the five characters of A4, non-ASCII, composed and decomposed), integers (the ends of the range), keys (byte order against
code-point order, multibyte, escaped), nesting, a value this library does not hold at a random place, a repeated key at a random place.
"""
import argparse
import hashlib
import json
import random

I64_MIN, I64_MAX = -(2**63), 2**63 - 1

# 0x7f, the pair of a surrogate and the separators are here on purpose: a character the writer must leave alone, and the five it must not.
CHARS = ["a", "Z", "0", " ", "\"", "\\", "/", "\b", "\t", "\n", "\f", "\r", "\x00", "\x01", "\x1f", "\x7f", "<", ">", "&", "\u2028", "\u2029",
         "é", "é", "ü", "€", "\U0001f600", "ÿ", "�", "\u0080", "߿", "ࠀ", "￿", "'", "-", "_"]
KEYS = ["a", "b", "z", "B", "A", "_", "-", "0", "10", "9", "a<b", "é", "ÿ", "€", "\U0001f600", "\u2028", "", "a b", "a\"b", "a\\b", "\x00", "\x7f",
        "￿", "ﬁ", "é́"]


def esc(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch in "\b\t\n\f\r":
            out.append({"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}[ch])
        elif o < 0x20 or ch in "<>&\u2028\u2029":
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


class Refused(Exception):
    pass


def ref(spec):
    """The canonical text of a spec, or Refused(reason). Written from section A, not from either implementation."""
    if spec is None:
        return "null"
    if spec is True:
        return "true"
    if spec is False:
        return "false"
    if isinstance(spec, str):
        return esc(spec)
    if isinstance(spec, list):
        return "[" + ",".join(ref(x) for x in spec) + "]"
    if "i" in spec:
        return str(int(spec["i"]))
    if "u" in spec:
        raise Refused("unsupported_value")
    entries = spec["m"]
    keys = [k for k, _ in entries]
    if len(set(keys)) != len(keys):
        raise Refused("duplicate_key")
    return "{" + ",".join(esc(k) + ":" + ref(v) for k, v in sorted(entries, key=lambda e: e[0].encode("utf-8"))) + "}"


def want(spec):
    try:
        text = ref(spec)
    except Refused as r:
        return ["err", str(r)]
    return ["ok", text, "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()]


def string(rng, n=None):
    return "".join(rng.choice(CHARS) for _ in range(rng.randint(0, 6) if n is None else n))


def integer(rng):
    return {"i": str(rng.choice([0, 1, -1, 7, 10, 255, 256, 65535, 2**31, -(2**31), 2**53, 2**53 + 1, -(2**53) - 1, I64_MAX, I64_MIN, I64_MAX - 1, I64_MIN + 1,
                                  rng.randint(I64_MIN, I64_MAX), rng.randint(-1000, 1000)]))}


def scalar(rng):
    k = rng.random()
    if k < 0.12:
        return None
    if k < 0.25:
        return rng.random() < 0.5
    if k < 0.5:
        return integer(rng)
    return string(rng)


def tree(rng, depth, keys_from_chars=False):
    if depth == 0 or rng.random() < 0.3:
        return scalar(rng)
    if rng.random() < 0.4:
        return [tree(rng, depth - 1) for _ in range(rng.randint(0, 4))]
    ks = set()
    while len(ks) < rng.randint(0, 5):
        ks.add(string(rng, rng.randint(0, 3)) if rng.random() < 0.3 else rng.choice(KEYS))
    ks = sorted(ks, key=lambda k: k.encode("utf-8"))  # a set is walked in a hash-seeded order: the same seed must give the same file
    rng.shuffle(ks)
    return {"m": [[k, tree(rng, depth - 1)] for k in ks]}


def paths(spec, here=()):
    """Every position as a path of indexes into lists and into the entries of a map."""
    yield here
    if isinstance(spec, list):
        for i, x in enumerate(spec):
            yield from paths(x, here + (i,))
    elif isinstance(spec, dict) and "m" in spec:
        for i, (_, v) in enumerate(spec["m"]):
            yield from paths(v, here + (i,))


def replace_at(spec, path, new):
    if not path:
        return new
    i, rest = path[0], path[1:]
    if isinstance(spec, list):
        return spec[:i] + [replace_at(spec[i], rest, new)] + spec[i + 1:]
    m = spec["m"]
    return {"m": m[:i] + [[m[i][0], replace_at(m[i][1], rest, new)]] + m[i + 1:]}


def maps_at(spec):
    return [p for p in paths(spec) if _at(spec, p) is not None and isinstance(_at(spec, p), dict) and "m" in _at(spec, p)]


def _at(spec, path):
    for i in path:
        spec = spec[i] if isinstance(spec, list) else spec["m"][i][1]
    return spec


def unsupported(rng):
    return {"u": rng.choice(["1.5", "-0.0", "1e100", "9223372036854775808", "-9223372036854775809", ".inf", "0.1"])}


def make(rng, tag):
    if tag == "strings":
        return string(rng, rng.randint(0, 12))
    if tag == "integers":
        return {"m": [[k, integer(rng)] for k in rng.sample(KEYS, 3)]}
    if tag == "keys":
        ks = rng.sample(KEYS, rng.randint(1, 8))
        return {"m": [[k, scalar(rng)] for k in ks]}
    if tag == "nested":
        return tree(rng, rng.randint(1, 5))
    if tag == "unsupported":
        t = tree(rng, rng.randint(1, 4))
        return replace_at(t, rng.choice(list(paths(t))), unsupported(rng))
    if tag == "duplicate":
        t = tree(rng, rng.randint(1, 4))
        ms = maps_at(t)
        if not ms:
            return {"m": [["k", None], ["k", True]]}
        p = rng.choice(ms)
        m = _at(t, p)["m"]
        entries = m + [[rng.choice(m)[0] if m else "k", scalar(rng)]] if m else [["k", None], ["k", True]]
        rng.shuffle(entries)
        return replace_at(t, p, {"m": entries})
    if tag == "both":
        # a duplicate key and a value this library does not hold, in places that decide which fault is met first
        t = tree(rng, rng.randint(2, 4))
        t = replace_at(t, rng.choice(list(paths(t))), unsupported(rng))
        ms = maps_at(t)
        if ms:
            p = rng.choice(ms)
            m = _at(t, p)["m"]
            if m:
                t = replace_at(t, p, {"m": m + [[m[0][0], None]]})
        return t
    raise ValueError(tag)


TAGS = [("strings", 4), ("integers", 1), ("keys", 3), ("nested", 5), ("unsupported", 2), ("duplicate", 2), ("both", 2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20261030)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    tags = [t for t, w in TAGS for _ in range(w)]
    with open(a.out, "w", encoding="utf-8") as f:
        for i in range(a.count):
            tag = tags[i % len(tags)]
            spec = make(rng, tag)
            f.write(json.dumps({"id": i, "tag": tag, "spec": spec, "want": want(spec)}, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
