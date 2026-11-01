"""Which Rust constructs is THIS build of the extractor qualified for? Measured on probes, with the claims kept apart.

    from qualify_rust import qualify
    verified = qualify(SCIPDEPS, TOOLCHAIN)        # a frozenset of construct names, for the Assurance of that extractor identity

Two kinds of ground, and the module says which is which:

* PROBES (lab/testdata/rust-probes): one crate in two states in which the dependency a construct hides moves a value and its test
  goes from pass to fail. A construct is qualified only if the Merkle key of its test differs between the states. A planted build
  (SCIPDEPS_PLANTED_NO_IMPL=1, the extractor without the type -> impl edges) must fail the trait impl and generic probes, which is
  how the probes are known to be able to fail.
      rs.trait_impl        a Display impl reached through format!
      rs.generic.type      the same impl reached through a type parameter
      rs.generic.lifetime  a helper called from a function with a lifetime parameter
      rs.macro.external    a macro of the standard library whose arguments call a function that changes
* CLAIMS (CLAIMED): the item kinds, which the coverage check of RESULTS.md section 3 holds against what 244 tests execute (0 misses),
  and `rs.env.CARGO_MANIFEST_DIR`, which is the directory of the crate and not a dependency of its content (what a test reads there is
  a data edge, recorded by the tracer). These are the reviewer's, not measured here.

The eleven implicit calls of lab/testdata/rust-dispatch are probes of the same kind and add no construct name.
A build that fails any probe is qualified for nothing (see qualify()).

NOT qualified, so UNVERIFIABLE whenever the closure holds them: rs.macro.local (the call site has no edge to the body of a macro of
the crate: lab/testdata/rust-macro is the red case), rs.macro.generated (found by text), rs.macro.include, every other rs.env.*,
rs.cfg.*. A different build is a different identity and has to be qualified again.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402
import rust_graph as rg  # noqa: E402
import repro_rust_dispatch as rrd  # noqa: E402

TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "testdata")
CLAIMED = frozenset(["rs.fn", "rs.method", "rs.type", "rs.const", "rs.static", "rs.env.CARGO_MANIFEST_DIR"])
# (construct, the test that must key apart)
PROBES = (
    ("rs.trait_impl", "tests/probe::t_trait_impl()."),
    ("rs.generic.type", "tests/probe::t_generic()."),
    ("rs.generic.lifetime", "tests/probe::t_lifetime()."),
    ("rs.macro.external", "tests/probe::t_macro()."),
)


def probes(scipdeps, toolchain, env=None):
    """[(construct, ok, detail)]: ok when the test goes pass -> fail between the states AND its key moves."""
    a, b = (os.path.join(TD, "rust-probes", v) for v in "ab")
    ka = merkle.keys(rg.graph(a, scipdeps, toolchain, env))[0]
    kb = merkle.keys(rg.graph(b, scipdeps, toolchain, env))[0]
    pa, pb = rg.passes(a, toolchain), rg.passes(b, toolchain)
    out = []
    for construct, test in PROBES:
        moves = ka.get(test) is not None and kb.get(test) is not None and ka[test] != kb[test]
        out.append((construct, bool(moves and pa and not pb), "the tests pass on a and fail on b: %s; key of %s moves: %s"
                    % (pa and not pb, test, moves)))
    # the implicit calls (lab/testdata/rust-dispatch): REQUIRED, like the four above: each is a rule of the build (a type reaches the impls
    # of its trait, `?` reaches From, `for` reaches next, `+` reaches Add, a scope end reaches Drop ...). They carry no construct name of
    # their own ("implicit call: ..."), so they add nothing to the qualified set; they decide whether the build is qualified at all.
    da, db = (os.path.join(TD, "rust-dispatch", v) for v in "ab")
    kda = merkle.keys(rg.graph(da, scipdeps, toolchain, env))[0]
    kdb = merkle.keys(rg.graph(db, scipdeps, toolchain, env))[0]
    pda, pdb = rg.passes(da, toolchain), rg.passes(db, toolchain)
    for t, what in rrd.TESTS.items():
        tid = "tests/dispatch::%s()." % t
        moves = kda.get(tid) is not None and kdb.get(tid) is not None and kda[tid] != kdb[tid]
        out.append(("implicit call: " + what, bool(moves and pda and not pdb),
                    "the tests pass on a and fail on b: %s; key of %s moves: %s" % (pda and not pdb, t, moves)))
    return out


def qualify(scipdeps, toolchain, env=None):
    """The constructs this build is qualified for; NOTHING if any probe fails.

    A build that fails a probe has a known gap in what it models, and the gap can hide the very construct it concerns: without the
    type -> impl edges the closure of a test does not reach the impl, so the impl's `rs.trait_impl` is never reported and a check of
    the reported constructs alone would pass. So a build that fails any probe is qualified for no construct at all."""
    ps = probes(scipdeps, toolchain, env)
    if not all(ok for _, ok, _ in ps):
        return frozenset()
    return CLAIMED | frozenset(c for c, _, _ in ps if c.startswith("rs."))


if __name__ == "__main__":
    tc = sys.argv[2]
    env = {"SCIPDEPS_PLANTED_NO_IMPL": "1"} if len(sys.argv) > 3 and sys.argv[3] == "--planted-no-impl" else None
    for c, ok, detail in probes(sys.argv[1], tc, env):
        print(("QUALIFIED " if ok else "NOT       ") + c + "  (" + detail + ")")
    print(sorted(qualify(sys.argv[1], tc, env)))
