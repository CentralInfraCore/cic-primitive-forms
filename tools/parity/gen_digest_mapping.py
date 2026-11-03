#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generated cases for the differential proof of the digest mapping (docs/P5-DIGEST-MAPPING-DIFFERENTIAL-PLAN.md, sections 3 and 4).

    gen_digest_mapping.py [--seed N] [--values N] [--pairs N] [--out FILE] [--summary]

One JSON line per case, in the shape of testdata/p5/digest-mapping.json: {"id", "class", "kind": "convert" | "map", "input" | "record", "pin", "expect", "refuse", "field"}.
`expect` is the receipt digest (a conversion) or the three digests (a mapping); `refuse` is one reason of the contract; `field` is the member the refusal names (a mapping only).

THE EXPECTED ANSWER COMES FROM NONE OF THE THREE IMPLEMENTATIONS. It is fixed by construction, in two ways that do not read any decoder or any mapping code:

 * a base64 text is built from raw bytes by bit arithmetic on 6-bit groups (a SHA-256 is 256 bits: 42 whole groups and a 43rd that holds 4 data bits and 2 zero bits), so the
   verdict of a change is known from the change: a character outside the alphabet is a shape refusal, another alphabet character in a whole group is another 32 bytes, in the
   last group it is the same 32 bytes in another spelling unless its two low bits are zero;
 * a record defect has its reason and field written here by hand, from the contract (docs/P5-DIGEST-MAPPING-CONTRACT.md, section 5), together with the RANK of the check that
   finds it; two defects together have the reason of the earlier rank.

