"""Which constructs is THIS build of the extractor qualified for? Measured on probes, not declared.

    from qualify import qualify
    verified = qualify(BIN, args)          # a frozenset of construct names, for the Assurance of that extractor identity

The extractor recognises implicit iota and the other constructs; it cannot say that it models them. A reviewer outside it can, on
evidence. This module is that evidence for the const group rule: three pairs of Go sources (lab/testdata/iota, iota-insert,
iota-inherit) in which the value of a constant moves while its own text does not, and the test of it goes from pass to fail. A build
is qualified for `go.const.implicit_repeat`, `go.const.iota` and `go.const.group` only if, for each pair, the Merkle key of the test
differs between the two states (so the rule would rerun it); it is a regression probe, not a proof that every case is modelled.

The base constructs (BASE) are the ones whose modelling the earlier measurements checked against coverage (RESULTS.md section 3);
they are the claim of the reviewer and unchanged here. The result belongs to one binary and one set of flags: a different build is
a different extractor identity and has to be qualified again.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402

TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata")
BASE = frozenset(["go.func", "go.method", "go.type", "go.var", "go.const.explicit"])
CONST_GROUP = frozenset(["go.const.implicit_repeat", "go.const.iota", "go.const.group"])
# (name, directory of the pair, package, the test)
PROBES = (
    ("an iota expression edited", "iota", "iotarepro", "TestB"),
    ("a constant inserted into the group", "iota-insert", "insrepro", "TestB"),
    ("an expression the implicit repeat inherits depends on a changed constant", "iota-inherit", "inhrepro", "TestB"),
)


def _key(binary, args, directory, state, pkg, test):
    d = os.path.join(TD, directory, state)
    g = json.loads(subprocess.run([os.path.abspath(binary), *args, "."], cwd=d, check=True, capture_output=True, text=True).stdout)
    return merkle.keys(g)[0].get("%s.%s" % (pkg, test))


def _passes(directory, state):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(TD, directory, state), capture_output=True).returncode == 0


def probes(binary, args=()):
    """[(name, ok, detail)]: ok when the test went pass -> fail between the states AND the key moved."""
    out = []
    for name, directory, pkg, test in PROBES:
        ka, kb = (_key(binary, args, directory, s, pkg, test) for s in "ab")
        semantic = _passes(directory, "a") and not _passes(directory, "b")
        out.append((name, bool(semantic and ka and kb and ka != kb),
                    "the value moves (pass -> fail): %s; key moves: %s" % (semantic, ka != kb)))
    return out


def qualify(binary, args=()):
    """The constructs this build is qualified for; NOTHING if any probe fails.

    A build that fails a probe has a known gap in what it models, and a gap can hide the construct it concerns (a missing edge keeps
    the construct out of the closure, so a check of the reported constructs alone would pass; see qualify_rust.py). Such a build is
    qualified for no construct, not even the base ones."""
    return BASE | CONST_GROUP if all(ok for _, ok, _ in probes(binary, args)) else frozenset()


if __name__ == "__main__":
    for n, ok, detail in probes(sys.argv[1], sys.argv[2:]):
        print(("QUALIFIED " if ok else "NOT       ") + n + "  (" + detail + ")")
    print(sorted(qualify(sys.argv[1], sys.argv[2:])))
