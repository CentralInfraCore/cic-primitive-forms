# The full run of a pull request: cargo-gamma and go-mutants

The full mutation run took about an hour (51 minutes on #106) and grows with the code: the mutants of the whole tree rose by about 20% in four days of P3b and P4
(Rust 1059 to 1267, Go 2148 to 2667 by go-mutants' balanced count), and the phases up to P7 are still to come. The seeded-defects job alone (33 minutes, one serial
step, 40 allowed) was close to its time limit. This note says what changed, what it rests on, and what it does not claim.

## Who measures what

| Run | Rust | Go |
|---|---|---|
| Pull request, **diff** mode (the gate is unchanged) | cargo-mutants on the lines of the diff | go-mutesting on the lines of the diff |
| Pull request, diff mode or full mode, beside the gate | cargo-gamma on the lines of the diff, **every** family, a *shadow* (`diff-gamma`): gates nothing, survivors in the run summary. Promoting it to a gate is a separate decision, after it has run on enough pull requests to show its noise | none yet (go-mutants' `--changed` selected no mutant in the layout of this repository, see below) |
| Pull request, **full** mode (`scope` says `engine=fast`) | cargo-gamma 0.3.0, every family but `literal`, `stmt`, `loop`, 6 shards; **and** the classic diff jobs (cargo-mutants) on the changed lines, as in a diff | go-mutants (pinned commit), profile `all`, 3 shards; **and** the classic diff jobs (go-mutesting) on the changed lines |
| **Nightly** FULL, a run by hand, the FULL after a change of `p1.yml` (`engine=classic`) | cargo-mutants, whole tree (as before) | go-mutesting, whole tree (as before) |
| Nightly FULL, beside it | cargo-gamma with **every** family, 8 shards, a *shadow*: gates nothing, its failure is not the FULL's | none |

The choice is made in one place, the `scope` job: `engine=fast` only for a pull request whose mode is `full`. A pull request is `full` when it changes a file of the
gate itself (`tools/quality_gate.py`, `tools/seeded_defects.py`, `tools/fuzz_matrix.py`), an existing test line, or the shared data (`diff_gate.py scope` says why).
The `quality` job checks that exactly the jobs of the chosen engine ran and that the other engine's did not.

## The seeded defects are sharded

`seeded-defects` was one serial step: 33 minutes 44 seconds of the 40 the job is allowed in a full run, and every pull request that adds planted defects (46 in P4)
makes it longer. A full run now cuts the defects into 8 shards (`seeded_defects.py --shard K/N`: every N-th defect of each language from K, so the shards cover every
defect exactly once); a diff runs the defects of the files it changes in one. The list of shards and their count are written together in the `scope` job, and a test
checks they agree. The job that the required checks list, `seeded-defects`, stays and joins the shards: it is red when any shard is not green, and never skipped.

## Measurement plumbing that fails closed (the review of this pull request)

A review of the tools around the gate found five places where a measurement that failed looked like a measurement of nothing, or a change that was not only an
addition looked like one. Each is fixed here, with a test that was seen to fail on the old code:

* **`diff-plan` listed mutants with `|| true`, `2>/dev/null || echo 0` and `grep -c ... || true`**, so a cargo-mutants or go-mutesting that failed gave "no mutants in the
  diff", which the diff gate accepts. Now a diff with Rust code must give a JSON list from a listing that succeeded (an empty diff has nothing to list: cargo-mutants prints
  nothing for it, and the list is written as `[]`), and the Go count comes from `tools/go_list_count.py`, which believes a count, zero included, only from a run that ended
  with go-mutesting's summary line and had no mutant the executor could not place.
* **`mutation_diff_exec.sh` took "the question could not be answered" for "the mutant is outside the diff"**: `diff_gate.py in-diff` exited 1 for both. It now answers 0, 1 or 2
  (could not tell), and the executor writes `unknown` (exit 3) for the third, which the gate refuses as an unreadable ledger line.
* **`nightly_last_full.py` counted any one green job of `p1` as a measured FULL** (`scope` is green seconds in). Only the verdict of the `quality` job, which joins every
  measurement, counts now (success or failure; a failed FULL is not repeated, its incident is open); a cancelled or shut-down run is measured again.
* **A new Go test file was always "an addition"**, but `TestMain` or `init` changes how every test of the package runs. A new or extended Go test file that adds either is `full`.
* **The Rust line coverage took every file whose path contains `/src/`.** It takes `/rust/src/`.

Not here, because they are decisions of the contract and not of the plumbing: `Patch` does not refuse two events on the same path (the checks of `previous` all run on the
original, so both pass and the second replaces the first; `ReadDiff` already refuses such a diff, a hand-built `PatchDiff` is not), and `Patch` parses the document again
for every event (about 4N+1 parses for N edits). The first needs a decision in ADR 0013 (which refusal code), the second a measurement before an optimization.

## What the gate does with the reports

`tools/quality_gate.py check --gamma-reports ... --gamma-listed ... --gomutants-reports ...` reads the `mutation-testing-elements` reports of both tools. It fails
closed, as it does for the classic tools:

* **Killed** is killed. **Survived**, **NoCoverage** and **Timeout** are not (cargo-gamma writes its timeouts and memory-limit stops as `Survived`).
* **Ignored**, a mutant nobody could judge (flaky, inconclusive), is not killed either and **stays in the denominator**: it can only lower the score. The number is
  printed under the score ("counted against the score, no verdict"). **CompileError** (unviable) is left out and printed, as for the classic tools.
* More than 90% killed, as before.
* **Completeness.** Every mutant listed (`cargo gamma list mutants --json`, with the same `--mutators`) must be measured by exactly one shard; for go-mutants, whose shard
  report names every mutant (the other shards' as `Ignored: another shard of this run measured this`), the shards must agree on the catalogue and measure each mutant
  once. A missing shard, a mutant twice or none, a mutant that is not listed, are `INCOMPLETE_SHARDS`, red.
* The self-test of the gate has a fault for each of these (24 new, 92 in all); it was run on the real whole-tree reports of both tools too.

## What it rests on, measured (2026-10-28, one machine, 12 cores, 10 jobs)

| | mutants | killed | survived | other | time |
|---|---|---|---|---|---|
| cargo-gamma, whole crate, every family | 5419 (4803 judged) | 4761 | 0 | 39 timeouts, 3 memory-limit, **464 flaky**, 152 unviable | 49 min |
| go-mutants `all`, whole module | 4603 | 4369 | 196 | 37 timeouts, 1 inconclusive | 11 min |

The whole-crate run with the families left out (`all,!literal,!stmt,!loop`), same machine: **3528 mutants judged, 3364 killed, 2 survived, 5 timeouts, 1 memory-limit stop,
156 flaky, 142 unviable, 16 minutes**. The gate counts the flaky ones against the score: 3364 / 3528 = **95.35%**, a clear margin over 90%. The two survivors are in
`src/parse.rs` (line 731, a reversed two-element array, probably equivalent; line 746, `&& exp.is_some()` removed).

cargo-gamma's own advice report shows where the time went: the `literal` family took 49% of the run, `stmt` 13%, `loop` 9% (71% together) and found no survivor in the
whole tree; 315 of the 464 flaky mutants are theirs. That is the reason for leaving them out of the pull request's full run. It is **not** a claim that they are useless:
they found survivors in new code (the diff of #107), which is why the diff mode keeps every family and the nightly shadow counts them over the whole tree.

## What this does not claim

* **At merge time the classic evidence on the code a pull request changes is kept.** The classic diff jobs (cargo-mutants and go-mutesting on the changed lines) run in the
  pull request's full run too, and `quality` wants them to have run; only the whole-tree measurement moved to the fast tools. What the full run of a pull request no longer
  does is the classic whole-tree measurement, and go-mutants does not make go-mutesting's operand-level mutations (`a && b` to `true && b`, a constant moved by one, `break`
  to `continue`, a returned value dropped: 14 of go-mutesting's 33 survivors on nine past pull requests are of these kinds). Those are found by the nightly FULL, after the merge.
* The classic tools stay the nightly's reference. The two sets of mutants are different (cargo-gamma makes about 4.3 times as many as cargo-mutants), so their scores are
  not comparable; the overlap of what they find is partial in both directions (go-mutesting 33 survivors on nine past pull requests, go-mutants 26 balanced / 47 `all`;
  19 of the 33 on the same line).
* The speed is real for go-mutants (11 minutes for the whole module) and for cargo-gamma per mutant on the diff, but a **whole-tree** cargo-gamma run costs more machine
  time than cargo-mutants (about 490 against 190 job-minutes), because it makes more mutants and each reaches the larger test binaries. Sharding spreads it; it does not
  shrink it. The families left out are what brings it back.
* go-mutants cannot be handed a narrower test command here: it runs the test binaries it built once, and a custom `go test -run ...` command makes it list the packages
  of the snapshot root, which in this layout (the module is `go/`, the tests read `../corpus`) is a workspace root with no package. The weakened-test proof therefore
  weakens the tree (only the test file that does not exercise `recognize.go` stays) and the Go diff shadow is not there yet (`--changed` selected no mutant; a run with
  `--include` on the changed files is possible and would be the next step).
* The flaky mutants are the open point of cargo-gamma: they are counted against the score here, so a rise shows up as a lower score and not as a hidden one.

## Required checks

The required checks stay the ones that exist whatever the mode is: `quality`, `seeded-defects`, `fuzz`, and the always-present jobs (`tools/test-evidence/required-checks.json`).
**The jobs of this change must not be required by name**: `fast-list`, `fast-mutation-rust (0..5)`, `fast-mutation-go (1..3)`, `seeded-defects-shard (0..7)`, `diff-gamma`
and `shadow-gamma` exist only in some modes (a ruleset that required them would wait for a check that a diff-mode pull request never creates). They are covered by
`quality`, which is required and which is red unless exactly the jobs of the chosen mode and engine ran and passed (`fast_ran`, `fast_not` in its first step), and by
`seeded-defects`, which joins its shards. `diff-proof` and `patch-proof` are always there and remain the owner's question for the ruleset and the cic-factory nightly
profile; nothing in this change depends on them.