The only borrowed code is the mechanical `mutate` of tools/p5/digest_mapping_cases.py (set / delete / append in a nested dict) and the constants of the record of v0.2.1.
"""
import argparse
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "p5"))
import digest_mapping_cases as shared  # noqa: E402  (the constants of the v0.2.1 record and the mechanical `mutate`)

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
DEFAULT_SEED = 20261103
CHECK_GRAMMAR, GRAMMAR_SCHEMA = shared.CHECK_GRAMMAR, shared.GRAMMAR_SCHEMA


# ---------------------------------------------------------------- base64 by construction (no decoder, no base64 module)

def bits_of(raw):
    return "".join("{:08b}".format(b) for b in raw)


def sextets_of(raw):
    """The 43 groups of a 32-byte value: 42 whole ones and a last that holds 4 data bits followed by 2 zero bits."""
    bits = bits_of(raw) + "00"
    return [int(bits[i:i + 6], 2) for i in range(0, 258, 6)]


def text_of(sextets):
    return "".join(ALPHABET[s] for s in sextets) + "="


def canonical(raw):
    return text_of(sextets_of(raw))


def raw_of(sextets):
    """The 32 bytes of 43 groups whose last group has two zero low bits (the only spelling the contract accepts)."""
    bits = "".join("{:06b}".format(s) for s in sextets)[:256]
    return bytes(int(bits[i:i + 8], 2) for i in range(0, 256, 8))


def digest_of(raw):
    return "sha256:" + raw.hex()


def flip_bit(raw, n):
    b = bytearray(raw)
    b[n // 8] ^= 0x80 >> (n % 8)
    return bytes(b)


# ---------------------------------------------------------------- conversion cases

BAD_CHARS = ["-", "_", " ", "\n", "\r", "\t", "=", ".", "!", chr(0), chr(0xE9), chr(0x2028), "\\", "/ ", "sha256:"]


def conversion_cases(rng, values):
    cases = []

    def add(kind, text, expect, refuse):
        cases.append({"id": "conv-%05d" % len(cases), "class": "b64:" + kind, "kind": "convert", "input": text, "expect": expect, "refuse": refuse, "field": None})

    for _ in range(values):
        raw = bytes(rng.randrange(256) for _ in range(32))
        sx = sextets_of(raw)
        text = text_of(sx)
        add("canonical", text, digest_of(raw), None)
        for i in range(43):                                   # every position: an alphabet character and a bad one
            v = rng.randrange(64)
            changed = sx[:i] + [v] + sx[i + 1:]
            if i == 42 and v & 3:
                add("last-group-trailing-bits", text_of(changed), None, "base64_not_canonical")
            else:
                add("one-group", text_of(changed), digest_of(raw_of(changed)), None)
            bad = rng.choice(BAD_CHARS)
            add("bad-character", text[:i] + bad + text[i + 1:], None, "base64_shape")
        for v in range(1, 4):                                 # the other spellings of the same 32 bytes
            changed = sx[:42] + [sx[42] | v]
            add("last-group-trailing-bits", text_of(changed), None, "base64_not_canonical")
        add("padding-replaced", text[:43] + rng.choice(ALPHABET), None, "base64_shape")
        add("padding-missing", text[:43], None, "base64_shape")
        add("padding-doubled", text[:43] + "==", None, "base64_shape")
        add("final-newline", text + "\n", None, "base64_shape")
        add("final-crlf", text + "\r\n", None, "base64_shape")
        add("leading-space", " " + text, None, "base64_shape")
        add("embedded-newline", text[:20] + "\n" + text[20:], None, "base64_shape")
        for n in (0, 1, 31, 33, 42, 45, 64, 88):              # other lengths, made of alphabet characters
            body = (text[:-1] * 3)[:n]
            if n == 44:
                continue
            add("length-%d" % n, body, None, "base64_shape")
        add("hex-instead", raw.hex(), None, "base64_shape")
        add("receipt-form", digest_of(raw), None, "base64_shape")
    for kind, value in (("none", None), ("true", True), ("false", False), ("int", 5), ("float", 3.5), ("list", [canonical(bytes(32))]), ("map", {}), ("empty-list", [])):
        add("type-" + kind, value, None, "field_type")
    return cases


# ---------------------------------------------------------------- record defects, with their reason, field and RANK written by hand from the contract

# the order of the checks (contract section 5): the rank of the check that finds a defect. A higher rank is found only when no lower one is.
R_ENV_RELEASE, R_ENV_PROV, R_BUILD, R_FILES = 1, 2, 3, 4
GRAMMAR = [  # (receipt field, artifact path, provenance member, list index of the entry, first rank of its checks)
    ("grammar_sha256", CHECK_GRAMMAR, "grammar_sha256", 1, 10),
    ("grammar_schema_sha256", GRAMMAR_SCHEMA, "grammar_schema_sha256", 2, 20),
]
# the ranks inside one grammar field: artifact, digest, provenance, disagreement, pin missing, pin differs
A_ART, A_DIG, A_PROV, A_DIS, A_PINMISS, A_PINDIFF = 0, 1, 2, 3, 4, 5

NOT_STRINGS = [("none", None), ("true", True), ("int", 5), ("float", 3.5), ("list", ["x"]), ("map", {"a": 1}), ("empty-list", []), ("empty-map", {})]
NOT_MAPPINGS = [("none", None), ("str", "x"), ("int", 1), ("list", [])]


def touches_conflict(a, b):
    """Two sets of touched places conflict when one is a prefix of the other (or equal): a pair of defects may not act on the same place."""
    for x in a:
        for y in b:
            if x == y or x.startswith(y + ".") or y.startswith(x + "."):
                return True
    return False


class Defect:
    def __init__(self, id, rank, ops, reason, field, pin=None, touches=None, list_edit=False, class_="defect"):
        self.id, self.rank, self.ops, self.reason, self.field = id, rank, ops, reason, field
        self.pin = pin or {}                   # {path: replacement hex, or None to remove}
        self.touches = touches or set()
        self.list_edit = list_edit             # deletes or appends a list element: such a defect is not combined with others
        self.class_ = class_


def defects(rng):
    out = []

    def d(id, rank, ops, reason, field, **kw):
        t = kw.pop("touches", None) or {op[1] for op in ops}
        out.append(Defect(id, rank, ops, reason, field, touches=t, **kw))

    # the envelopes
    for who, rank in (("release", R_ENV_RELEASE), ("provenance", R_ENV_PROV)):
        member = who + ".envelope"
        for label, value in (("2", 2), ("1", 1), ("0", 0), ("4", 4), ("string-3", "3"), ("float-3", 3.0), ("true", True), ("none", None), ("list", [3]), ("map", {"v": 3})):
            d("env-%s-%s" % (who, label), rank, [("set", member, value)], "envelope_not_3", member)
        d("env-%s-absent" % who, rank, [("delete", member)], "envelope_not_3", member)
        d("env-%s-parent-absent" % who, rank, [("delete", who)], "envelope_not_3", member)
        d("env-%s-parent-scalar" % who, rank, [("set", who, 7)], "envelope_not_3", member)
    # the build hash
    b = "release.build_hash"
    d("build-absent", R_BUILD, [("delete", b)], "field_missing", b)
    d("build-none", R_BUILD, [("set", b, None)], "field_missing", b)
    for label, value in NOT_STRINGS[1:]:
        d("build-type-" + label, R_BUILD, [("set", b, value)], "field_type", b)
    good_raw = bytes.fromhex(shared.BUILD_HEX)
    gtext = canonical(good_raw)
    for label, value, reason in (("final-newline", gtext + "\n", "base64_shape"), ("leading-space", " " + gtext, "base64_shape"), ("url-safe", "-" + gtext[1:], "base64_shape"),
                                 ("unpadded", gtext[:-1], "base64_shape"), ("hex", good_raw.hex(), "base64_shape"), ("receipt-form", digest_of(good_raw), "base64_shape"),
                                 ("another-spelling", gtext[:42] + "t=", "base64_not_canonical")):
        d("build-" + label, R_BUILD, [("set", b, value)], reason, b)
    # artifacts.files
    d("files-artifacts-absent", R_FILES, [("delete", "artifacts")], "field_missing", "artifacts.files")
    d("files-absent", R_FILES, [("delete", "artifacts.files")], "field_missing", "artifacts.files")
    d("files-none", R_FILES, [("set", "artifacts.files", None)], "field_missing", "artifacts.files")
    for label, value in (("str", "x"), ("int", 5), ("true", True), ("map", {"a": 1}), ("empty-map", {})):
        d("files-type-" + label, R_FILES, [("set", "artifacts.files", value)], "field_type", "artifacts.files")
    # the two grammar fields
    for receipt_field, path, member, idx, base in GRAMMAR:
        base_raw = bytes.fromhex(shared.GRAMMAR_HEX if idx == 1 else shared.SCHEMA_HEX)
        ent = "artifacts.files.%d" % idx
        dig_field = "artifacts.files[%s].digest" % path
        prov = "provenance." + member
        # the artifact entry: absent, twice, spelled differently, not a mapping, a path that is not a string
        out.append(Defect("%s-art-absent" % member, base + A_ART, [("delete", ent)], "artifact_absent", path, touches={"artifacts.files"}, list_edit=True))
        out.append(Defect("%s-art-twice" % member, base + A_ART, [("append", "artifacts.files", {"path": path, "digest": digest_of(base_raw)})], "artifact_duplicate", path,
                          touches={"artifacts.files"}, list_edit=True))
        out.append(Defect("%s-art-twice-other-digest" % member, base + A_ART, [("append", "artifacts.files", {"path": path, "digest": digest_of(flip_bit(base_raw, 3))})],
                          "artifact_duplicate", path, touches={"artifacts.files"}, list_edit=True))
        for label, spelled in (("dot-slash", "./" + path), ("trailing-space", path + " "), ("upper", path.upper()), ("backslash", path.replace("/", "\\")), ("parent", "../" + path)):
            d("%s-art-path-%s" % (member, label), base + A_ART, [("set", ent + ".path", spelled)], "artifact_absent", path)
        for label, value in NOT_MAPPINGS:
            d("%s-art-entry-%s" % (member, label), base + A_ART, [("set", ent, value)], "artifact_absent", path)
        for label, value in NOT_STRINGS:
            d("%s-art-path-type-%s" % (member, label), base + A_ART, [("set", ent + ".path", value)], "artifact_absent", path)
        d("%s-art-path-key-absent" % member, base + A_ART, [("delete", ent + ".path")], "artifact_absent", path)
        # the digest of the entry: forms, then types
        hexs = base_raw.hex()
        for label, value in (("upper", "sha256:" + hexs.upper()), ("sha512", "sha512:" + hexs), ("63", "sha256:" + hexs[:-1]), ("65", "sha256:" + hexs + "0"), ("no-prefix", hexs),
                             ("final-newline", digest_of(base_raw) + "\n"), ("leading-space", " " + digest_of(base_raw)), ("empty", ""), ("base64", canonical(base_raw)),
                             ("prefix-twice", "sha256:" + digest_of(base_raw)), ("mixed-case", "sha256:" + hexs[:10].upper() + hexs[10:]), ("non-hex", "sha256:" + "g" + hexs[1:])):
            d("%s-dig-%s" % (member, label), base + A_DIG, [("set", ent + ".digest", value)], "digest_form", dig_field)
        for label, value in NOT_STRINGS:
            d("%s-dig-type-%s" % (member, label), base + A_DIG, [("set", ent + ".digest", value)], "field_type", dig_field)
        d("%s-dig-absent" % member, base + A_DIG, [("delete", ent + ".digest")], "field_type", dig_field)
        # the provenance member: absent, null, types, shapes
        d("%s-prov-absent" % member, base + A_PROV, [("delete", prov)], "field_missing", prov)
        d("%s-prov-none" % member, base + A_PROV, [("set", prov, None)], "field_missing", prov)
        for label, value in NOT_STRINGS[1:]:
            d("%s-prov-type-%s" % (member, label), base + A_PROV, [("set", prov, value)], "field_type", prov)
        ptext = canonical(base_raw)
        for label, value, reason in (("final-newline", ptext + "\n", "base64_shape"), ("url-safe", "-" + ptext[1:], "base64_shape"), ("unpadded", ptext[:-1], "base64_shape"),
                                     ("hex", hexs, "base64_shape"), ("another-spelling", ptext[:42] + "B=", "base64_not_canonical")):
            d("%s-prov-%s" % (member, label), base + A_PROV, [("set", prov, value)], reason, prov)
        # disagreement: the provenance holds a valid digest that is not the artifact's (a bit of it changed)
        for n in rng.sample(range(256), 6):
            d("%s-disagree-bit%03d" % (member, n), base + A_DIS, [("set", prov, canonical(flip_bit(base_raw, n)))], "provenance_artifacts_disagree", receipt_field)
        # the pin: absent, or different
        out.append(Defect("%s-pin-absent" % member, base + A_PINMISS, [], "field_missing", "pin." + path, pin={path: None}, touches={"pin." + path}))
        for n in rng.sample(range(256), 6):
            out.append(Defect("%s-pin-bit%03d" % (member, n), base + A_PINDIFF, [], "record_pin_disagree", receipt_field, pin={path: flip_bit(base_raw, n).hex()}, touches={"pin." + path}))
        out.append(Defect("%s-pin-with-prefix" % member, base + A_PINDIFF, [], "record_pin_disagree", receipt_field, pin={path: digest_of(base_raw)}, touches={"pin." + path}))
        out.append(Defect("%s-pin-upper" % member, base + A_PINDIFF, [], "record_pin_disagree", receipt_field, pin={path: hexs.upper()}, touches={"pin." + path}))
    return out


def apply(defs):
    record = shared.REAL_RECORD
    ops = [op for x in defs for op in x.ops]
    if ops:
        record = shared.mutate(record, *ops)
    pin = dict(shared.REAL_PIN)
    for x in defs:
        for path, value in x.pin.items():
            if value is None:
                pin.pop(path, None)
            else:
                pin[path] = value
    return record, pin


def record_cases(rng, pairs, valid):
    cases = []

    def add(class_, id, record, pin, expect, refuse, field):
        cases.append({"id": id, "class": class_, "kind": "map", "record": record, "pin": pin, "expect": expect, "refuse": refuse, "field": field})

    add("base", "rec-base", shared.REAL_RECORD, dict(shared.REAL_PIN), shared.REAL_EXPECT, None, None)
    catalogue = defects(rng)
    ids = [x.id for x in catalogue]
    assert len(ids) == len(set(ids)), "a defect id is repeated"
    def written(record, pin):
        # the written form, not Python equality: 3 == 3.0 and True == 1 in Python, and in the file they are different values
        return json.dumps([record, pin], sort_keys=True)

    base_written = written(*apply([]))
    for x in catalogue:
        record, pin = apply([x])
        assert written(record, pin) != base_written, "the defect %s changes nothing: it is not a defect" % x.id
        add("defect", "rec-" + x.id, record, pin, None, x.reason, x.field)
    # pairs of defects whose order the contract fixes: the earlier rank wins, whichever was named first
    free = [x for x in catalogue if not x.list_edit]
    made = set()
    tries = 0
    while len(made) < pairs and tries < pairs * 60:
        tries += 1
        a, b = rng.sample(free, 2)
        if a.rank == b.rank or touches_conflict(a.touches, b.touches):
            continue
        first, second = (a, b) if a.rank < b.rank else (b, a)
        key = (first.id, second.id)
        if key in made:
            continue
        made.add(key)
        record, pin = apply([a, b] if rng.random() < 0.5 else [b, a])
        add("pair", "pair-%s+%s" % key, record, pin, None, first.reason, first.field)
    # valid records: random digests, consistent in the artifacts, the provenance and the pin, and a random valid build hash
    for i in range(valid):
        build, gram, sch = (bytes(rng.randrange(256) for _ in range(32)) for _ in range(3))
        record = shared.mutate(shared.REAL_RECORD, ("set", "release.build_hash", canonical(build)), ("set", "provenance.grammar_sha256", canonical(gram)),
                               ("set", "provenance.grammar_schema_sha256", canonical(sch)), ("set", "artifacts.files.1.digest", digest_of(gram)),
                               ("set", "artifacts.files.2.digest", digest_of(sch)))
        pin = {CHECK_GRAMMAR: gram.hex(), GRAMMAR_SCHEMA: sch.hex()}
        add("valid", "valid-%04d" % i, record, pin,
            {"primitive_build_hash": digest_of(build), "grammar_sha256": digest_of(gram), "grammar_schema_sha256": digest_of(sch)}, None, None)
    return cases


def generate(seed=DEFAULT_SEED, values=40, pairs=400, valid=40):
    rng = random.Random(seed)
    return conversion_cases(rng, values) + record_cases(rng, pairs, valid)


def render(cases):
    return "".join(json.dumps(c, sort_keys=True, ensure_ascii=True) + "\n" for c in cases)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--values", type=int, default=40, help="random 32-byte values for the base64 class")
    ap.add_argument("--pairs", type=int, default=400, help="pairs of defects")
    ap.add_argument("--valid", type=int, default=40, help="valid random records")
    ap.add_argument("--out")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    cases = generate(args.seed, args.values, args.pairs, args.valid)
    text = render(cases)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    if args.summary or not args.out:
        by = {}
        for c in cases:
            k = c["class"].split(":")[0]
            by[k] = by.get(k, 0) + 1
        print("seed %d: %d cases, sha256 %s" % (args.seed, len(cases), hashlib.sha256(text.encode("utf-8")).hexdigest()), file=sys.stderr)
        print("  " + ", ".join("%s %d" % kv for kv in sorted(by.items())), file=sys.stderr)
    if not args.out:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
