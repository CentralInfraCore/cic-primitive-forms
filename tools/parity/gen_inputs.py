#!/usr/bin/env python3
"""Deterministic inputs for the Rust/Go parser differential (docs/P1-SUPPORTED-SUBSET.md).

    gen_inputs.py OUT [--count N] [--seed S] [--upstream DIR]

Writes one input per line, hex-encoded, to OUT. The inputs are (1) the YAML texts the repository already
holds (corpus `input_yaml`, the shared vectors, the corpus files themselves, the pinned access.yaml when
--upstream points at the checkout), (2) a hand-written list of cases that YAML readers are known to
disagree on, and (3) seeded random edits of (1) and (2). The same seed gives the same file.
"""
import argparse
import glob
import json
import os
import random
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAND = [
    # tabs and whitespace
    "a:\tb\n", "a: b\t\n", "a:\t[1]\n", "a: [\t1]\n", "\ta: 1\n", "a:\n\tb: 1\n", "a: 1\n\n\n", " a: 1\n", "a: 1\n b: 2\n",
    "- a\n -b\n", "-\ta\n", "a: \"\tx\"\n", "a: |\n\tx\n", "a: b #c\n", "a: b#c\n", "a:  b  \n", "a:b\n", "a:\n",
    # flow collections
    "[]", "{}", "[a, b]", "{a: 1, b: 2}", "{a: 1, a: 2}", "[a, [b, [c]]]", "{a: {b: {c: 1}}}", "[,]", "[a,]", "{,}", "{a}", "{a: }",
    "{a:1}", "{a: 1,}", "[a b]", "[a: b]", "{? a: b}", "[? a]", "[a, b", "{a: 1", "]", "}", "[a]]", "{a: [1, 2], b: {c: d}}",
    "a: [1, 2\n  , 3]\n", "a: {b: 1,\n  c: 2}\n", "[1,\n2]", "[\n1,\n2,\n]\n",
    # block collections
    "- a\n- b\n", "- - a\n  - b\n- c\n", "- a: 1\n  b: 2\n- c: 3\n", "a:\n- 1\n- 2\n", "a:\n  - 1\n  - 2\nb: 3\n", "a:\n - 1\n  - 2\n",
    "? a\n: b\n", "? [a]\n: b\n", "? a\n", ": b\n", "a: b\nc\n", "a: b\n  c\n", "a:\n  b\n  c\n", "- a\nb: 1\n", "a: 1\n- b\n",
    # scalars
    "a", "'a'", "\"a\"", "'it''s'", "\"a\\nb\"", "\"\\u00e9\"", "\"\\x41\"", "\"\\q\"", "'a\nb'", "\"a\n  b\"", "a\n  b", "a: b\n  c: d\n",
    "~", "null", "Null", "NULL", "true", "True", "TRUE", "false", "yes", "no", "on", "off", "y", "n", "0", "-0", "+0", "00", "01", "0o7", "0x1F", "0b1",
    "1_000", "1.5", ".5", "5.", "1e3", "1E3", "-1.5", ".inf", "-.inf", ".nan", "9223372036854775807", "9223372036854775808", "-9223372036854775808", "-9223372036854775809",
    "123abc", "a123", "1.2.3", "10.0.0.1", "1:30", "12:30:45", "2026-10-19", "2026-10-19T10:00:00Z", "=", "<<", "<<: {a: 1}", "a: <<", "&a x", "*a", "!!str x", "!x y",
    "!!int 5", "a: !!str 5", "a: &x 1\nb: *x\n", "a: &x\n  b: 1\nc: *x\n", "%YAML 1.2\n---\na: 1\n", "%TAG ! tag:x,2000:\n---\na: !y 1\n", "--- a\n", "---\n", "---\n...\n", "a\n...\n", "...\n",
    "---\na: 1\n---\nb: 2\n", "--- \n--- \n", "a: 1\n...\n---\nb: 2\n", "a: 1\n---\n", "# c\n", "# c\na: 1\n", "a: 1 # c\n", "a: 1\n# c\n", "#c\n#d\n",
    # block scalars
    "a: |\n  x\n", "a: |\n  x\n  y\n", "a: |-\n  x\n", "a: |+\n  x\n\n", "a: >\n  x\n  y\n", "a: >-\n  x\n\n  y\n", "a: |2\n   x\n", "a: |\nx\n", "a: |\n\n", "a: |0\n x\n",
    "a: >\n\n  x\n", "a: |\n  x\n b: 1\n", "a: |\n  x\nb: 1\n",
    # keys
    "'a': 1\n", "\"a\": 1\n", "1: a\n", "true: a\n", "null: a\n", "~: a\n", "'': a\n", ": a\n", "a b: c\n", "a: b: c\n", "a:: b\n", "a :b\n", "a : b\n", "-a: 1\n", "- : 1\n",
    "[a]: 1\n", "{a: 1}: 2\n", "? - a\n: b\n", "a: 1\na: 2\n", "a: 1\nb: 2\na: 3\n", "'a': 1\na: 2\n", "1: a\n'1': b\n", "a: {b: 1, b: 2}\n", "x: [{a: 1, a: 2}]\n",
    # duplicates before errors
    "a: 1\na: 2\nb: [\n", "a: 1\na: 2\n&x b: 1\n", "a: 1\na: 2\n---\nb: 1\n", "a: &x 1\na: 2\n", "&x a: 1\na: 2\n",
    # unicode and encodings
    "\ufeffa: 1\n", "\ufeff\ufeffa: 1\n", "a: \u00e9\n", "\u00e9: a\n", "a: \U0001F642\n", "a: \u0085\n", "a: \u2028\n", "a: \u2029\n", "a: \u00a0\n", "a: \u200b\n", "a: \ufeff\n", "\u0000", "a: \u0000\n", "a: \u0001\n", "a: \u007f\n", "a: \u001b\n",
    "a: \"\\u0085\"\n", "a: \"\\ufeff\"\n", "a: \"\\U0001F642\"\n", "a\rb: 1\r", "a: 1\r\nb: 2\r\n", "a: 1\rb: 2\r", "a: 1\r\n\r\nb: 2", "a: |\r\n  x\r\n", "\r\n", "\n\n\n", "   \n", "\t\n",
    # nesting and size
    "[" * 70 + "]" * 70, "[" * 64 + "]" * 64, "[" * 65 + "]" * 65, "a: " + "{b: " * 70 + "1" + "}" * 70 + "\n", "- " * 70 + "x\n",
    "a: " + "x" * 1030 + "\n", ("k" * 1030) + ": 1\n", "? " + ("k" * 1030) + "\n: 1\n", "'" + ("k" * 1030) + "': 1\n",
    # misc
    "", " ", "\n", "a", "a b c", "[a, b]: c", "- a\n  - b\n", "a: 'b\n", "a: \"b\n", "a: [b\n", "a: {b\n", "a: b]\n", "a: b}\n", "a: ,\n", "a: @x\n", "a: `x\n", "a: %x\n", "a: |x\n", "a: >x\n",
    "a: - b\n", "a: -\n", "a: ?\n", "a: ? b\n", "- ? a\n  : b\n", "a: *\n", "a: &\n", "a: !\n", "a: !!\n",
]


