#!/usr/bin/env python3
"""Writes vectors/canon.json: the test vectors of the canonical JSON (canon.py and canon-go/ both read them).

The expected values are typed here by hand, not computed by either implementation: that is the point of a vector.
Each case is the input bytes and either the canonical bytes the document must give or the class of its refusal.
Reading a document as canonical must succeed exactly when the input already equals the canonical bytes.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

OK = []      # (name, input bytes, canonical bytes)
REFUSE = []  # (name, input bytes, class)


def ok(name, i, c=None):
    OK.append((name, i, i if c is None else c))


def refuse(name, i, kind):
    REFUSE.append((name, i, kind))


# --- documents that are accepted ------------------------------------------------------------------------------------
ok("empty object", b"{}")
ok("empty array", b"[]")
ok("scalars", b'[null,true,false,0,-1,"x"]')
ok("members are sorted", b'{"a":1,"b":2}')
ok("members out of order are sorted", b'{"b":2,"a":1}', b'{"a":1,"b":2}')
ok("white space is dropped", b' { "a" : [ 1 , 2 ] }\n', b'{"a":[1,2]}')
ok("nested order", b'{"b":{"d":1,"c":2},"a":[{"y":1,"x":2}]}', b'{"a":[{"x":2,"y":1}],"b":{"c":2,"d":1}}')
# UTF-16 code unit order differs from code point order here: U+1F600 is D83D DE00, U+FFFF is FFFF
ok("order by UTF-16 code unit, not code point", '{"￿":1,"\U0001F600":2}'.encode(), '{"\U0001F600":2,"￿":1}'.encode())
ok("order of a prefix", b'{"ab":1,"a":2}', b'{"a":2,"ab":1}')
ok("an escaped name sorts as its character", b'{"b":1,"\\u0061":2}', b'{"a":2,"b":1}')
ok("non-ASCII is written as is", '"é中\U0001F600"'.encode())
ok("an escaped non-ASCII is written as is", b'"\\u00e9\\u4e2d\\ud83d\\ude00"', '"é中\U0001F600"'.encode())
ok("escapes with a short form", b'"\\"\\\\\\b\\f\\n\\r\\t"')
ok("the solidus is not escaped", b'"\\/"', b'"/"')
ok("other control characters are \\u00xx lower case", b'"\\u0000\\u001f\\u000B"', b'"\\u0000\\u001f\\u000b"')
ok("DEL, U+2028 and U+0085 are written as is", '"\x7f \u0085"'.encode())
ok("upper case hex in an escape is read", b'"\\u00E9"', '"é"'.encode())
ok("integers", b"[0,1,-1,9007199254740991,-9007199254740991]")
ok("depth 64 is allowed", b"[" * 64 + b"]" * 64)

# --- documents that are refused ------------------------------------------------------------------------------------
refuse("empty input", b"", "EMPTY")
refuse("only white space", b" \n", "EMPTY")
refuse("a byte order mark", b"\xef\xbb\xbf{}", "BOM")
refuse("invalid UTF-8", b'"\xff"', "INVALID_UTF8")
refuse("an overlong UTF-8 form", b'"\xc0\x80"', "INVALID_UTF8")
refuse("an encoded surrogate", b'"\xed\xa0\x80"', "INVALID_UTF8")
refuse("a truncated UTF-8 sequence", b'"\xe4\xb8"', "INVALID_UTF8")
refuse("a duplicate member name", b'{"a":1,"a":2}', "DUPLICATE_KEY")
refuse("a duplicate member name once escaped", b'{"a":1,"\\u0061":2}', "DUPLICATE_KEY")
refuse("a duplicate in a nested object", b'{"x":{"k":1,"k":1}}', "DUPLICATE_KEY")
refuse("a lone high surrogate", b'"\\ud800"', "LONE_SURROGATE")
refuse("a lone low surrogate", b'"\\udc00"', "LONE_SURROGATE")
refuse("a high surrogate and no low one", b'"\\ud800\\u0041"', "LONE_SURROGATE")
refuse("a lone surrogate in a member name", b'{"\\udfff":1}', "LONE_SURROGATE")
refuse("a raw control character in a string", b'"a\nb"', "CONTROL_CHAR")
refuse("a raw tab in a string", b'"a\tb"', "CONTROL_CHAR")
refuse("an unknown escape", b'"\\x41"', "ESCAPE")
refuse("a short \\u escape", b'"\\u12"', "ESCAPE")
refuse("a \\u escape that is not hex", b'"\\u00zz"', "ESCAPE")
refuse("trailing bytes", b"{} x", "TRAILING")
refuse("two documents", b"{}{}", "TRAILING")
refuse("a trailing comma in an array", b"[1,]", "SYNTAX")
refuse("a trailing comma in an object", b'{"a":1,}', "SYNTAX")
refuse("a missing colon", b'{"a" 1}', "SYNTAX")
refuse("a name that is not a string", b"{a:1}", "SYNTAX")
refuse("an unterminated string", b'"abc', "SYNTAX")
refuse("an unclosed array", b"[1,2", "SYNTAX")
refuse("a single-quoted string", b"'a'", "SYNTAX")
refuse("NaN", b"NaN", "SYNTAX")
refuse("Infinity", b"Infinity", "SYNTAX")
refuse("a plus sign", b"+1", "SYNTAX")
refuse("a leading dot", b".5", "SYNTAX")
refuse("True with a capital", b"True", "SYNTAX")
refuse("a leading zero", b"01", "NUMBER")
refuse("minus zero", b"-0", "NUMBER")
refuse("a fraction", b"1.0", "NUMBER")
refuse("an exponent", b"1e2", "NUMBER")
refuse("an upper case exponent", b"1E2", "NUMBER")
refuse("a minus and no digit", b"-", "NUMBER")
refuse("a minus and a letter", b"-a", "NUMBER")
refuse("a number past 2^53-1", b"9007199254740992", "NUMBER")
refuse("a negative number past 2^53-1", b"-9007199254740992", "NUMBER")
refuse("a very long integer", b"1" * 40, "NUMBER")
refuse("depth 65", b"[" * 65 + b"]" * 65, "DEPTH")

cases = []
for name, i, c in OK:
    cases.append({"name": name, "input_hex": i.hex(), "canonical_hex": c.hex(), "refuse": None})
for name, i, kind in REFUSE:
    cases.append({"name": name, "input_hex": i.hex(), "canonical_hex": None, "refuse": kind})
with open(os.path.join(HERE, "vectors", "canon.json"), "w") as fh:
    json.dump({"schema": "test-evidence-canon-vectors/1", "cases": cases}, fh, indent=1)
    fh.write("\n")
print(len(OK), "accepted,", len(REFUSE), "refused")
