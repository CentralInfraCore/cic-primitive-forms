#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Selftest of the digest mapping differential (docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md, section 7): the proof has to be able to fail, and this shows it does.

    selftest_digest_mapping.py [--out DIR]          (runs the dumps of the unmodified tree first, then everything below)

 A. the comparison is broken on purpose, in every way the contract names (a dropped / duplicated / reordered / swapped line, an id missing from all three, an unknown id, a
    truncated file, another input set, a value outside the vocabulary, a vanished or changed known difference, a thin class): each must turn the compare red, and the untouched
    dumps must be green;
 B. a defect is planted, in a COPY of the source (never in the tree), in each of Python, Go and Rust: the compare must turn red on the cases named for it (RED), and on the
    unmodified copy it must be green again (GREEN). A defect that stays green is a finding about the case set, not a pass;
 C. the same defect in all three at once: the three agree and are all wrong, so only the construction can see it;
 D. changes that cannot change an answer are listed as EQUIVALENT with the reason; they must stay green.
The lists below are committed; the cases named for a defect are class prefixes of tools/parity/gen_digest_mapping.py ("b64", "defect", "pair", "valid", "shared").
"""
import argparse
import copy
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_digest_mapping as cmp_  # noqa: E402
import run_digest_mapping as rdm  # noqa: E402

ROOT = rdm.ROOT
IMPLS = cmp_.IMPLS

# (name, language, relative file, old text (occurs once), new text, class prefixes that must turn red)
PLANTED = [
    ("the padding is not required", "go", "go/digestmapping.go", "if len(s) != 44 || s[43] != '=' {", "if len(s) != 44 {", ["b64"]),
    ("the canonical spelling is not required", "go", "go/digestmapping.go", "if base64.StdEncoding.EncodeToString(raw) != text {", "if false {", ["b64"]),
    ("the provenance envelope is not checked", "go", "go/digestmapping.go", '[]string{"release.envelope", "provenance.envelope"}', '[]string{"release.envelope"}', ["defect"]),
    ("provenance is not cross-checked", "go", "go/digestmapping.go", "if cross != primary {", "if false && cross != primary {", ["defect"]),
    ("the pin is not checked", "go", "go/digestmapping.go", 'if "sha256:"+pinned != primary {', 'if false && "sha256:"+pinned != primary {', ["defect"]),
    ("a path listed twice is accepted", "go", "go/digestmapping.go", "if len(found) > 1 {", "if false {", ["defect"]),
    ("null is judged as a type error", "go", "go/digestmapping.go", "if v == nil || v.Kind == KindNull {", "if v == nil {", ["defect"]),
    ("a path that is a prefix is accepted", "go", "go/digestmapping.go", "p.Kind == KindStr && p.Str == path", "p.Kind == KindStr && strings.HasPrefix(p.Str, path)", ["defect"]),
    ("the float 3.0 is read as 3", "go", "go/digestmapping.go", "if v == nil || v.Kind != KindInt || v.Int != 3 {", "if v == nil || (v.Kind != KindInt && v.Kind != KindUnsupported) || (v.Kind == KindInt && v.Int != 3) {", ["defect"]),
    ("uppercase hex is a receipt digest", "go", "go/receipt.go", "(c < '0' || c > '9') && (c < 'a' || c > 'f')", "(c < '0' || c > '9') && (c < 'a' || c > 'f') && (c < 'A' || c > 'F')", ["defect"]),
    ("the padding is not required", "rust", "rust/src/digest_mapping.rs", "b.len() == 44 && b[43] == b'=' && ", "b.len() == 44 && ", ["b64"]),
    ("the canonical spelling is not required", "rust", "rust/src/digest_mapping.rs", "if encode_base64(raw) != text {", "if false {", ["b64"]),
    ("the provenance envelope is not checked", "rust", "rust/src/digest_mapping.rs", '["release.envelope", "provenance.envelope"]', '["release.envelope"]', ["defect"]),
    ("provenance is not cross-checked", "rust", "rust/src/digest_mapping.rs", "if cross != primary {", "if false && cross != primary {", ["defect"]),
    ("the pin is not checked", "rust", "rust/src/digest_mapping.rs", 'Some(pinned) if format!("sha256:{pinned}") != primary => {', 'Some(pinned) if false && format!("sha256:{pinned}") != primary => {', ["defect"]),
    ("a path listed twice is accepted", "rust", "rust/src/digest_mapping.rs", "_ => Err(refused(MAPPING_ARTIFACT_DUPLICATE, path)),", "_ => entry_digest(found[0], path),", ["defect"]),
    ("null is judged as a type error", "rust", "rust/src/digest_mapping.rs",
     "        Some(Node {\n            value: Value::Null, ..\n        }) => Err(refused(MAPPING_FIELD_MISSING, member)),\n", "", ["defect"]),
    ("a path that is a prefix is accepted", "rust", "rust/src/digest_mapping.rs", "}) => p == path,", "}) => p.starts_with(path),", ["defect"]),
    ("the float 3.0 is read as 3", "rust", "rust/src/digest_mapping.rs", "value: Value::Int(3),", "value: Value::Int(3) | Value::Unsupported(_),", ["defect"]),
    ("uppercase hex is a receipt digest", "rust", "rust/src/digest_mapping.rs", "matches!(b, b'0'..=b'9' | b'a'..=b'f')", "matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F')", ["defect"]),
    ("the padding is not required", "python", "tools/p5/digest_mapping.py", 'BASE64_32_BYTES = re.compile(r"[A-Za-z0-9+/]{43}=")', 'BASE64_32_BYTES = re.compile(r"[A-Za-z0-9+/]{43}=?")', ["b64"]),
    ("the shape is a prefix match", "python", "tools/p5/digest_mapping.py", "BASE64_32_BYTES.fullmatch(value)", "BASE64_32_BYTES.match(value)", ["b64"]),
    ("the canonical spelling is not required", "python", "tools/p5/digest_mapping.py", 'if base64.b64encode(raw).decode("ascii") != value:', "if False:", ["b64"]),
    ("provenance is not cross-checked", "python", "tools/p5/digest_mapping.py", "if cross != primary:", "if False:", ["defect"]),
    ("the pin is not checked", "python", "tools/p5/digest_mapping.py", 'if "sha256:" + pinned != primary:', "if False:", ["defect"]),
    ("the envelope is not required to be an integer", "python", "tools/p5/digest_mapping.py", "type(value) is not int or value != 3", "value != 3", ["defect"]),
    ("a path listed twice is accepted", "python", "tools/p5/digest_mapping.py", "if len(entries) > 1:", "if False:", ["defect"]),
    ("uppercase hex is a receipt digest", "python", "tools/p5/digest_mapping.py", 'RECEIPT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")', 'RECEIPT_DIGEST = re.compile(r"sha256:[0-9a-fA-F]{64}")', ["defect"]),
]
# the same defect in all three implementations at once (names of PLANTED, one entry per language each)
COMMON_MODE = ["the canonical spelling is not required", "provenance is not cross-checked", "the pin is not checked", "a path listed twice is accepted", "uppercase hex is a receipt digest"]
# changes that cannot alter an answer on any input: (language, file, old, new, why)
EQUIVALENT = [
    ("go", "go/digestmapping.go", "if len(raw) != 32 {", "if false {", "the shape (43 characters and one padding) already means 32 bytes: no input reaches the length guard"),
    ("rust", "rust/src/digest_mapping.rs", "if raw.len() != 32 {", "if false {", "the shape (43 characters and one padding) already means 32 bytes: no input reaches the length guard"),
    ("python", "tools/p5/digest_mapping.py", '    if len(raw) != 32:\n        raise Refusal("base64_length", field)\n', "", "the shape (43 characters and one padding) already means 32 bytes: no input reaches the length guard"),
]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def classes_of(inp):
    return {json.loads(l)["id"]: json.loads(l).get("class", "shared").split(":")[0] for l in read(inp).splitlines()}


def compare_with(inp, dumps, known, floors):
    return cmp_.compare(inp, dumps, known, floors)


# ---- A. breaking the comparison -------------------------------------------------------------------------------------------------------------------------------------

def breakages(inp, dumps, known, floors, workdir):
    lines = {i: read(dumps[i]).splitlines(True) for i in IMPLS}
    cases = [json.loads(l) for l in read(inp).splitlines()]
    n = len(cases)

    def put(name, texts):
        paths = {}
        for i in IMPLS:
            paths[i] = os.path.join(workdir, "%s-%s.jsonl" % (name.replace(" ", "_"), i))
            write(paths[i], texts[i])
        return paths

    def base():
        return {i: "".join(lines[i]) for i in IMPLS}

    def replace_line(texts, impl, idx, fn):
        ls = texts[impl].splitlines(True)
        ls[idx] = fn(ls[idx])
        texts[impl] = "".join(ls)

    k = n // 2
    out = []

    def case(name, make, known_=known, floors_=floors, input_=inp):
        texts = base()
        extra = make(texts)
        rep = compare_with(extra or input_, put(name, texts), known_, floors_)
        out.append({"name": name, "red": rep["is_red"], "errors": rep["errors"][:3], "red_cases": rep["red_cases"]})

    out_green = compare_with(inp, put("untouched", base()), known, floors)
    out.append({"name": "the untouched dumps are green", "red": False, "expected_red": False, "ok": not out_green["is_red"]})

    def drop(texts, impl, idx):
        ls = texts[impl].splitlines(True)
        del ls[idx]
        texts[impl] = "".join(ls)

    for impl in IMPLS:
        case("a dropped line (%s)" % impl, lambda t, impl=impl: drop(t, impl, k))
        case("a duplicated line (%s)" % impl, lambda t, impl=impl: replace_line(t, impl, k, lambda l: l + l))
        case("a swapped answer (%s)" % impl, lambda t, impl=impl: _swap(t, impl, k, k + 1))
        case("a reordered pair of lines (%s)" % impl, lambda t, impl=impl: _swap_lines(t, impl, k, k + 1))
        case("a truncated file (%s)" % impl, lambda t, impl=impl: t.__setitem__(impl, t[impl][: len(t[impl]) // 2]))
        case("an unknown id (%s)" % impl, lambda t, impl=impl: replace_line(t, impl, k, lambda l: l.replace(json.loads(l)["id"], "zz-unknown", 1)))
        case("a value outside the vocabulary (%s)" % impl, lambda t, impl=impl: replace_line(t, impl, 5, lambda l: json.dumps({"id": json.loads(l)["id"], "ok": False, "reason": "mystery"}) + "\n"))
        case("a crash written as a defined reason is a crash (%s)" % impl, lambda t, impl=impl: replace_line(t, impl, 5, lambda l: json.dumps({"id": json.loads(l)["id"], "ok": False, "reason": "CRASH", "detail": "X"}) + "\n"))
        case("an answer that is not JSON (%s)" % impl, lambda t, impl=impl: replace_line(t, impl, 5, lambda l: "{not json\n"))
        case("an empty dump (%s)" % impl, lambda t, impl=impl: t.__setitem__(impl, ""))
    case("a case missing from all three", lambda t: [drop(t, i, k) for i in IMPLS] and None)

    # another input set in one dump: the same Python reference on a different seed
    other = os.path.join(workdir, "other-input.jsonl")
    text, params = rdm.build_input(7, 40, 400, 40)
    write(other, text)
    odump = os.path.join(workdir, "other-dump.jsonl")
    rdm.dump_python(other, odump)
    case("another seed in one dump", lambda t: t.__setitem__("go", read(odump)))

    # an input set that has a duplicated id
    dupe_input = os.path.join(workdir, "dupe-input.jsonl")
    ls = read(inp).splitlines(True)
    write(dupe_input, "".join(ls + [ls[0]]))
    case("a duplicated id in the input", lambda t: dupe_input)
    # an input file whose digest is not the generator's
    bad = os.path.join(workdir, "bad-input.jsonl")
    write(bad, read(inp).replace("conv-00000", "conv-0000X", 1))
    try:
        rdm.verify_input(bad, {"seed": rdm.gen.DEFAULT_SEED, "values": 40, "pairs": 400, "valid": 40})
        out.append({"name": "an input file that is not the generator's output", "red": False})
    except SystemExit:
        out.append({"name": "an input file that is not the generator's output", "red": True})

    # known differences
    first = cases[0]["id"]
    ans = {i: json.loads(lines[i][0]) for i in IMPLS}
    entry = {"id": first, "reason": "selftest", "answers": {i: ["ok", ans[i]["value"]] for i in IMPLS}}
    case("a known difference that no longer occurs", lambda t: None, known_={"entries": [dict(entry, id=cases[1]["id"], answers={i: ["refuse", "x", None] for i in IMPLS})]})
    case("a known difference with another answer", lambda t: None, known_={"entries": [dict(entry, answers={i: ["ok", "sha256:" + "0" * 64] for i in IMPLS})]})
    case("a thin run", lambda t: None, floors_=dict(floors, b64=10 ** 9))
    return out


def _swap(texts, impl, a, b):
    ls = texts[impl].splitlines(True)
    ra, rb = json.loads(ls[a]), json.loads(ls[b])
    ra2, rb2 = dict(rb, id=ra["id"]), dict(ra, id=rb["id"])
    ls[a], ls[b] = json.dumps(ra2, sort_keys=True) + "\n", json.dumps(rb2, sort_keys=True) + "\n"
    texts[impl] = "".join(ls)


def _swap_lines(texts, impl, a, b):
    ls = texts[impl].splitlines(True)
    ls[a], ls[b] = ls[b], ls[a]
    texts[impl] = "".join(ls)


# ---- B, C, D. planted defects ---------------------------------------------------------------------------------------------------------------------------------------

class Trees:
    """A copy of the source of each language, in which one file at a time is mutated and restored."""

    def __init__(self, workdir, env, rust_target):
        self.dir, self.env = workdir, env
        self.tree = os.path.join(workdir, "tree")
        self.target = rust_target or os.path.join(workdir, "rust-target")
        if os.path.exists(self.tree):
            shutil.rmtree(self.tree)
        os.makedirs(self.tree)
        shutil.copytree(os.path.join(ROOT, "go"), os.path.join(self.tree, "go"))
        shutil.copytree(os.path.join(ROOT, "rust"), os.path.join(self.tree, "rust"), ignore=shutil.ignore_patterns("target"))
        for d in ("testdata", "corpus"):
            if os.path.isdir(os.path.join(ROOT, d)):
                shutil.copytree(os.path.join(ROOT, d), os.path.join(self.tree, d))
        os.makedirs(os.path.join(self.tree, "tools", "p5"))
        shutil.copy(os.path.join(ROOT, "tools", "p5", "digest_mapping.py"), os.path.join(self.tree, "tools", "p5", "digest_mapping.py"))
        self.pristine = {}

    def path(self, rel):
        return os.path.join(self.tree, rel)

    def mutate(self, rel, old, new):
        p = self.path(rel)
        text = read(p)
        self.pristine[rel] = text
        if text.count(old) != 1:
            raise SystemExit("the text to mutate occurs %d times in %s: %r" % (text.count(old), rel, old))
        write(p, text.replace(old, new))

    def restore(self, rel):
        write(self.path(rel), self.pristine.pop(rel))

    def dump(self, lang, inp, out):
        if lang == "go":
            rdm.dump_go(self.tree, inp, out, self.env)
        elif lang == "rust":
            rdm.dump_rust(self.tree, inp, out, self.env, self.target)
        else:
            rdm.dump_python(inp, out, self.path("tools/p5/digest_mapping.py"))


def planted(inp, base_dumps, known, floors, trees, workdir, cls):
    results = []
    for name, lang, rel, old, new, targets in PLANTED:
        trees.mutate(rel, old, new)
        try:
            d = os.path.join(workdir, "planted-%s-%s.jsonl" % (lang, name.replace(" ", "_")))
            trees.dump(lang, inp, d)
            red = compare_with(inp, dict(base_dumps, **{lang: d}), known, floors)
        finally:
            trees.restore(rel)
        d2 = os.path.join(workdir, "restored-%s-%s.jsonl" % (lang, name.replace(" ", "_")))
        trees.dump(lang, inp, d2)
        green = compare_with(inp, dict(base_dumps, **{lang: d2}), known, floors)
        got = sorted({cls[r["id"]] for r in red["red"]})
        ok = red["is_red"] and all(t in got for t in targets) and not green["is_red"]
        results.append({"defect": name, "language": lang, "red_cases": red["red_cases"], "red_classes": got, "expected_classes": targets, "red": red["is_red"], "green_after_restore": not green["is_red"], "ok": ok})
    return results


def common_mode(inp, known, floors, trees, workdir, cls):
    results = []
    for name in COMMON_MODE:
        rows = [p for p in PLANTED if p[0] == name]
        dumps = {}
        try:
            for _, lang, rel, old, new, _t in rows:
                trees.mutate(rel, old, new)
            for _, lang, rel, old, new, _t in rows:
                dumps[lang] = os.path.join(workdir, "common-%s-%s.jsonl" % (lang, name.replace(" ", "_")))
                trees.dump(lang, inp, dumps[lang])
        finally:
            for _, lang, rel, old, new, _t in reversed(rows):
                if rel in trees.pristine:
                    trees.restore(rel)
        rep = compare_with(inp, dumps, known, floors)
        agreed_but_wrong = sum(1 for r in rep["red"] if len({json.dumps(v) for v in r["answers"].values()}) == 1)
        results.append({"defect": name, "languages": sorted(dumps), "red": rep["is_red"], "red_cases": rep["red_cases"], "all_three_agree_and_are_wrong": agreed_but_wrong, "ok": rep["is_red"] and agreed_but_wrong > 0 and len(dumps) == 3})
    return results


def equivalents(inp, base_dumps, known, floors, trees, workdir):
    results = []
    for lang, rel, old, new, why in EQUIVALENT:
        trees.mutate(rel, old, new)
        try:
            d = os.path.join(workdir, "equiv-%s.jsonl" % lang)
            trees.dump(lang, inp, d)
            rep = compare_with(inp, dict(base_dumps, **{lang: d}), known, floors)
        finally:
            trees.restore(rel)
        results.append({"language": lang, "change": old.strip(), "why": why, "green": not rep["is_red"], "ok": not rep["is_red"]})
    return results


def run_all(out, env, dumps, inp, rust_target=None):
    workdir = os.path.join(out, "selftest")
    os.makedirs(workdir, exist_ok=True)
    known = json.load(open(rdm.KNOWN, encoding="utf-8"))
    floors = json.load(open(rdm.FLOORS, encoding="utf-8"))
    cls = classes_of(inp)
    a = breakages(inp, dumps, known, floors, workdir)
    a_ok = all((x.get("ok") if "ok" in x else x["red"]) for x in a)
    trees = Trees(workdir, env, rust_target)
    b = planted(inp, dumps, known, floors, trees, workdir, cls)
    c = common_mode(inp, known, floors, trees, workdir, cls)
    d = equivalents(inp, dumps, known, floors, trees, workdir)
    passed = a_ok and all(x["ok"] for x in b + c + d)
    res = {"passed": passed, "comparison_breakage": a, "planted_defects": b, "common_mode": c, "equivalent_changes": d}
    write(os.path.join(out, "selftest.json"), json.dumps(res, indent=1, sort_keys=True) + "\n")
    print("selftest: breakage %d/%d, planted %d/%d, common mode %d/%d, equivalent %d/%d" % (
        sum(1 for x in a if (x.get("ok") if "ok" in x else x["red"])), len(a), sum(x["ok"] for x in b), len(b), sum(x["ok"] for x in c), len(c), sum(x["ok"] for x in d), len(d)))
    for x in b + c + d:
        if not x["ok"]:
            print("  NOT OK:", json.dumps(x, sort_keys=True))
    for x in a:
        if not (x.get("ok") if "ok" in x else x["red"]):
            print("  NOT OK:", json.dumps(x, sort_keys=True))
    return res


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "digest-mapping-diff"))
    a = ap.parse_args(argv)
    # the dumps of the unmodified tree, then the selftest on them
    return rdm.main(["--out", a.out])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
