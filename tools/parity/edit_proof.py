#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The edit differential on real defects: a defect planted in ONE language must turn the Rust/Go comparison red (P3b step E4).

    edit_proof.py INPUTS RUST_OUT GO_OUT [--lang rust|go] [--only SUBSTRING]

The defects are the ones of tools/seeded_defects.py that change what `edit_scalar` answers and that the generated inputs can reach (the shape of the
differential has no nullable position, so a defect of the spelling of null is not among them). For each: a copy of the module of that language gets
the defect, its dump (`parity_edit_dump`) is made, and tools/parity/compare_edit.py must exit 1 with the mutated dump on its side and the unmutated
dump of the other language on the other side. The unmutated dumps given on the command line must pass first. A defect the comparison does not see is a
failure of this script, and the list says which. Nothing in the repository is modified.
"""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import seeded_defects as sd  # noqa: E402

OBSERVABLE = [
    "a no-op rewrites the token",
    "the single quote is not doubled in a single-quoted scalar",
    "the backslash and the quote are not escaped in a double-quoted scalar",
    "the kind of the new value is not checked",
    "a control character is written",
    "a string that needs quotes is written plain",
    "the result is not validated",
    "the document is not validated before the edit",
    "null is accepted by a position that is not nullable",
    "the booleans are written capitalised",
]


def compare(inputs, rust, go):
    return subprocess.run([sys.executable, os.path.join(HERE, "compare_edit.py"), inputs, rust, go, "--show", "1"], capture_output=True, text=True)


def dump(lang, module, inputs, out, env):
    e = dict(env, PARITY_EDIT_IN=inputs, PARITY_EDIT_OUT=out)
    cmd = (["cargo", "+stable", "test", "--locked", "--test", "parity_edit_dump"] if lang == "rust"
           else ["go", "test", "-count=1", "-run", "^TestDumpEditOutcomes$", "."])
    return subprocess.run(cmd, cwd=module, env=e, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs")
    ap.add_argument("rust")
    ap.add_argument("go")
    ap.add_argument("--lang", choices=("rust", "go"))
    ap.add_argument("--only")
    a = ap.parse_args()
    inputs, rust, go = (os.path.abspath(p) for p in (a.inputs, a.rust, a.go))
    base = compare(inputs, rust, go)
    if base.returncode:
        print("the unmutated dumps do not pass:\n" + base.stdout[-1500:])
        return 1
    env = dict(os.environ, CIC_FORMS_ROOT=sd.ROOT)
    for name in ("CIC_PRIMITIVES_ROOT", "CIC_REGISTRY_ROOT"):
        if name not in env:
            print("%s is not set" % name)
            return 2
    langs = (a.lang,) if a.lang else ("rust", "go")
    defects = [d for d in sd.DEFECTS if d[1] in OBSERVABLE and d[2].endswith("editscalar.go" if d[0] == "go" else "editscalar.rs")
               and d[0] in langs and (not a.only or a.only in d[1])]
    missed = checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        env["CARGO_TARGET_DIR"] = os.path.join(tmp, "target")        # one build directory for all the defects of the run
        for lang in langs:
            work = os.path.join(tmp, lang + "-work")
            os.makedirs(work)
            module = sd.prepare(lang, work)
            for _, name, rel, ops in [d for d in defects if d[0] == lang]:
                path = os.path.join(module, rel)
                original = open(path, encoding="utf-8").read()
                text = original
                for old, new in ops:
                    if text.count(old) != 1:
                        print("%-4s STALE %s: %r occurs %d times" % (lang, name, old, text.count(old)))
                        return 1
                    text = text.replace(old, new)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                mutated = os.path.join(tmp, "mutated.jsonl")
                try:
                    os.remove(mutated)
                except FileNotFoundError:
                    pass
                r = dump(lang, module, inputs, mutated, env)
                if not os.path.exists(mutated):
                    # the defect stopped the dump from running (a build error or a panic): the comparison has nothing to compare, which is a finding too
                    print("%-4s %-70s: NO DUMP (%s)" % (lang, name, (r.stdout + r.stderr).strip().splitlines()[-1][:80] if (r.stdout + r.stderr).strip() else "no output"))
                    missed += 1
                else:
                    res = compare(inputs, mutated, go) if lang == "rust" else compare(inputs, rust, mutated)
                    seen = res.returncode != 0
                    checked += 1
                    print("%-4s %-70s: %s" % (lang, name, "RED" if seen else "NOT SEEN"))
                    missed += not seen
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(original)
    print("EDIT PROOF: %s" % ("every planted defect turns the differential red (%d)" % checked if not missed else "%d NOT SEEN" % missed))
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
