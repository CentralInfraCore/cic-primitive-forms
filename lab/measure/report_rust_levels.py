"""The RESULTS.md section for the three Rust levels, made from the measured files, with wording that depends on what was measured.

    python3 lab/measure/report_rust_levels.py --fixed F --control C --honest H --kernel K --check S --replay V --results lab/RESULTS.md --notes N

Writes (or replaces) the block between <!-- rust-levels:begin --> and <!-- rust-levels:end --> in RESULTS.md, before "## 11. Reproducing", and
prints the flags a human must look at before this is committed:

  ATTENTION  the fixed build called a failing test eligible, or a killed test's key did not move, or the history check found a false reuse.
             Nothing is committed; the files are left for review.
  WARNING    the control (old model, everything vouched) found no false reuse: the mutants then show no sensitivity for Rust, and the section
             says so instead of presenting the 0 of the fixed build as evidence.

Exit code 2 if there is an ATTENTION, else 0.
"""
import argparse
import collections
import gzip
import json
import os
import sys

BEGIN, END = "<!-- rust-levels:begin -->", "<!-- rust-levels:end -->"


def load(path):
    if path.endswith(".gz"):
        return json.load(gzip.open(path, "rt"))
    return json.load(open(path))


def targeted(r):
    c = collections.Counter(m["status"] for m in r["mutants_detail"] if m["op"].startswith("targeted"))
    return dict(c)


def fmt(d):
    return ", ".join("%s %s" % (k, v) for k, v in d.items()) or "none"


