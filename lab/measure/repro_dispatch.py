"""Dynamic dispatch the graph does not follow: nine red cases and two that hold.

    python3 lab/measure/repro_dispatch.py TYPEDDEPS_BINARY --expect vulnerable|fixed [--extractor-arg=-planted-...] [--record OUT.json]

In each pair of Go sources (lab/testdata/go-generic-*, go-conv-*) a method that `fmt` calls by dynamic dispatch changes, the test goes
from pass to fail, and the question is whether the Merkle key of the test moves. A case is
  RED    pass -> fail and the key is the SAME: the rule would reuse a result that is wrong now
  GREEN  pass -> fail and the key MOVES
  (anything else means the fixture itself is wrong, and the script stops.)

--expect vulnerable: the RED cases must be RED and the GREEN cases GREEN (the known state of the extractor before the fix; exit 0).
--expect fixed:      every case must be GREEN (exit 0 only then).
The mechanism: a value that becomes an interface value (an argument of fmt.Sprint, a type argument that fmt receives) reaches the
methods of its type, of the types it embeds (promoted methods), of its elements and of its fields. The conversion rule of the
extractor looked only at the methods declared on a named type, and skipped type parameters.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402

TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata")
# (directory, package, expected before the fix)
CASES = (
    ("go-generic-impl", "genimpl", "GREEN"),        # a generic function calls a method of its constraint: the implementers rule
    ("go-generic-iface", "geniface", "GREEN"),      # a type argument that is an interface: the value is converted where it is assigned
    ("go-generic-fmt", "genfmt", "RED"),            # fmt.Sprint(x) of a value of a type parameter
    ("go-generic-ptr", "genptr", "RED"),            # the type argument is *S, String has a pointer receiver
    ("go-generic-promoted", "genprom", "RED"),      # String is promoted from an embedded type
    ("go-generic-pass", "genpass", "RED"),          # the type parameter is handed on to another generic function
    ("go-generic-type", "gentype", "RED"),          # a generic type Box[S] formats its field
    ("go-generic-slice", "genslice", "RED"),        # the type argument is []S
    ("go-conv-promoted", "convprom", "RED"),        # no generics: fmt.Sprint(S{}), String promoted from an embedded type
    ("go-conv-slice", "convslice", "RED"),          # no generics: fmt.Sprint([]S{...})
    ("go-conv-field", "convfield", "RED"),          # no generics: fmt.Sprint(S{...}) where a field has String
)


def key(binary, args, rel, pkg):
    d = os.path.join(TD, rel)
    g = json.loads(subprocess.run([os.path.abspath(binary), *args, "."], cwd=d, check=True, capture_output=True, text=True).stdout)
    return merkle.keys(g)[0].get(pkg + ".TestG")


def passes(rel):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(TD, rel), capture_output=True).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("--expect", choices=["vulnerable", "fixed"], required=True)
    ap.add_argument("--extractor-arg", action="append", default=[])
    ap.add_argument("--record")
    a = ap.parse_args()
    seen, bad = {}, []
    for rel, pkg, before in CASES:
        pa, pb = passes(rel + "/a"), passes(rel + "/b")
        if not (pa and not pb):
            sys.exit("fixture %s is wrong: passes on a %s, on b %s" % (rel, pa, pb))
        ka, kb = key(a.binary, a.extractor_arg, rel + "/a", pkg), key(a.binary, a.extractor_arg, rel + "/b", pkg)
        got = "GREEN" if ka and kb and ka != kb else "RED"
        seen[rel] = {"state": got, "key_a": ka, "key_b": kb, "before_the_fix": before}
        want = "GREEN" if a.expect == "fixed" else before
        print("%-5s %-22s expected %s%s" % (got, rel, want, "" if got == want else "   <-- not as expected"))
        if got != want:
            bad.append(rel)
    if a.record:
        json.dump({"expect": a.expect, "extractor_args": a.extractor_arg, "cases": seen}, open(a.record, "w"), indent=1, sort_keys=True)
    if bad:
        print("not as expected: %s" % ", ".join(bad))
        return 1
    print("as expected (%s)" % a.expect)
    return 0


if __name__ == "__main__":
    sys.exit(main())