# Cases for the rules the two parsers share since the differential (docs/P1-SUPPORTED-SUBSET.md, "Parser
# parity"): each line of the list pins one rule, and `make_corpus.py` turns the list into the regression
# corpus both languages run.
EXTRA = [
    # error priority: a syntax error anywhere wins over an earlier duplicate / unsupported / limit error
    "a: 1\na: 2\nb: [\n", "a: 1\na: 2\n]\n", "a: 0x1F\nb: [\n", "a: &x 1\nb: [\n", "a: 1\na: 2\nb: 3\n", "a: 0x1F\nb: 0x2F\n",
    "a: {b: 1, b: 2}\nc: [\n", "a: 1\na: 2\n: x\n",
    # the first non-syntax error is the one reported
    "a: 0x1F\na: 2\n", "a: 1\na: 2\nb: 0x1F\n", "<<: 1\na: 1\na: 2\n", "a: 1\na: 2\nb: *x\n",
    # control characters, tabs, byte order marks inside the text
    "a: \u0001\n", "a: \u0008\n", "a: \u000b\n", "a: \u000e\n", "a: \u001f\n", "a: \u007f\n", "\u0000", "a:\tb\n", "\ta: 1\n", "a: \"\tx\"\n", "a: |\n  x\ty\n",
    "a: \ufeff\n", "\ufeff\ufeffa: 1\n", "a: 1\n\ufeffb: 2\n", "\ufeffa: 1\n",
    # directives and document markers
    "%YAML 1.2\n---\na: 1\n", "%TAG ! x\n", "a: 1\n%x\n", "---\na: 1\n", "# c\n---\na: 1\n", "\n---\na: 1\n", "--- a\n", "---\n---\na: 1\n", "a: 1\n---\n", "a: 1\n...\n",
    "...\n", "---\n...\n", "  ---\n", " ...\n", "a: 1\n --- \n", "---a: 1\n", "a: ---\n", "a: ...\n", "- ---\n", "---\n- a\n", "--- \na: 1\n",
    # tags, anchors, aliases, merge keys
    "a: !\n", "a: ! x\n", "- !\n- ! 1\n", "a: !!str x\n", "a: !x y\n", "[!, a]", "{a: !}", "a: &x 1\n", "a: *x\n", "a: & x\n", "a: * x\n", "- &a b\n", "- *a\n", "&a b: 1\n", "a: [&x 1]\n",
    "a: \"!x\"\n", "a: 'x !y'\n", "a: \"&x *y ?\"\n", "a: x!y\n", "a: x&y\n", "a: x*y\n", "a: x? y\n", "a: b # !x &y *z ?\n", "a: |\n  !x &y *z ? [\n", "a: >-\n  ! & *\n", "<<: {a: 1}\n", "'<<': 1\n", "\"<<\": 1\n",
    # explicit keys
    "? a\n: b\n", "? a b\n: c\n", "? \"a\"\n: b\n", "?  a\n: b\n", "?\n: b\n", "? \n: b\n", "? - a\n: b\n", "? ? a\n: b\n", "? : a\n: b\n", "[? a]", "{? a: b}", "{?}", "a: ? b\n", "a: ?b\n", "- ? a\n  : b\n", "  ? a\n  : b\n",
    "? " + "k" * 600 + "\n: v\n", "a:\n  ? " + "k" * 600 + "\n  : v\n",
    # empty implicit keys
    ": b\n", "{: 2}", "{a: 1, : 2}", "a: 1\n: 2\n", "- : a\n", "? a\n: b\n: c\n",
    # comments
    "[a]#c\n", "[a] #c\n", "{a: 1}#c\n", "{a: 1} #c\n", "a: [b]#c\n", "'a'#c\n", "a: b#c\n", "a: \"b\" #c\n", "a: \"#b\"\n", "a: '#b'\n", "a: [\"#b\"]\n", "# only a comment\n",
    # block context: `[`, `{` and `,` inside a plain scalar do not start a word
    "a[&: 1\n", "a{!: 1\n", "a,*b: 1\n", "a: x[&y\n", "a: [x, &y 1]\n",
    "a: \"x\\\"y\"\nb: [? c]\n", "a: 'x''y'\nb: [? c]\n", "a: [|]\n  b: [? c]\n", "a:\n  - x\n  - - y\n", "a:\n    - - - y\n",
    # inside a plain scalar these are characters, not tags, anchors, aliases or keys
    "a *b: 1\n", "x: a !b\n", "x: a &b c\n", "x: a ?b\n", "k: a, *b\n", "a ?b: 1\n", "aAB# *: 1\n", "x: [a *b]\n", "x: a [&b\n", "x: a, !b\n", "- a *b\n",
    "a: \"x\\\"\n  ? - y\"\n", "a: \"x\\\"\"\n? - y\n", "a: 'x''\n  ? - y'\n", "a: 'x'''\n? - y\n", "a: 1 # c\r? - y\r", "a: 1 # c\n? - y\n", "a: |\r  x\r? - y\r", "a: |\n  x\n? - y\n", "a: '\n  ? - y'\n", "a: >\n  ? - y\n", "a|\n  ? - y\n", "a: | # c\n  ? - y\n", "a: 1\r? y\r: z\r", "]#\n", "}#\n", " ? - y\n", "  a: [? c]\n", "? ", "?x",
    # markers and directives: text inside block and quoted scalars, refused as lines elsewhere
    "a: |\n  ---\n  ...\n", "a: >\n  x\n  ---\n  y\n", "a: |+\n  ...\n\n", "a: |\n  %YAML 1.2\n", "a: \"x\n  ---\n  ...\"\n", "a: 'x\n  ---\n  ...'\n", "a: \"x\n  --- y\"\n", "a: 'x\n  ... y'\n", "a: \"x\n---\n  y\"\n", "a: 'x\n...\n  y'\n", "a: \"x\n--- y\"\n", "a: |\n  x\n---\n", "a: |\n  x\n...\n", "a: x\n  ---\n", "# c\n\n---\na: 1\n", "b: [\n---\n", "b: [\n...\n", "b: [\n%YAML 1.2\n",
    "? -x\n: 1\n", "? :x\n: 1\n", "? ?x\n: 1\n", "? -\n: 1\n", "? - \n: 1\n", "? -kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk\n: 1\n", "? :kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk\n: 1\n",
    # boundaries of the marker and explicit-key checks, the end of a block scalar, comments
    "k:\n  a: |\n    x\n  b: 1\n  c: [? q]\n", "k:\n  a: |\n    x\n  b:\n    - [? q]\n", "a: 1\n---", "a: 1\n...", "---x", "x\n...y", " ---\n", "? x", "? -", "? -x", "? :", "a: &\rb: 1\r", "a: \"x\n%y\"\n", "a: 1 # [? c]\n", "# [? c]\na: 1\n", "a: |\n  x\n  # [? c]\nb: 1\n",
    # `-`, `?` and `:` inside a plain scalar are text, at a scalar start they are indicators
    "a? *: 1\n", "a- &: 1\n", ":- *: 1\n", "-? &: 1\n", "a: b- *c\n", "- - *a\n", "a: - *b\n",
    # the scan's own boundaries (each pins a condition of scan_words)
    "a: [x,?c]\n", "a: [x]\nb: z,?c\n", "a: {x: 1}\nb: z,?c\n", "a: x]\nb: [y,?c]\n", "a:'x\nb: [? c]\n", "\"a ?b\"\n", "a: \"x\"'\nb: [? c]\n",
    "a: 1 # c\rb: [? c]\r", "a: |\r  x\rb: [? c]\r", "a: |\r  x\r", "a: \u000c\n", "a: 1\r---\rb: 2\r", "a: 1\r%x\r", "a: 1\n---\n", "a: 'x'\rb: [? c]\r",
    # quotes: specials stay text; a backslash is an escape in double quotes only; a block scalar goes on over a
    # whitespace-only line that is less indented than the block
    "a: 'x\\'\nb: [? c]\n", "a: \"x\"\nb: [? c]\n", "a: \"x\\\"[? c]\"\n", "k:\n  a: |\n      x\n \n      !y\n", "k:\n  a: |\n      x\n\n  b: !y\n", "k:\n  a: |\n      x\n\n  b: [? c]\n", "k:\r  a: |\r      x\r \r      !y\r",
    "a: 'it''s !x'\n", "a: \"\\\"!x\"\n", "a: \"x\\\\\" # &y\n", "a: 'x\n  ? y'\n", "a: \"x\n  *y\"\n", "- \",?\"\n", "- ' ?x'\n", "- \"|\"\n", "- '>'\n",
    # block scalars: positions at the indicator, refusals
    "a: |\n  x\n", "a: |\n  x\n  y\n", "a: >\n  x\n  y\n", "a: |\n\n  x\n", "a: |-\n  x\n", "a: |+\n  x\n\n", "a: |2\n   x\n", "a: | # c\n  x\n", "a:\n  b: |\n    x\n  c: 1\n",
    "- |\n  x\n- y\n", "a: |\n  x", "a: >\n  x\n  y", "a: |\n\n", "a: |\n", "a: >\n\n\n", "a: |+\n\n", "a: |\n  x\nb: |\n  y\n", "a: |\n  ! & * ? [\nb: !x\n", "a: |\n  x\n  # not a comment\nb: 1\n",
    "a: |\n  x\n# c\nb: 1\n", "a: >\n\n  x\n  \n  y\n",
    # sequence and mapping positions
    "a:\n- 1\n- 2\n", "a:\n  - 1\n  - 2\n", "a:\n- - 1\n- 2\n", "a:\n- - - 1\n", "- - 1\n- 2\n", "- - - a\n", "-   - a\n", "a:\n- b: 1\n  c: 2\n", "- a: 1\n- b: 2\n", "a:\n-\n  - 1\n", "a:\n  -\n    b: 1\n",
    "[[a]]", "[[], [a]]", "a: [b, [c]]\n", "{a: [1], b: {c: 2}}",
    # implicit nulls and their positions
    "{a}", "{a, b}", "{a: }", "[a, , b]", "[a, ]", "a:\n", "a:\nb: 1\n", "a: ~\n", "a: null\n", "- \n- ~\n", "---\n", "", "\n", "# c\n", "a: # c\nb: 1\n",
    # more than one document, stray tokens
    "a: 1\n--- b\n", "[a]]", "{a: 1}}", "a]", "]", "[a]\n[b]\n", "a\n---\nb\n",
    # byte order mark and line endings
    "\ufeff# c\na: 1\n", "\ufeffa: [b, c]\n", "a: 1\r\nb:\r\n- 1\r\n", "a: |\r\n  x\r\n", "a: 1\rb: [ z]\r", "a:\r- 1\r- 2\r",
    # limits next to the new checks
    "a: [" + "[" * 70 + "]" * 70 + ", !x\n", "a: " + "{b: " * 70 + "1" + "}" * 70 + "\n", ("a: 1\n" * 3) + "a: [\n",
    # known open differences in flow context (documented, pinned with both outcomes)
    "{a?}", "[a?,]", "[|]", "[||]", "{:1}", "{-}", "{aa:}", "\"a\"#", "{a: 1,a: 2:}", "a: {b: \r1,1,\n  c: 2}\n", "a: \"\nx\"\n ",
]