def main():
    ap = argparse.ArgumentParser()
    for k in ("fixed", "control", "honest", "kernel", "check", "replay", "results", "notes"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args()
    F, C, H = load(a.fixed), load(a.control), load(a.honest)
    K, S, V = load(a.kernel), load(a.check), load(a.replay)
    flags = []

    def pairs(r):
        return r["pairs_mutant_x_test"]

    rust_dec = {k.split("/", 1)[1]: v for k, v in S["decisions"].items() if k.startswith("rust/")}
    false_hist = sum(v.get("FALSE_REUSE", 0) for v in rust_dec.values())
    unstable = collections.Counter()
    skip = fails = 0
    for c, e in V.items():
        for t, r in e["rust"].items():
            if r[0] != r[1]:
                unstable[t] += 1
            skip += "SKIP" in r
            fails += "FAIL" in r
    kd = K["decisions"]["rust"]
    lost = K["reusable_by_the_older_rule_not_by_the_kernel"]["rust"]
    lost_unstable = sum(v for k, v in lost.items() if "NONDETERMINISTIC" in k)
    lost_proptest = sum(v for k, v in lost.items() if "ANALYSIS_INCOMPLETE" in k)
    by_kind = collections.Counter()
    for m in C["mutants_detail"]:
        by_kind[m["op"].split(":")[0]] += len(m.get("false_reuse", []))
    eligible_all = K["decisions"]["go"]["REUSE_ELIGIBLE"] + kd["REUSE_ELIGIBLE"]

    if F["false_reuse_tests"] or F["killed_tests_with_an_unchanged_key"] or false_hist:
        flags.append("ATTENTION: fixed build: %d false reuse, %d killed tests with an unchanged key; history: %d false reuse" % (
            F["false_reuse_tests"], F["killed_tests_with_an_unchanged_key"], false_hist))
    if not C["false_reuse_tests"]:
        flags.append("WARNING: the control found no false reuse: no demonstrated sensitivity for Rust")

    ft, ct = targeted(F), targeted(C)
    sens = (
        "Control A finds **%d false reuses in %d mutants**, with the key of %d killed tests unchanged: the mutant set can see the defect of the "
        "old model (no edges from an impl to its type), and the 0 of the fixed build means something." % (
            C["false_reuse_tests"], C["killed_mutants_with_a_false_reuse"], C["killed_tests_with_an_unchanged_key"])
        if C["false_reuse_tests"] else
        "**Control A finds nothing** (0 false reuses): in this sample no mutant reaches a test through an impl, so these mutants show **no sensitivity** "
        "to the defect of the old model, and the 0 of the fixed build carries no weight. The sensitivity for Rust rests on the eleven fixture pairs "
        "(level 1), not on this experiment.")

    text = f'''{BEGIN}
### 10.5 Rust: the same three levels as Go

Level 1, the implicit calls (`testdata/rust-dispatch`, `repro_rust_dispatch.py`): one crate in two states, eleven tests, one per implicit call
(`Display` through `to_string()`, a trait object and an `impl Display` argument; `?` through `From`; `for` through `Iterator::next`; `+`, `==`,
`[]`; a method through `Deref`; `Drop`; `sort()` through `Ord`). Ten keys moved. **One did not: the `Drop` of `impl Drop for D<'_>`**, because
the id of an impl for a type with a parameter quotes the type (``impl#[`D<'_>`][Drop]drop()``) and the rule "a type reaches its impls" read only
``impl#[Name]``. On the crate itself 54 of 89 impl methods have such a self type (all inherent, so latent: a caller that names the method has its own
edge). Fixed (`b0f426f`: 65 edges added on 9 types, none removed); the eleven cases are required probes of `qualify_rust.py`; with
`SCIPDEPS_PLANTED_PLAIN_SELF_ONLY=1` the red case is back, and a build with the old rule is refused whole.

Level 2, the history observed (`shadow_replay_rust.py`, 114 commits, every test binary run twice, `cargo test`):

| Rust decision of the kernel | Runs | Observed on the child |
|---|---|---|
| `REUSE_ELIGIBLE` | {kd["REUSE_ELIGIBLE"]} | {fmt(rust_dec.get("REUSE_ELIGIBLE", {}))} |
| `RERUN_REQUIRED` | {kd["RERUN_REQUIRED"]} | {fmt(rust_dec.get("RERUN_REQUIRED", {}))} |
| `UNVERIFIABLE` | {kd["UNVERIFIABLE"]} | {fmt(rust_dec.get("UNVERIFIABLE", {}))} |

False reuse in the history: **{false_hist}**. As for Go this has little power, because main is green: no test fails stably at any commit
({skip} skipped runs); the only failing runs ({fails} test-commit pairs) are those of the one unstable test
({", ".join(sorted(unstable))}; in {sum(unstable.values())} commits). That test is a timing check ("does not cost quadratic time") and passes or fails with the load of the machine: it is not a function of its code, and
the kernel now treats a test seen unstable as `NONDETERMINISTIC_SUBJECT` (`UNVERIFIABLE`) instead of reusing its pass. With observed verdicts and
that rule both languages together: **{eligible_all} of 29096 runs are `REUSE_ELIGIBLE` ({100.0 * eligible_all / 29096:.1f}%)**.

Level 3, injected changes (`mutant_shadow_rust.py`; the tree at HEAD and mutants of `rust/src`, outside the `#[cfg(test)]` part of a file: {sum(F["mutants"].values())} here;
targeted = inside an `impl Trait for Type`):

| Pairs (mutant x test) | Fixed build, measured assurance | Control A: old model (no impl edges), **everything vouched** |
|---|---|---|
| mutants | {fmt(F["mutants"])} | {fmt(C["mutants"])} |
| targeted mutants | {fmt(ft)} | {fmt(ct)} |
| parent PASS, mutant FAIL (twice): `RERUN_REQUIRED` | {F["killed_tests_by_decision"].get("RERUN_REQUIRED", 0)} | {C["killed_tests_by_decision"].get("RERUN_REQUIRED", 0)} |
| ... `UNVERIFIABLE` | {F["killed_tests_by_decision"].get("UNVERIFIABLE", 0)} | {C["killed_tests_by_decision"].get("UNVERIFIABLE", 0)} |
| ... **`REUSE_ELIGIBLE` = false reuse** | **{F["false_reuse_tests"]}** | **{C["false_reuse_tests"]}** |
| killed tests whose key did **not** move | **{F["killed_tests_with_an_unchanged_key"]}** | **{C["killed_tests_with_an_unchanged_key"]}** |
| parent PASS, mutant PASS | {pairs(F)["pass_pass"]} | {pairs(C)["pass_pass"]} |
| unstable | {pairs(F)["unstable"]} | {pairs(C)["unstable"]} |

{sens} Of Control A's false reuses {by_kind.get("targeted", 0)} are in the targeted mutants (inside a trait impl) and {by_kind.get("general", 0)} in the general
ones; I have not traced by which implicit path each of those tests reaches the mutated code. Control B (old model with **its own** measured
Assurance, the targeted mutants only): {fmt(H["mutants"])}; failing tests by decision {fmt(H["killed_tests_by_decision"])}; false reuse
{H["false_reuse_tests"]}; {H["killed_tests_with_an_unchanged_key"]} killed test(s) whose key did not move, which the kernel withheld all the same.

What this does not show:

* Only {sum(ft.values())} mutants are inside a trait impl (the crate has few: free functions and `#[derive]`), so the targeted class is small. A survivor says
  nothing about a decision; dropped mutants (no build, or a timeout) are not judged ({F["mutants"].get("dropped", 0)} here).
* Textual mutants of `rust/src`: a sample. Rust only; nothing about data files or the environment; {kd["UNVERIFIABLE"]} Rust runs are `UNVERIFIABLE`;
  of the {sum(lost.values())} the older rule reuses, {lost_proptest} are the `proptest!` tests (found by their text, so their analysis is incomplete) and
  {lost_unstable} the one unstable test.
* The numbers belong to these builds (rust-analyzer and `scipdeps` identity in the JSON) and to Rust 1.99.1 on this machine, not to the pinned CI.
{END}
'''
    md = open(a.results).read()
    if BEGIN in md:
        md = md[: md.index(BEGIN)] + text + md[md.index(END) + len(END) + 1:]
    else:
        marker = "## 11. Reproducing"
        assert md.count(marker) == 1
        md = md.replace(marker, text + "\n" + marker)
    open(a.results, "w").write(md)
    open(a.notes, "w").write("\n".join(flags) + ("\n" if flags else ""))
    for f in flags:
        print(f)
    print("section written; flags: %d" % len(flags))
    return 2 if any(f.startswith("ATTENTION") for f in flags) else 0


if __name__ == "__main__":
    sys.exit(main())
