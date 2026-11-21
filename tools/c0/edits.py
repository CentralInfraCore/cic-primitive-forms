"""Make a copy of the go package with one realistic edit applied: edits.py <name> <go package dir> <destination dir>
(the C0 study, docs/C0-GO-MUTANT-ID-STUDY.md; the names are the experiments of its table)"""
import os, re, shutil, sys

name, SRC, dst = sys.argv[1], sys.argv[2], sys.argv[3]
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(SRC, dst)


def edit(fname, fn):
    """Apply ``fn`` to the text of ``fname`` in the destination copy and write it back.

    Asserts that the edit changed the file: an experiment whose anchor no longer matches must fail
    loudly instead of measuring an unedited package.
    """
    p = os.path.join(dst, fname)
    s = open(p).read()
    t = fn(s)
    assert t != s, "the edit changed nothing"
    open(p, "w").write(t)


def func_span(s, header):
    """Return the ``[start, end)`` offsets of the top-level Go function starting at ``header``.

    The end is just past the first ``\n}\n`` after it, i.e. the function's closing brace line.
    """
    i = s.index(header)
    j = s.index("\n}\n", i) + 3
    return i, j


NEWFN = "\nfunc c0Unrelated(a, b int) int {\n\tif a > b {\n\t\treturn a - b\n\t}\n\treturn a + b\n}\n"


def insert_after_package(s):
    """Insert two comment lines and blank lines after the ``package`` clause (the "comments" experiment)."""
    i = s.index("\n", s.index("\npackage ") + 1) if "\npackage " in s else s.index("\n", s.index("package "))
    return s[: i + 1] + "// c0 comment\n// c0 comment\n\n\n" + s[i + 1:]


def insert_before_first_func(s):
    """Insert the unrelated function ``NEWFN`` before the file's first function ("newfunc-top")."""
    i = s.index("\nfunc ")
    return s[:i] + NEWFN + s[i:]


def insert_in_middle(s):
    """Insert ``NEWFN`` just before ``startsScalar``, in the middle of the file ("newfunc-middle")."""
    i = s.index("func startsScalar(")
    return s[:i] + NEWFN.lstrip("\n") + "\n" + s[i:]


def append_end(s):
    """Append ``NEWFN`` at the end of the file ("newfunc-end" and "other-file")."""
    return s + NEWFN


def string_literal(s):
    """Change one error-message string literal ("string-literal")."""
    return s.replace('"non-scalar mapping key"', '"non-scalar mapping key (c0)"', 1)


def inside_add_statement(s):
    """Add an early-return statement inside ``startsScalar`` ("inside-add-statement")."""
    i, j = func_span(s, "func startsScalar(")
    body = s[i:j].replace("\tj := i\n", "\tj := i\n\tif i < 0 {\n\t\treturn false\n\t}\n", 1)
    return s[:i] + body + s[j:]


def inside_rename(s):
    """Rename the local variable ``j`` to ``jj`` throughout ``startsScalar`` ("inside-rename")."""
    i, j = func_span(s, "func startsScalar(")
    body = re.sub(r"\bj\b", "jj", s[i:j])
    return s[:i] + body + s[j:]


def inside_whitespace(s):
    """Re-indent the body of ``startsScalar`` from tabs to four spaces ("inside-whitespace")."""
    i, j = func_span(s, "func startsScalar(")
    body = s[i:j].replace("\n\t", "\n    ")
    return s[:i] + body + s[j:]


def callee_body(s):
    """Change a constant in a condition of access.go, the body of a callee ("callee-body")."""
    return s.replace("(c >= 0x1c && c <= 0x1f)", "(c >= 0x1d && c <= 0x1f)", 1)


E = {
    "comments": ("parse.go", insert_after_package),
    "newfunc-top": ("parse.go", insert_before_first_func),
    "newfunc-middle": ("parse.go", insert_in_middle),
    "newfunc-end": ("parse.go", append_end),
    "string-literal": ("parse.go", string_literal),
    "inside-add-statement": ("parse.go", inside_add_statement),
    "inside-rename": ("parse.go", inside_rename),
    "inside-whitespace": ("parse.go", inside_whitespace),
    "callee-body": ("access.go", callee_body),
    "other-file": ("node.go", append_end),
}
f, fn = E[name]
edit(f, fn)
print("made", dst, "edit:", name, "file:", f)