# Regression inputs that are NOT mutation seeds: adding one must not change the fixed-seed generated run the allow list
# (testdata/p1/parity-known.txt) is keyed to, so they are appended after the random part (see main) and read by make_corpus.py.
REGRESSIONS = [
    # an alias in the place of a mapping key (the Rust parser panicked on it, #31; the answer must be a refusal, never a panic)
    "[[Alse2]:&2-/{a,{[Alse2]:*2-/{6{a,Pa,;~$=PP", "{*a : 1}\n", "{*a : 1, b: 2}\n", "{x: &a 1, *a : 2}\n", "*a : 1\n", "*a : 1\nb: 2\n", "a: &x 1\n*x : 2\n",
    "&x a: 1\n*x : 2\n", "? *a\n: 1\n", "? *a\n", "{? *a : 1}", "{*a}", "{*a : }", "{*a:1}", "a: {*b : 1}\n", "a: [{*b : 1}]\n", "- {*a : 1}\n", "[*a : 1]",
    "{a: 1, a: 2, *b : 3}\n", "{<<: 1, *a : 2}\n", "{&a b: 1, *a : 2}\n", "{*a : *a}\n", "{*a : &b 1, *b : 2}\n", "{a: &x [1], *x : 2}", "{{*a : 1} : 2}",
    "{*a : 1, *a : 2}\n", "{*a : 1}\n{*a : 2}\n", "a:\n  {*b : 1}\n", "{a: {*b : 1}, c: 2}\n",
]


