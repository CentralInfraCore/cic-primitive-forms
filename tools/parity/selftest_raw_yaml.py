#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Selftest of the raw YAML differential, Unit 3c (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, section 7): the proof has to be able to fail, and this shows it does.

    selftest_raw_yaml.py [--out DIR] [--rust-target-dir DIR]        (runs the dumps of the unmodified tree first, then everything below)

The measured run has a real finding that is red on purpose (F1, issue #161: the Go parser is quadratic on a long flow sequence). The selftest therefore works on the input WITHOUT the cases
that are red in the unmodified run (listed in its result) and requires that, with them removed, the unmodified run is green: a mutant is then red or it is not.

 A. the comparison is broken on purpose: a dropped / duplicated / reordered / swapped / unknown / truncated / unfinished line, an id missing from all three, another input set, a changed
    input line in one reader, a value outside the vocabulary, a crash or timeout of ANY reader, a vanished or changed known difference, a changed pinned subtype, a thin class;
 B. a defect is planted in a COPY of the Rust parser and of the Go parser, one at a time: the compare must turn red on the named classes (RED), and on the restored copy it must be green
    again (GREEN); a defect that stays green is a finding about the cases, not a pass;
 C. the same defect in Rust and Go together: the two agree and are both wrong; the compare must still be red, through the `allowed` label and the construction (never through parity);
 D. the Python oracle is changed (it refuses a duplicate key; it reads the envelope as a string): the run must be red (the known differences stop matching; a difference on a read member);
 E. the harness itself: a reader that answers the same thing for every case, one that echoes the expectation, one that never runs;
 F. the evidence rule: no selftest, a dirty tree, a red compare or a failed selftest is never evidence.
The lists are committed; each planted defect names the classes of tools/parity/gen_raw_yaml.py that must turn red. Equivalent changes are listed with the reason and must stay green.
"""
import argparse
import collections
import copy
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_raw_yaml as cmp_  # noqa: E402
import run_raw_yaml as rr  # noqa: E402

ROOT = rr.ROOT
IMPLS = cmp_.IMPLS

# (name, language, file, old text (occurs once), new text, classes that must turn red). Measured first, then committed.
PLANTED = [
    ("a duplicate key is accepted", "go", "go/parse.go", "if _, dup := seen[k.Value]; dup {", "if _, dup := seen[k.Value]; dup && false {", ["dup"]),
    ("a tab is accepted", "go", "go/parse.go", 'if strings.Contains(body, "\\t") {', "if false {", ["syntax-text:tab-after-value"]),
    ("a second document is accepted", "go", "go/parse.go", "return nil, unsupported(\"more than one document\")", "", ["syntax-text:second-document"]),
    ("a merge key is followed", "go", "go/parse.go", 'if isPlain(k) && k.Value == "<<" {', "if false {", ["syntax-injected"]),
    ("a byte order mark inside the text is accepted", "go", "go/parse.go", 'if strings.Contains(body, "\\uFEFF") {', "if false {", ["syntax-text:bom-inside"]),
    ("a float or radix number is read as a string", "go", "go/parse.go", "if floatRe.MatchString(s) || radixRe.MatchString(s) {", "if false {", ["syntax-scalar"]),
    ("a leading zero is accepted", "go", "go/parse.go", "if len(m[1]) > 1 && m[1][0] == '0' {", "if false {", ["syntax-scalar"]),
    ("a block scalar's trailing newline is dropped", "go", "go/parse.go", "out.Kind, out.Str = KindStr, n.Value\n\t\t\treturn out, nil", 'out.Kind, out.Str = KindStr, strings.TrimRight(n.Value, "\\n")\n\t\t\treturn out, nil', ["value:block-digest"]),
    ("the depth limit is gone", "go", "go/parse.go", "MaxDepth: 64,", "MaxDepth: 100000,", ["limit-over:depth"]),
    ("the size limit is gone", "go", "go/parse.go", "MaxBytes: 1 << 20,", "MaxBytes: 1 << 30,", ["limit-over:size"]),
    ("a line separator is accepted", "go", "go/parse.go", 'strings.IndexAny(body, "\\u0085\\u2028\\u2029")', 'strings.IndexAny(body, "\\u0085")', ["syntax-text:ls-2028", "syntax-text:ps-2029"]),
    ("a duplicate key is accepted", "rust", "rust/src/parse.rs", "let duplicate = !merge && !seen.insert(value.to_string());", "let duplicate = !merge && !seen.insert(value.to_string()) && false;", ["dup"]),
    ("a tab is accepted", "rust", "rust/src/parse.rs", "if body.contains('\\t') {", "if false {", ["syntax-text:tab-after-value"]),
    ("a second document is accepted", "rust", "rust/src/parse.rs", "syntax error anywhere beats both.\n    if docs > 1 {", "syntax error anywhere beats both.\n    if docs > 1 && false {", ["syntax-text:second-document"]),
    ("a merge key is followed", "rust", "rust/src/parse.rs", 'let merge = plain && value == "<<";', "let merge = false;", ["syntax-injected"]),
    ("a byte order mark inside the text is accepted", "rust", "rust/src/parse.rs", "if body.contains('\\u{FEFF}') {", "if false {", ["syntax-text:bom-inside"]),
    ("a float or radix number is read as a string", "rust", "rust/src/parse.rs", "if is_float_like(s) || is_radix_number(s) {", "if false {", ["syntax-scalar"]),
    ("a leading zero is accepted", "rust", "rust/src/parse.rs", "if digits.len() > 1 && digits.starts_with('0') {", "if false {", ["syntax-scalar"]),
    ("a control character is accepted", "rust", "rust/src/parse.rs", "if let Some(c) = body.chars().find(", "if let Some(c) = body.chars().filter(|_| false).find(", ["syntax-text:nul"]),
    ("a block scalar's trailing newline is dropped", "rust", "rust/src/parse.rs", "                    Value::Str(value.into_owned())\n                };\n                let pos = if block", "                    Value::Str(if block { value.trim_end_matches('\\n').to_string() } else { value.into_owned() })\n                };\n                let pos = if block", ["value:block-digest"]),
    ("the depth limit is gone", "rust", "rust/src/parse.rs", "max_depth: 64,", "max_depth: 100000,", ["limit-over:depth"]),
    ("the size limit is gone", "rust", "rust/src/parse.rs", "max_bytes: 1 << 20,", "max_bytes: 1 << 30,", ["limit-over:size"]),
    ("a line separator is accepted", "rust", "rust/src/parse.rs", "['\\u{85}', '\\u{2028}', '\\u{2029}'].contains(c)", "['\\u{85}'].contains(c)", ["syntax-text:ls-2028", "syntax-text:ps-2029"]),
]
# a defect that needs a second edit in the same parser because two layers refuse the same construct (the pre-scan refuses a marker line, the decoder refuses the second document):
# both layers must be removed for the construct to get through. Measured: the single edit above stays green (the other layer still refuses), the pair is red.
EXTRA = {
    ("a second document is accepted", "go"): [("go/parse.go", "		if refused {\n\t\t\treturn unsupported(\"document marker\")", "		if refused && false {\n\t\t\treturn unsupported(\"document marker\")")],
    ("a second document is accepted", "rust"): [("rust/src/parse.rs", "        if refused {\n            return Err(Error::Unsupported(\"document marker\".into()));", "        if refused && false {\n            return Err(Error::Unsupported(\"document marker\".into()));")],
}
# The findings the unmodified run is ALLOWED to be red on. The selftest sets them aside to get a green baseline, and refuses to run if the unmodified run is red on anything else: a baseline
# that is red for another reason is a broken harness (or a reader built from stale or mutated code), not a finding.
# An accepted finding is bound to the EXACT result it was accepted with, not to the id: the Go reader times out, the Rust reader refuses with the node limit, PyYAML accepts. Any other
# answer on that id (the Rust reader crashing, the Go reader answering something else) is a new finding, and the baseline is red.
ACCEPTED_FINDINGS = {}     # F1 (issue #161, the Go parser quadratic on a long line) was fixed; an unmodified run that is red now stops the selftest. A new accepted finding needs its exact result, as below.
# A synthetic accepted finding, the shape of the former F1, to keep the guard itself under test:
SYNTHETIC_ACCEPTED_FINDING = {
    "limit-over-nodes-00001": {
        "why": "F1, issue #161: the Go parser is quadratic on a long flow sequence (TIMEOUT)",
        "go": {"ok": False, "class": "TIMEOUT"},
        "rust": {"ok": False, "class": "syntax", "code": "LIMIT_EXCEEDED"},
        "python": {"ok": True},
        "red_reason": "go: TIMEOUT",
    },
}
# Within the classes a common-mode defect is aimed at, some cases are refused by ANOTHER rule that the defect does not touch (the float defect leaves `03` to the leading-zero rule and
# `99999999999999999999` to the 64-bit rule). The aim is the set of cases the defect is meant to let through, decided from the text of the case, never from what turned red.
import re as _re
AIM = {
    "a merge key is followed": lambda text: bool(_re.search(r"(?m)^ *<<: ", text)),
    "a block scalar's trailing newline is dropped": lambda text: bool(_re.search(r"(?m)^  \w+: [|>]$", text)),     # the clip indicators keep the trailing newline; |- and >- strip it anyway
    "a float or radix number is read as a string": lambda text: bool(_re.search(r"(?m)^  envelope: (3\.0|3e0|\.5|5\.|1e3|\.inf|\.nan|0x3|0o3|\+3\.0|-\.inf)$", text)),
}
# the same defect in Rust and Go at once, by name (both entries of PLANTED are applied)
COMMON_MODE = ["a duplicate key is accepted", "a tab is accepted", "a merge key is followed", "a float or radix number is read as a string", "a byte order mark inside the text is accepted",
               "a block scalar's trailing newline is dropped"]
# changes that cannot alter an answer on any case of the input, with the reason (measured: they stay green)
EQUIVALENT = [
    ("go", "go/parse.go", "if c <= 0x08 || c == 0x0B || c == 0x0C || (c >= 0x0E && c <= 0x1F) || c == 0x7F {", "if false {",
     "the Go pre-scan check of control characters is redundant for every control-character case of the input: yaml.v3 refuses them itself (the Rust pre-scan is not redundant: its YAML library accepts them, see the Rust entry of PLANTED)"),
]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def lines_of(path):
    return read(path).splitlines()


class Trees:
    """A copy of the Go module and the Rust crate, in which one file at a time is mutated and restored, and the readers built from it."""

    def __init__(self, workdir, env, rust_target):
        self.dir, self.env = workdir, env
        self.tree = os.path.join(workdir, "tree")
        self.rust_target = rust_target or os.path.join(workdir, "rust-target")
        if os.path.exists(self.tree):
            shutil.rmtree(self.tree)
        os.makedirs(self.tree)
        shutil.copytree(os.path.join(ROOT, "go"), os.path.join(self.tree, "go"))
        shutil.copytree(os.path.join(ROOT, "rust"), os.path.join(self.tree, "rust"), ignore=shutil.ignore_patterns("target"))
        self.pristine = {}

    def mutate(self, rel, old, new):
        p = os.path.join(self.tree, rel)
        text = read(p)
        if text.count(old) != 1:
            raise SystemExit("the text to mutate occurs %d times in %s: %r" % (text.count(old), rel, old))
        self.pristine.setdefault(rel, text)          # the first state, whatever number of edits a file gets
        write(p, text.replace(old, new))

    def restore(self, rel):
        write(os.path.join(self.tree, rel), self.pristine.pop(rel))

    def build(self, lang):
        if lang == "go":
            out = os.path.join(self.dir, "go-selftest.test")
            p = subprocess.run(["go", "test", "-c", "-o", out, "."], cwd=os.path.join(self.tree, "go"), env=self.env, capture_output=True, text=True)
            if p.returncode:
                raise SystemExit("a mutant did not compile (Go):\n" + (p.stdout + p.stderr)[-800:])
            return out
        e = dict(self.env, CARGO_TARGET_DIR=self.rust_target)
        p = subprocess.run(["cargo", "test", "--test", "parity_raw_yaml_dump", "--no-run", "--message-format=json"], cwd=os.path.join(self.tree, "rust"), env=e, capture_output=True, text=True)
        if p.returncode:
            raise SystemExit("a mutant did not compile (Rust):\n" + p.stderr[-800:])
        exe = None
        for line in p.stdout.splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("reason") == "compiler-artifact" and m.get("target", {}).get("name") == "parity_raw_yaml_dump" and m.get("executable"):
                exe = m["executable"]
        return exe

    def dump(self, lang, lines, name):
        binary = self.build(lang)
        launch = rr.reader_launch(lang, binary)
        # the readers run from their own tree
        def launch_in_tree(inp, outp):
            cmd, cwd, extra = launch(inp, outp)
            return cmd, os.path.join(self.tree, "go" if lang == "go" else "rust"), extra
        out, stats = rr.run_dump(launch_in_tree, lines, self.env, self.dir, name)
        return out, stats


def write_dump(path, lines):
    write(path, "".join(l + "\n" for l in lines))


def new_red(rep, base_ids):
    return [r for r in rep["red"] if r["id"] not in base_ids]


def classes_of(rows):
    return {r["id"]: r["class"] for r in rows}


def run_all(out, env, rust_target, report_lines=None):
    work = os.path.join(out, "selftest")
    os.makedirs(work, exist_ok=True)
    known = json.load(open(rr.KNOWN, encoding="utf-8"))
    floors = json.load(open(rr.FLOORS, encoding="utf-8"))
    subtypes = json.load(open(rr.SUBTYPES, encoding="utf-8"))
    full = lines_of(os.path.join(out, "input.jsonl"))
    rows = [json.loads(l) for l in full]
    cls = classes_of(rows)
    # the baseline: the dumps of the run itself
    dumps_full = {i: lines_of(os.path.join(out, "dump-%s.jsonl" % i)) for i in IMPLS}
    paths = {i: os.path.join(out, "dump-%s.jsonl" % i) for i in IMPLS}
    base_full = cmp_.compare(os.path.join(out, "input.jsonl"), paths, known, floors, subtypes)
    removed = sorted(r["id"] for r in base_full["red"])
    by_id_rows = {lang: {json.loads(l)["id"]: json.loads(l) for l in dumps_full[lang]} for lang in IMPLS}
    answers = {i: {lang: by_id_rows[lang][i] for lang in IMPLS} for i in removed}
    unexpected = unexpected_baseline_red(removed, answers, {r["id"]: r["why"] for r in base_full["red"]})
    if unexpected or base_full["errors"]:
        result = {"passed": False, "why": "the unmodified run is red on something that is not an accepted finding", "unexpected_red": unexpected, "errors": base_full["errors"][:5], "accepted_findings": {k: v["why"] for k, v in ACCEPTED_FINDINGS.items()}}
        write(os.path.join(out, "selftest.json"), json.dumps(result, indent=1, sort_keys=True) + "\n")
        return result
    keep = [i for i, r in enumerate(rows) if r["id"] not in set(removed)]
    s_lines = [full[i] for i in keep]
    s_dumps = {k: [v[i] for i in keep] for k, v in dumps_full.items()}
    s_input = os.path.join(work, "input.jsonl")
    write(s_input, "".join(l + "\n" for l in s_lines))
    s_paths = {}
    for k in IMPLS:
        s_paths[k] = os.path.join(work, "base-%s.jsonl" % k)
        write_dump(s_paths[k], s_dumps[k])

    def compare(dumps, inp=s_input, kn=known, fl=None, st=subtypes):
        return cmp_.compare(inp, dumps, kn, fl, st)

    # the floors are those of the run, less the cases taken out (a class is not "thin" because a finding was set aside)
    floors = dict(floors)
    for rid in removed:
        k = cls[rid].split(":")[0]
        floors[k] = floors.get(k, 0) - 1 if floors.get(k, 0) else 0
    base = compare(s_paths, fl=floors)
    result = {"removed_from_the_input": removed, "removed_reason": "red in the unmodified run (a finding); the selftest needs a green baseline apart from them", "baseline_green": not base["is_red"],
              "baseline_cases": base["cases"]}
    if base["is_red"]:
        result.update({"passed": False, "why": "the baseline is red even without the removed cases", "baseline_errors": base["errors"][:5], "baseline_red": base["red"][:5]})
        write(os.path.join(out, "selftest.json"), json.dumps(result, indent=1, sort_keys=True) + "\n")
        return result

    # ---- A. breaking the comparison
    A = []

    def put(name, texts):
        d = {}
        for k in IMPLS:
            d[k] = os.path.join(work, "%s-%s.jsonl" % (name.replace(" ", "_")[:60], k))
            write(d[k], texts[k])
        return d

    def texts():
        return {k: "".join(l + "\n" for l in s_dumps[k]) for k in IMPLS}

    def case(name, make, **kw):
        t = texts()
        extra = make(t)
        inp = extra if isinstance(extra, str) else s_input
        rep = compare(put(name, t), inp=inp, **kw)
        A.append({"name": name, "red": rep["is_red"], "first": (rep["errors"] + [r["why"] for r in rep["red"]])[:2]})

    mid = len(s_lines) // 2

    def set_line(t, impl, idx, fn):
        ls = t[impl].splitlines(True)
        ls[idx] = fn(ls[idx])
        t[impl] = "".join(ls)

    def drop(t, impl, idx):
        ls = t[impl].splitlines(True)
        del ls[idx]
        t[impl] = "".join(ls)

    def swap_answers(t, impl, a, b):
        ls = t[impl].splitlines(True)
        ra, rb = json.loads(ls[a]), json.loads(ls[b])
        ls[a], ls[b] = json.dumps(dict(rb, id=ra["id"]), sort_keys=True) + "\n", json.dumps(dict(ra, id=rb["id"]), sort_keys=True) + "\n"
        t[impl] = "".join(ls)

    A.append({"name": "the untouched dumps are green", "red": base["is_red"], "ok": not base["is_red"]})
    for impl in IMPLS:
        case("a dropped line (%s)" % impl, lambda t, impl=impl: drop(t, impl, mid))
        case("a duplicated line (%s)" % impl, lambda t, impl=impl: set_line(t, impl, mid, lambda l: l + l))
        case("a swapped answer (%s)" % impl, lambda t, impl=impl: swap_answers(t, impl, mid, mid + 1))
        case("a reordered pair of lines (%s)" % impl, lambda t, impl=impl: _swap_lines(t, impl, mid, mid + 1))
        case("a truncated file (%s)" % impl, lambda t, impl=impl: t.__setitem__(impl, t[impl][: len(t[impl]) // 2]))
        case("an unfinished last line (%s)" % impl, lambda t, impl=impl: t.__setitem__(impl, t[impl] + '{"id": "zz-extra", "ok": true'))
        case("a complete dump without its final newline (%s)" % impl, lambda t, impl=impl: t.__setitem__(impl, t[impl][:-1]))
        case("an unknown id (%s)" % impl, lambda t, impl=impl: set_line(t, impl, mid, lambda l: l.replace(json.loads(l)["id"], "zz-unknown", 1)))
        case("a line that is not JSON (%s)" % impl, lambda t, impl=impl: set_line(t, impl, 5, lambda l: "{not json\n"))
        case("a word outside the vocabulary (%s)" % impl, lambda t, impl=impl: set_line(t, impl, 5, lambda l: json.dumps({"id": json.loads(l)["id"], "ok": False, "class": "mystery", "code": "X"}) + "\n"))
        case("a CRASH of this reader (%s)" % impl, lambda t, impl=impl: set_line(t, impl, 5, lambda l: json.dumps({"id": json.loads(l)["id"], "ok": False, "class": "CRASH", "code": "CRASH"}) + "\n"))
        case("a TIMEOUT of this reader (%s)" % impl, lambda t, impl=impl: set_line(t, impl, 5, lambda l: json.dumps({"id": json.loads(l)["id"], "ok": False, "class": "TIMEOUT", "code": "TIMEOUT"}) + "\n"))
        case("an empty dump (%s)" % impl, lambda t, impl=impl: t.__setitem__(impl, ""))
    case("a case missing from all three", lambda t: [drop(t, i, mid) for i in IMPLS] and None)
    # another input set in one dump: the generator at another seed, run through the Python oracle
    import gen_raw_yaml as gen
    other = os.path.join(work, "other-input.jsonl")
    write(other, gen.render(gen.generate(7).rows))
    other_dump = os.path.join(work, "other-dump.jsonl")
    subprocess.run([sys.executable, os.path.join(HERE, "dump_raw_yaml_py.py"), other, other_dump], check=True)
    other_rows = {json.loads(l)["id"]: l for l in lines_of(other_dump)}
    case("another input set in one reader's dump", lambda t: t.__setitem__("go", "".join(other_rows[json.loads(l)["id"]] + "\n" for l in s_dumps["go"])))
    # an input line changed in ONE reader's copy: the Rust reader is run on bytes that differ from the others'
    changed = list(s_lines)
    victim = next(i for i, l in enumerate(changed) if json.loads(l)["class"].startswith("built:"))
    c = json.loads(changed[victim])
    c["bytes_hex"] = c["bytes_hex"].replace("20", "21", 1)
    changed[victim] = json.dumps(c, sort_keys=True, ensure_ascii=True)
    # the mutants are built in a target directory of their own: sharing one with the run under test lets cargo take a build of a mutated copy for a fresh build of the real tree
    trees = Trees(work, env, None)
    got, _ = trees.dump("rust", changed, "changed-input")
    t = texts()
    t["rust"] = "".join(l + "\n" for l in got)
    rep = compare(put("changed input", t))
    A.append({"name": "a changed input line in one reader's copy", "red": rep["is_red"], "first": (rep["errors"] + [r["why"] for r in rep["red"]])[:1]})
    # a duplicated id in the input
    dupe_input = os.path.join(work, "dupe-input.jsonl")
    write(dupe_input, "".join(l + "\n" for l in s_lines + [s_lines[0]]))
    case("a duplicated id in the input", lambda t: dupe_input)
    # known differences
    ids = [e["id"] for e in known["entries"]]
    case("a known difference that no longer occurs", lambda t: None, kn={"entries": known["entries"] + [dict(known["entries"][0], id=[r["id"] for r in rows if r["class"].startswith("built:")][0])]})
    case("a known difference with another answer", lambda t: None, kn={"entries": [dict(known["entries"][0], python_tree_sha256="0" * 64)] + known["entries"][1:]})
    case("a known difference without its acceptance", lambda t: None, kn={"entries": [{k: v for k, v in known["entries"][0].items() if k != "acceptance"}] + known["entries"][1:]})
    case("a known difference whose id is not in the input", lambda t: None, kn={"entries": known["entries"] + [dict(known["entries"][0], id="zz-not-an-id")]})
    case("a changed pinned subtype", lambda t: None, st={k: dict(v, subtype="refusal_position_only") if v["subtype"] == "positions_only" else v for k, v in subtypes.items()})
    case("a pinned subtype whose original answers changed", lambda t: None, st={k: dict(v, original={"rust": [[0, 0, 0]], "go": [[0, 0, 1]]}) for k, v in subtypes.items()})
    case("a thin class", lambda t: None, fl=dict(floors, built=10 ** 9))
    A.append(_evidence_rule_check())
    f1 = "limit-over-nodes-00001"
    good = {"go": {"ok": False, "class": "TIMEOUT"}, "rust": {"ok": False, "class": "syntax", "code": "LIMIT_EXCEEDED"}, "python": {"ok": True}}
    guard = [
        ("an unmodified run red on another id stops the selftest", bool(unexpected_baseline_red([f1, "value-block-digest-00003"], {f1: good}, {f1: "go: TIMEOUT"}, SYNTHETIC_ACCEPTED_FINDING))),
        ("the accepted finding with exactly its accepted result is accepted", not unexpected_baseline_red([f1], {f1: good}, {f1: "go: TIMEOUT"}, SYNTHETIC_ACCEPTED_FINDING)),
        ("the accepted id with the Rust reader crashing is a new finding", bool(unexpected_baseline_red([f1], {f1: dict(good, rust={"ok": False, "class": "CRASH", "code": "CRASH"})}, {f1: "go: TIMEOUT"}, SYNTHETIC_ACCEPTED_FINDING))),
        ("the accepted id with the Go reader answering something else is a new finding", bool(unexpected_baseline_red([f1], {f1: dict(good, go={"ok": False, "class": "CRASH"})}, {f1: "go: TIMEOUT"}, SYNTHETIC_ACCEPTED_FINDING))),
        ("the accepted id with a different red reason is a new finding", bool(unexpected_baseline_red([f1], {f1: good}, {f1: "rust: CRASH"}, SYNTHETIC_ACCEPTED_FINDING))),
        ("the accepted id with PyYAML refusing is a new finding", bool(unexpected_baseline_red([f1], {f1: dict(good, python={"ok": False})}, {f1: "go: TIMEOUT"}, SYNTHETIC_ACCEPTED_FINDING))),
    ]
    for name, held in guard:
        A.append({"name": "baseline guard: " + name, "red": held, "ok": held})
    a_ok = all((x.get("ok") if "ok" in x else x["red"]) for x in A)

    # ---- B. planted defects, one parser at a time
    B = []
    for name, lang, rel, old, new, targets in PLANTED:
        edits = [(rel, old, new)] + EXTRA.get((name, lang), [])
        for r_, o_, n_ in edits:
            trees.mutate(r_, o_, n_)
        try:
            got, st = trees.dump(lang, s_lines, "mut-%s" % lang)
        finally:
            for r_, _o, _n in reversed(edits):
                trees.restore(r_) if r_ in trees.pristine else None
        d = dict(s_paths)
        d[lang] = os.path.join(work, "mut-%s.jsonl" % lang)
        write_dump(d[lang], got)
        red = compare(d)
        red_classes = sorted({cls[r["id"]] for r in red["red"]})
        got2, _ = trees.dump(lang, s_lines, "restored-%s" % lang)
        d2 = dict(s_paths)
        d2[lang] = os.path.join(work, "restored-%s.jsonl" % lang)
        write_dump(d2[lang], got2)
        green = compare(d2)
        hit = all(any(c == t or c.startswith(t) for c in {cls[r["id"]] for r in red["red"]}) for t in targets)
        B.append({"defect": name, "language": lang, "red_cases": red["red_cases"], "red_classes": red_classes, "expected": targets, "red": red["is_red"],
                  "events": sorted(red["events"].keys()), "green_after_restore": not green["is_red"], "ok": red["is_red"] and hit and not green["is_red"]})

    # ---- C. the same defect in Rust and Go. For the cases the defect is aimed at, it is not enough that the run turned red: Rust and Go must AGREE (semantically), the agreed answer must be
    # the wrong one (not the constructed one), and the run must be red for exactly the reason that the normative check gives: a forbidden construct in P_parity (allowed false) for a text that
    # must be refused, "a tree that is not the constructed one" for a text that must be accepted. Parity must NOT be what caught it.
    C = []
    for name in COMMON_MODE:
        rows_ = [p for p in PLANTED if p[0] == name]
        aimed = sorted({tg for p in rows_ for tg in p[5]})
        dumps = dict(s_paths)
        try:
            for _, lang, rel, old, new, _t in rows_:
                trees.mutate(rel, old, new)
            for _, lang, rel, old, new, _t in rows_:
                got, _ = trees.dump(lang, s_lines, "common-%s" % lang)
                dumps[lang] = os.path.join(work, "common-%s.jsonl" % lang)
                write_dump(dumps[lang], got)
        finally:
            for _, lang, rel, old, new, _t in reversed(rows_):
                if rel in trees.pristine:
                    trees.restore(rel)
        rep = compare(dumps)
        by = {lang: {json.loads(l)["id"]: json.loads(l) for l in lines_of(dumps[lang])} for lang in ("rust", "go")}
        lab = {json.loads(l)["id"]: json.loads(l) for l in s_lines}
        reds = {r["id"]: r["why"] for r in rep["red"]}
        aim_text = AIM.get(name, lambda text: True)
        in_aim = lambda i: any(cls[i] == tg or cls[i].startswith(tg) for tg in aimed) and aim_text(bytes.fromhex(lab[i]["bytes_hex"]).decode("utf-8", "replace"))
        # the aimed set comes from the INPUT, not from what turned red: a case the defect should have let through and did not is a miss
        targets = [i for i in lab if in_aim(i)]
        missed = [i for i in targets if i not in reds]
        agree = [i for i in targets if i in reds and by["rust"][i]["ok"] and by["go"][i]["ok"] and by["rust"][i]["tree"] == by["go"][i]["tree"]]
        wrong = [i for i in agree if by["rust"][i]["tree"] != (lab[i]["parse_expect"].get("tree"))]
        def exact(i):
            pe = lab[i]["parse_expect"]["result"]
            if pe == "refuse":
                return "forbidden construct in P_parity" in reds[i]
            if pe == "accept":
                return "not the constructed one" in reds[i]
            return False
        exact_reason = [i for i in agree if exact(i)]
        forbidden = rep["events"].get("forbidden_construct_in_parity", [])
        ok = bool(targets) and not missed and len(agree) == len(targets) == len(wrong) == len(exact_reason)
        C.append({"defect": name, "red": rep["is_red"], "aimed_classes": aimed, "aimed_cases": len(targets), "aimed_cases_still_green": len(missed), "rust_and_go_agree": len(agree), "agreed_answer_is_not_the_constructed_one": len(wrong),
                  "red_for_the_normative_reason": len(exact_reason), "forbidden_construct_in_parity": len(forbidden), "other_red_cases": rep["red_cases"] - len(targets), "ok": ok})

    # ---- the equivalent changes: they must stay green
    EQ = []
    for lang, rel, old, new, why in EQUIVALENT:
        trees.mutate(rel, old, new)
        try:
            got, _ = trees.dump(lang, s_lines, "equiv-%s" % lang)
        finally:
            trees.restore(rel)
        d = dict(s_paths)
        d[lang] = os.path.join(work, "equiv-%s.jsonl" % lang)
        write_dump(d[lang], got)
        rep_ = compare(d)
        EQ.append({"language": lang, "change": old.strip()[:80], "why": why, "green": not rep_["is_red"], "ok": not rep_["is_red"]})

    # ---- D. the Python oracle changed
    D = []
    for variant in ("strict-duplicates", "stringify-envelope"):
        pth = os.path.join(work, "oracle-%s.jsonl" % variant)
        subprocess.run([sys.executable, os.path.join(HERE, "dump_raw_yaml_py.py"), s_input, pth, "--variant", variant], check=True)
        d = dict(s_paths)
        d["python"] = pth
        rep = compare(d)
        D.append({"oracle": variant, "red": rep["is_red"], "errors": rep["errors"][:2], "events": sorted(rep["events"].keys()), "ok": rep["is_red"]})

    # ---- E. the harness itself
    E = []
    expect_lines = []
    for l in s_lines:
        c = json.loads(l)
        pe = c["parse_expect"]
        if pe["result"] == "accept":
            expect_lines.append(json.dumps({"id": c["id"], "ok": True, "tree": pe["tree"], "positions": [], "mapping": ({"ok": c["mapping_expect"]["expect"]} if c["mapping_expect"] and c["mapping_expect"]["refuse"] is None else ({"refuse": c["mapping_expect"]["refuse"], "field": c["mapping_expect"]["field"]} if c["mapping_expect"] else None))}, sort_keys=True))
        else:
            cl = pe.get("class", "syntax")
            expect_lines.append(json.dumps({"id": c["id"], "ok": False, "class": "syntax" if cl == "any" else cl, "code": "ECHO", "locations": []}, sort_keys=True))
    canned = [json.dumps({"id": json.loads(l)["id"], "ok": True, "tree": {"k": "null", "v": None}, "positions": [], "mapping": None}, sort_keys=True) for l in s_lines]
    for name, lines in (("a reader that answers the same thing for every case", canned), ("a reader that echoes the expectation", expect_lines), ("a reader that never runs", [])):
        d = dict(s_paths)
        d["rust"] = d["go"] = os.path.join(work, "harness-%s.jsonl" % name.split()[2])
        write_dump(d["rust"], lines)
        rep = compare(d)
        E.append({"harness": name, "red": rep["is_red"], "red_cases": rep["red_cases"], "errors": len(rep["errors"]), "ok": rep["is_red"]})

    ok_all = a_ok and all(x["ok"] for x in B + C + D + E + EQ)
    result.update({"passed": ok_all, "comparison_breakage": A, "planted_defects": B, "common_mode": C, "oracle_changes": D, "harness": E, "equivalent_changes": EQ})
    write(os.path.join(out, "selftest.json"), json.dumps(result, indent=1, sort_keys=True) + "\n")
    print("selftest: baseline cases %d (removed %d), breakage %d/%d, planted %d/%d, common mode %d/%d, oracle %d/%d, harness %d/%d" % (
        base["cases"], len(removed), sum(1 for x in A if (x.get("ok") if "ok" in x else x["red"])), len(A), sum(x["ok"] for x in B), len(B), sum(x["ok"] for x in C), len(C),
        sum(x["ok"] for x in D), len(D), sum(x["ok"] for x in E), len(E)))
    for x in B + C + D + E + EQ:
        if not x["ok"]:
            print("  NOT OK:", json.dumps(x, sort_keys=True)[:300])
    for x in A:
        if not (x.get("ok") if "ok" in x else x["red"]):
            print("  NOT OK:", json.dumps(x, sort_keys=True)[:300])
    return result


def unexpected_baseline_red(red_ids, answers=None, reasons=None, accepted=None):
    """The red ids that are not accepted findings, and the accepted ones whose result is not exactly the accepted result. `answers`: {id: {"go": row, "rust": row, "python": row}}, `reasons`: {id: why}."""
    bad = []
    for i in red_ids:
        f = (ACCEPTED_FINDINGS if accepted is None else accepted).get(i)
        if f is None:
            bad.append(i)
        elif answers is not None:
            for lang in ("go", "rust", "python"):
                row = answers[i][lang]
                if any(row.get(k) != v for k, v in f[lang].items()):
                    bad.append("%s (%s answered %s, accepted %s)" % (i, lang, {k: row.get(k) for k in f[lang]}, f[lang]))
            if reasons is not None and reasons.get(i) != f["red_reason"]:
                bad.append("%s (red for %r, accepted for %r)" % (i, reasons.get(i), f["red_reason"]))
    return bad


def _swap_lines(t, impl, a, b):
    ls = t[impl].splitlines(True)
    ls[a], ls[b] = ls[b], ls[a]
    t[impl] = "".join(ls)


def _evidence_rule_check():
    ev = [rr.evidence_of(True, False, {"passed": True}), rr.evidence_of(True, False, None), rr.evidence_of(False, False, {"passed": True}),
          rr.evidence_of(True, True, {"passed": True}), rr.evidence_of(True, False, {"passed": False})]
    return {"name": "evidence needs a clean tree, a green compare and a passed selftest (and not a missing, failed or dirty one)", "red": ev == [True, False, False, False, False], "ok": ev == [True, False, False, False, False]}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "raw-yaml-diff"))
    ap.add_argument("--rust-target-dir")
    a = ap.parse_args(argv)
    return rr.main(["--out", a.out, "--selftest"] + (["--rust-target-dir", a.rust_target_dir] if a.rust_target_dir else []))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
