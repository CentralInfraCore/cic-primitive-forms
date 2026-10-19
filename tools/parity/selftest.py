#!/usr/bin/env python3
"""A gate that cannot fail proves nothing: this shows that compare.py --allow turns red when it should.

    selftest.py INPUTS RUST_OUT GO_OUT ALLOW

Takes the real outputs of a parity run (which must pass as they are) and breaks them, one way at a time, in a
temporary copy. Each break must make compare.py exit 1 with the right word in its output:

  CHANGED   a known divergence whose Go outcome (or Rust outcome) is another one than the allowed one
  NEW       a divergence that is not in the allow list (an allow line removed; an agreeing input made to differ)
And two that must stay green: an allowed input that stopped diverging (a fix), and the unbroken run.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def refused_pair(r, g):
    """compare.py counts two refusals as agreement, whatever their codes."""
    return "err" in r and "err" in g and "CONTRACT" not in (r["err"], g["err"])


def alt(o):
    """Another answer of the same kind: a different tree for an accepted text, a contract error for a refusal."""
    if "ok" in o:
        return {"ok": {"n": {"t": "str", "v": "tampered"}, "p": [1, 1, 0]}}
    return {"err": "CONTRACT", "viol": []}


def run(inputs, rust, go, allow):
    p = subprocess.run([sys.executable, os.path.join(HERE, "compare.py"), inputs, rust, go, "--show", "2", "--allow", allow],
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def main():
    inputs, rust, go, allow = sys.argv[1:5]
    rs = [json.loads(l) for l in open(rust)]
    gs = [json.loads(l) for l in open(go)]
    ins = [l.strip() for l in open(inputs)]
    entries = [l.split()[0] for l in open(allow) if l.strip() and not l.startswith("#")]
    allowed = set(entries)
    count = {}
    for h in ins:
        count[h] = count.get(h, 0) + 1
    # Inputs that occur once, so that changing one answer changes the verdict on that input alone.
    div = [i for i, h in enumerate(ins) if h in allowed and count[h] == 1 and not refused_pair(rs[i], gs[i])]
    agree = [i for i, h in enumerate(ins) if count[h] == 1 and rs[i] == gs[i] and "ok" in rs[i]]
    assert div and agree, "the run has no known divergence or no agreeing accepted input"
    bad = 0

    def case(name, want_rc, word, rust_o=None, go_o=None, allow_lines=None):
        nonlocal bad
        r2, g2 = list(rs), list(gs)
        for i, o in (rust_o or {}).items():
            r2[i] = o
        for i, o in (go_o or {}).items():
            g2[i] = o
        with tempfile.TemporaryDirectory() as d:
            rp, gp, ap = (os.path.join(d, n) for n in ("rust", "go", "allow"))
            open(rp, "w").write("".join(json.dumps(o) + "\n" for o in r2))
            open(gp, "w").write("".join(json.dumps(o) + "\n" for o in g2))
            lines = open(allow).read().splitlines() if allow_lines is None else allow_lines
            open(ap, "w").write("\n".join(lines) + "\n")
            rc, out = run(inputs, rp, gp, ap)
        ok = rc == want_rc and (word is None or word in out)
        print("%-62s: %s" % (name, "ok" if ok else "FAILED (exit %d, wanted %d, word %r)" % (rc, want_rc, word)))
        bad += 0 if ok else 1

    i = div[0]
    case("the run as it is", 0, "changed: 0; new: 0")
    case("a known divergence answers differently on the Go side", 1, "CHANGED", go_o={i: alt(gs[i])})
    case("a known divergence answers differently on the Rust side", 1, "CHANGED", rust_o={i: alt(rs[i])})
    case("a known divergence gets another kind of answer on both sides", 1, "CHANGED",
         go_o={i: alt(gs[i])}, rust_o={i: alt(rs[i])})
    case("a known divergence line is removed from the allow list", 1, "NEW",
         allow_lines=[l for l in open(allow).read().splitlines() if not l.startswith(ins[i])])
    j = agree[0]
    case("an input that agreed now diverges", 1, "NEW", go_o={j: alt(gs[j])})
    case("an allowed input stops diverging (a fix)", 0, "no longer divergent: 1", go_o={i: rs[i]})
    print("SELFTEST: %s" % ("every break turns the gate red" if bad == 0 else "FAILED (%d)" % bad))
    return 0 if bad == 0 else 1


sys.exit(main())