def seeds(upstream):
    out = [t for t in HAND] + EXTRA
    # texts already in the repository
    for f in sorted(glob.glob(os.path.join(ROOT, "corpus", "*", "vectors.yaml"))):
        raw = open(f, encoding="utf-8").read()
        out.append(raw)
        for c in yaml.safe_load(raw)["cases"]:
            for k in ("input_yaml", "a_yaml", "b_yaml"):
                if isinstance(c.get(k), str):
                    out.append(c[k])
    for f in ("subset.json",):
        d = json.load(open(os.path.join(ROOT, "testdata", "p1", f), encoding="utf-8"))
        for group in ("parse_ok", "unsupported", "syntax"):
            out += [v["yaml"] for v in d[group]]
        out += [v["yaml"] for v in d["emit"]]
    out.append(open(os.path.join(ROOT, "corpus", "pins.yaml"), encoding="utf-8").read())
    if upstream:
        p = os.path.join(upstream, "schemas", "atomic", "access.yaml")
        if os.path.exists(p):
            out.append(open(p, encoding="utf-8").read())
    return out


ALPHABET = list(":,-[]{}#&*!|>'\"%@`? \t\n\r") + list("abAB019_.=") + ["\u00e9", "\U0001F642", "\u0085", "\u2028", "\ufeff", "\u00a0", "---", "...", "- ", ": ", "? ", "  ", "\\", "\\n", "\\u0041"]


