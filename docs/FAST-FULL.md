# The full run of a pull request: cargo-gamma and go-mutants

The full mutation run took about an hour (51 minutes on #106) and grows with the code: the mutants of the whole tree rose by about 20% in four days of P3b and P4
(Rust 1059 to 1267, Go 2148 to 2667 by go-mutants' balanced count), and the phases up to P7 are still to come. The seeded-defects job alone (33 minutes, one serial
step, 40 allowed) was close to its time limit. This note says what changed, what it rests on, and what it does not claim.

## Who measures what

| Run | Rust | Go |
|---|---|---|
| Pull request, **diff** mode (the gate is unchanged) | cargo-mutants on the lines of the diff | go-mutesting on the lines of the diff |
| Pull request, diff mode, beside the gate | cargo-gamma on the lines of the diff, **every** family, a *shadow* (`diff-gamma`): gates nothing, survivors in the run summary | none yet (go-mutants' `--changed` selected no mutant in the layout of this repository, see below) |
| Pull request, **full** mode (`scope` says `engine=fast`) | cargo-gamma 0.3.0, every family but `literal`, `stmt`, `loop`, 6 shards | go-mutants (pinned commit), profile `all`, 3 shards |
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

New job names: `seeded-defects-shard (0..7)` (the joining job `seeded-defects` is the required one and keeps its name), `diff-gamma` (a shadow, diff mode), `fast-list`, `fast-mutation-rust (0..5)`, `fast-mutation-go (1..3)` (and `shadow-gamma (0..7)` on the nightly). The ruleset of `main` and the nightly profile of
cic-factory list required job names by hand: adding them is the owner's.
