"""The canonical JSON of the test evidence (docs/TEST-EVIDENCE.md, "Canonical form").

One declared byte format, RFC 8785 (JSON Canonicalization Scheme) restricted to an I-JSON subset:
UTF-8 without a byte order mark; no insignificant whitespace; object member names sorted by UTF-16 code unit;
strings escaped as RFC 8785 says; integers only, in +-(2^53-1), without exponent, fraction, leading zero or -0;
no NaN or infinity; no lone surrogate; no duplicate member name; nesting at most 64 deep.

The reader is strict. `parse_strict` accepts any document of that subset (white space and order as the writer
likes); `read_canonical` accepts only a document that is byte for byte its own canonical form. Every refusal
carries a class (CanonError.kind) that the Go program in canon-go/ names the same way; the vectors in
vectors/canon.json hold both.
"""
import hashlib

MAX_INT = 2**53 - 1
MAX_DEPTH = 64
WS = " \t\n\r"


class CanonError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind


def parse_strict(data):
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("bytes expected")
    data = bytes(data)
    if data.startswith(b"\xef\xbb\xbf"):
        raise CanonError("BOM")
    try:
        text = data.decode("utf-8")  # strict: refuses overlong forms, encoded surrogates, truncated sequences
    except UnicodeDecodeError:
        raise CanonError("INVALID_UTF8")
    p = _Parser(text)
    p.skip_ws()
    if p.i >= len(text):
        raise CanonError("EMPTY")
    v = p.value(0)
    p.skip_ws()
    if p.i != len(text):
        raise CanonError("TRAILING")
    return v


class _Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0

    def skip_ws(self):
        while self.i < len(self.s) and self.s[self.i] in WS:
            self.i += 1

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def value(self, depth):
        c = self.peek()
        if c in ("{", "[") and depth >= MAX_DEPTH:  # depth counts the containers around the value
            raise CanonError("DEPTH")
        if c == "{":
            return self.obj(depth)
        if c == "[":
            return self.arr(depth)
        if c == '"':
            return self.string()
        if c == "-" or c.isdigit() and c in "0123456789":
            return self.number()
        for lit, val in (("true", True), ("false", False), ("null", None)):
            if self.s.startswith(lit, self.i):
                self.i += len(lit)
                return val
        raise CanonError("SYNTAX")

    def obj(self, depth):
        self.i += 1
        out = {}
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            self.skip_ws()
            if self.peek() != '"':
                raise CanonError("SYNTAX")
            k = self.string()
            if k in out:
                raise CanonError("DUPLICATE_KEY", k)
            self.skip_ws()
            if self.peek() != ":":
                raise CanonError("SYNTAX")
            self.i += 1
            self.skip_ws()
            out[k] = self.value(depth + 1)
            self.skip_ws()
            c = self.peek()
            self.i += 1
            if c == "}":
                return out
            if c != ",":
                raise CanonError("SYNTAX")

    def arr(self, depth):
        self.i += 1
        out = []
        self.skip_ws()
        if self.peek() == "]":
            self.i += 1
            return out
        while True:
            self.skip_ws()
            out.append(self.value(depth + 1))
            self.skip_ws()
            c = self.peek()
            self.i += 1
            if c == "]":
                return out
            if c != ",":
                raise CanonError("SYNTAX")

    def number(self):
        j = self.i
        if self.s[j] == "-":
            j += 1
        if j >= len(self.s) or self.s[j] not in "0123456789":
            raise CanonError("NUMBER")
        k = j
        while k < len(self.s) and self.s[k] in "0123456789":
            k += 1
        digits = self.s[j:k]
        if len(digits) > 1 and digits[0] == "0":
            raise CanonError("NUMBER", "leading zero")
        if k < len(self.s) and self.s[k] in ".eE":
            raise CanonError("NUMBER", "fraction or exponent")
        n = int(digits)
        if self.s[self.i] == "-":
            if n == 0:
                raise CanonError("NUMBER", "-0")
            n = -n
        if abs(n) > MAX_INT:
            raise CanonError("NUMBER", "out of range")
        self.i = k
        return n

    def string(self):
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                raise CanonError("SYNTAX", "unterminated string")
            c = self.s[self.i]
            self.i += 1
            if c == '"':
                return "".join(out)
            if ord(c) < 0x20:
                raise CanonError("CONTROL_CHAR")
            if c != "\\":
                out.append(c)
                continue
            if self.i >= len(self.s):
                raise CanonError("SYNTAX", "unterminated string")
            e = self.s[self.i]
            self.i += 1
            simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
            if e in simple:
                out.append(simple[e])
            elif e == "u":
                cu = self.hex4()
                if 0xD800 <= cu <= 0xDBFF:
                    if self.s.startswith("\\u", self.i):
                        self.i += 2
                        lo = self.hex4()
                        if 0xDC00 <= lo <= 0xDFFF:
                            out.append(chr(0x10000 + ((cu - 0xD800) << 10) + (lo - 0xDC00)))
                            continue
                    raise CanonError("LONE_SURROGATE")
                if 0xDC00 <= cu <= 0xDFFF:
                    raise CanonError("LONE_SURROGATE")
                out.append(chr(cu))
            else:
                raise CanonError("ESCAPE")

    def hex4(self):
        h = self.s[self.i:self.i + 4]
        if len(h) != 4 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
            raise CanonError("ESCAPE")
        self.i += 4
        return int(h, 16)


def _utf16_key(s):
    return s.encode("utf-16-be", "surrogatepass")


def _string(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _ser(v, depth):
    if isinstance(v, (list, tuple, dict)) and depth >= MAX_DEPTH:
        raise CanonError("DEPTH")
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        if abs(v) > MAX_INT:
            raise CanonError("NUMBER", "out of range")
        return str(v)
    if isinstance(v, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in v):
            raise CanonError("LONE_SURROGATE")
        return _string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_ser(x, depth + 1) for x in v) + "]"
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise CanonError("SYNTAX", "member names are strings")
        return "{" + ",".join(_string_key(k) + ":" + _ser(v[k], depth + 1) for k in sorted(v, key=_utf16_key)) + "}"
    raise CanonError("SYNTAX", "type %s" % type(v).__name__)


def _string_key(k):
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in k):
        raise CanonError("LONE_SURROGATE")
    return _string(k)


def canonicalize(value):
    """The canonical bytes of a value (a dict, list, str, int, bool or None)."""
    return _ser(value, 0).encode("utf-8")


def read_canonical(data):
    """The value of a document that is its own canonical form; NOT_CANONICAL for any other."""
    v = parse_strict(data)
    if canonicalize(v) != bytes(data):
        raise CanonError("NOT_CANONICAL")
    return v


def content_hash(value):
    return hashlib.sha256(canonicalize(value)).hexdigest()