def mutate(rng, text):
    ops = rng.randint(1, 3)
    for _ in range(ops):
        n = len(text)
        k = rng.randrange(14)
        lines = text.split("\n")
        if k == 0 and n:
            i = rng.randrange(n)
            text = text[:i] + text[i + 1:]
        elif k in (1, 2):
            i = rng.randint(0, n)
            text = text[:i] + rng.choice(ALPHABET) + text[i:]
        elif k == 3 and n:
            i = rng.randrange(n)
            text = text[:i] + rng.choice(ALPHABET) + text[i + 1:]
        elif k == 4 and len(lines) > 1:
            i = rng.randrange(len(lines))
            lines.insert(i, lines[i])
            text = "\n".join(lines)
        elif k == 5 and len(lines) > 1:
            del lines[rng.randrange(len(lines))]
            text = "\n".join(lines)
        elif k == 6 and len(lines) > 2:
            i, j = rng.sample(range(len(lines)), 2)
            lines[i], lines[j] = lines[j], lines[i]
            text = "\n".join(lines)
        elif k == 7:
            lines.insert(rng.randint(0, len(lines)), rng.choice(["", "# c", "  ", "---", "..."]))
            text = "\n".join(lines)
        elif k == 8 and lines:
            i = rng.randrange(len(lines))
            lines[i] = rng.choice(["  ", " ", "\t", ""]) + lines[i]
            text = "\n".join(lines)
        elif k == 9:
            text = text.replace("\n", rng.choice(["\r\n", "\r"]))
        elif k == 10 and n:
            text = text[: rng.randrange(n)]
        elif k == 11 and n:
            i = rng.randrange(n)
            text = text[:i] + text[i:].replace(":", ": ", 1)
        elif k == 12:
            text = rng.choice(["", "\ufeff", "%YAML 1.2\n---\n", "--- "]) + text
        elif k == 13 and n:
            i, j = sorted((rng.randint(0, n), rng.randint(0, n)))
            text = text[:j] + text[i:j] + text[j:]
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--count", type=int, default=30000)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--upstream", default=os.environ.get("CIC_PRIMITIVES_ROOT"))
    a = ap.parse_args()
    rng = random.Random(a.seed)
    base = seeds(a.upstream)
    items = [t.encode("utf-8") for t in base]
    while len(items) < a.count:
        t = rng.choice(base)
        if len(t) > 6000:  # keep mutated inputs small; the big files are covered unmutated
            lines = t.split("\n")
            i = rng.randrange(max(1, len(lines) - 30))
            t = "\n".join(lines[i:i + 30])
        items.append(mutate(rng, t).encode("utf-8", "surrogatepass"))
    items += [t.encode("utf-8") for t in REGRESSIONS]  # after the random part: the seeded run above is unchanged by them
    # a few byte-level cases the text path cannot produce
    items += [b"\xff", b"a: \xc3\n", b"\xef\xbb", b"a: \xed\xa0\x80\n", b"\xfe\xff"]
    with open(a.out, "w") as fh:
        for b in items:
            fh.write(b.hex() + "\n")
    print("%d inputs (%d seeds)" % (len(items), len(base)), file=sys.stderr)


if __name__ == "__main__":
    main()
