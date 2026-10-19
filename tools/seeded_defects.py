#!/usr/bin/env python3
"""Seeded defects: the test suites must catch an implementation that is deliberately wrong.

A gate that cannot fail proves nothing, and neither does a test suite that has never been shown to fail on
the defects it exists to catch. For each defect below, this copies the Rust crate (or the Go module) to a
temporary directory, applies one textual change (it must match exactly once, so a stale entry cannot pass
silently), runs the tests that cover the code and requires them to FAIL. It first requires the unmodified
copy to pass. Nothing in the repository is modified.

    seeded_defects.py rust|go|all

The first defects are about the grammar-1 spelling of `inherit`, the version binding, the pin verification and
the CertPattern reading (cic-primitives D-018, ADR 0004): the places where a wrong implementation looks
right on the happy path. The last ones are about the parser parity of the two languages (error priority,
positions, the pre-parse refusals): the shared regression corpus must turn red on each.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (language, name, file, [(old, new), ...])
DEFECTS = [
    ("rust", "integer 0 is read as no-inherit (0 coerced to false)", "src/access.rs",
     [("(Value::Int(0), _) => Ok(Mode::Reset),", "(Value::Int(0), _) => Ok(Mode::NoInherit),")]),
    ("rust", "a missing version silently falls back to grammar 1", "src/access.rs",
     [("None | Some(Value::Null) => Err(binding(\"GRAMMAR_VERSION_MISSING\")),", "None | Some(Value::Null) => Ok(1),"),
      ("if is_missing(context) || is_missing(pinned) {", "if false {")]),
    ("rust", "the pinned schema is used without verifying its digest", "src/access.rs",
     [("if sha256_hex(schema) != expected_sha256 {", "if false {")]),
    ("rust", "a string without '=' is a CertPattern", "src/access.rs",
     [("        None => false,\n    })", "        None => true,\n    })")]),
    ("rust", "the empty list is read as the symbol inherit", "src/access.rs",
     [("            Ok(Rule::List(patterns))", "            if patterns.is_empty() { Ok(Rule::Symbol) } else { Ok(Rule::List(patterns)) }")]),
    ("rust", "grammar 2 accepts the legacy boolean spelling", "src/access.rs",
     [("None if legacy => Err(invalid(\"LEGACY_INHERIT_IN_GRAMMAR_2\", node)),", "None if legacy => Ok(Mode::Inherit),")]),
    ("rust", "a violation drops its source position", "src/access.rs",
     [("location: node.pos.map_or(Location::Unavailable, Location::At),", "location: Location::Unavailable,")]),
    ("go", "integer 0 is read as no-inherit (0 coerced to false)", "access.go",
     [("case n.Kind == KindInt && n.Int == 0:\n\t\t\treturn ModeReset, nil", "case n.Kind == KindInt && n.Int == 0:\n\t\t\treturn ModeNoInherit, nil")]),
    ("go", "a missing version silently falls back to grammar 1", "access.go",
     [("func isMissing(v *Node) bool { return v == nil || v.Kind == KindNull }", "func isMissing(v *Node) bool { return false }"),
      ("func checked(v *Node) (int64, error) {", "func checked(v *Node) (int64, error) {\n\tif v == nil || v.Kind == KindNull {\n\t\treturn 1, nil\n\t}")]),
    ("go", "the pinned schema is used without verifying its digest", "access.go",
     [("if SHA256Hex(schema) != expectedSHA256 {", "if false {")]),
    ("go", "a string without '=' is a CertPattern", "access.go",
     [("if !ok || !isAttr(attr) || strings.Contains(value, \"=\") {", "if !ok {\n\t\t\tcontinue\n\t\t}\n\t\tif !isAttr(attr) || strings.Contains(value, \"=\") {")]),
    ("go", "the empty list is read as the symbol inherit", "access.go",
     [("return Rule{Patterns: patterns}, nil", "if len(patterns) == 0 {\n\t\t\treturn Rule{Symbol: true}, nil\n\t\t}\n\t\treturn Rule{Patterns: patterns}, nil")]),
    ("go", "grammar 2 accepts the legacy boolean spelling", "access.go",
     [("\treturn ModeInvalid, invalid(\"LEGACY_INHERIT_IN_GRAMMAR_2\", n)", "\treturn ModeInherit, nil")]),
    ("go", "a violation drops its source position", "access.go",
     [("Location: loc(n.Pos)}", "Location: Location{}}")]),
    ("go", "an unbound or unknown Grammar is read as grammar 2", "access.go",
     [("\tif !isSupported(g.version) {\n\t\treturn ModeInvalid, invalid(", "\tif false {\n\t\treturn ModeInvalid, invalid(")]),
    ("go", "an unbound or unknown Grammar is written as grammar 2", "access.go",
     [("\tif !isSupported(g.version) {\n\t\treturn nil, binding(", "\tif false {\n\t\treturn nil, binding(")]),
    ("go", "an invalid Mode is written as reset", "access.go",
     [("\tif m < ModeInherit || m > ModeReset {\n\t\treturn nil, unsupported(", "\tif false {\n\t\treturn nil, unsupported(")]),
    ("go", "a caller can change the supported versions", "access.go",
     [("func SupportedGrammars() []int64 { return slices.Clone(supportedGrammars) }", "func SupportedGrammars() []int64 { return supportedGrammars }")]),
    # Parser parity (docs/P1-SUPPORTED-SUBSET.md, "Parser parity"): the shared regression corpus must turn red.
    ("rust", "the last non-syntax error is reported, not the first", "src/parse.rs",
     [("        self.deferred.get_or_insert(e);", "        *self.deferred = Some(e);")]),
    ("rust", "a block sequence is located at its first item", "src/parse.rs",
     [("                let at = self.table.dash(at);", "                let at = at;")]),
    ("rust", "a block scalar is located at its first content line", "src/parse.rs",
     [("let pos = if block { self.table.indicator(at) } else { at };", "let pos = at;")]),
    ("rust", "a word starting with ! is not refused", "src/parse.rs",
     [("'!' => return Err(Error::Unsupported(\"tag\".into())),", "'!' => {}")]),
    ("rust", "a tab is accepted", "src/parse.rs",
     [("    if body.contains('\\t') {", "    if false {")]),
    ("rust", "a block scalar without content is accepted", "src/parse.rs",
     [("if value.chars().all(|c| c == '\\n') {", "if false {")]),
    ("rust", "an empty implicit key is read as ~", "src/parse.rs",
     [("                        if !has_text {", "                        if false {")]),
    ("go", "a tab is accepted", "parse.go",
     [("\tif strings.Contains(body, \"\\t\") {", "\tif false {")]),
    ("go", "a word starting with ! is not refused", "parse.go",
     [("\t\t\tcase ch == '!':", "\t\t\tcase false:")]),
    ("go", "a block scalar without content is accepted", "parse.go",
     [("\t\t\tif strings.Trim(n.Value, \"\\n\") == \"\" {", "\t\t\tif false {")]),
    ("rust", "the content of a block scalar is scanned for markers", "src/parse.rs",
     [("                if blank || indent > parent {", "                if false {")]),
    ("rust", "an indented marker inside a quoted scalar is a document marker", "src/parse.rs",
     [("            indent == 0\n        } else {", "            true\n        } else {")]),
    ("go", "the content of a block scalar is scanned for markers", "parse.go",
     [("\t\t\t\tif blank || indent > block {", "\t\t\t\tif false {")]),
    ("go", "an indented marker inside a quoted scalar is a document marker", "parse.go",
     [("\t\t\trefused = indent == 0", "\t\t\trefused = true")]),
    ("go", "a syntax error in a second document is reported as a second document", "parse.go",
     [("\tcase !errors.Is(err, io.EOF):\n\t\treturn nil, &Error{Kind: ErrSyntax, Detail: err.Error()}", "\tcase !errors.Is(err, io.EOF):\n\t\treturn nil, unsupported(\"more than one document\")")]),
]


def run(cmd, cwd, env):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def prepare(lang, tmp):
    """A copy of the module, laid out so that its tests find the corpus and testdata."""
    os.symlink(os.path.join(ROOT, "corpus"), os.path.join(tmp, "corpus"))
    os.symlink(os.path.join(ROOT, "testdata"), os.path.join(tmp, "testdata"))
    dest = os.path.join(tmp, lang)
    shutil.copytree(os.path.join(ROOT, lang), dest, ignore=shutil.ignore_patterns("target", "fuzz", "rapid"))
    return dest


def command(lang):
    if lang == "rust":
        return ["cargo", "+stable", "test", "--locked", "--lib", "--test", "access", "--test", "corpus", "--test", "properties",
                "--test", "parity_dump", "--test", "behaviour"]
    return ["go", "test", "-count=1", "./..."]


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    langs = ("rust", "go") if which == "all" else (which,)
    bad = 0
    env = dict(os.environ, CIC_FORMS_ROOT=ROOT)
    if "CIC_PRIMITIVES_ROOT" not in env:
        print("CIC_PRIMITIVES_ROOT is not set: the corpus runners need the pinned checkout")
        return 2
    for lang in langs:
        with tempfile.TemporaryDirectory() as tmp:
            env["CARGO_TARGET_DIR"] = os.path.join(tmp, "target")
            base = prepare(lang, tmp)
            first = run(command(lang), base, env)
            print("%-4s baseline (unmodified)           : %s" % (lang, "pass" if first.returncode == 0 else "FAIL"))
            if first.returncode != 0:
                print(first.stdout[-2000:], first.stderr[-2000:])
                bad += 1
                continue
            for _, name, rel, ops in [d for d in DEFECTS if d[0] == lang]:
                path = os.path.join(base, rel)
                original = open(path, encoding="utf-8").read()
                text = original
                for old, new in ops:
                    if text.count(old) != 1:
                        print("%-4s STALE %s: %r occurs %d times" % (lang, name, old, text.count(old)))
                        bad += 1
                        break
                    text = text.replace(old, new)
                else:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    r = run(command(lang), base, env)
                    caught = r.returncode != 0
                    print("%-4s %-46s: %s" % (lang, name, "CAUGHT" if caught else "NOT CAUGHT"))
                    bad += 0 if caught else 1
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(original)
    print("SEEDED DEFECTS: %s" % ("every defect is caught" if bad == 0 else "FAILED (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
