# P1: the implemented slice, its supported subset and its quality gates

Status: **first vertical slice**, Rust (`rust/`) and Go (`go/`), one shared corpus. It is
implementation, not a decision: it follows the accepted ADRs and re-opens none of them.

## What exists

| Function | Rust | Go | What it does |
|---|---|---|---|
| parse | `parse`, `parse_with` | `Parse`, `ParseWith` | One YAML document to a `Node` tree with ADR 0007 source positions; `DUPLICATE_KEY` (parse phase) |
| recognize / validate | `recognize` | `Recognize` | ADR 0001 recognition at a synthetic position, then the position validation of ADR 0003/0007 (`MAP_AT_SCALAR_POSITION`, `UNKNOWN_FIELD`, `EXPANDED_WITH_EXTRA_KEY`) |
| emit | `emit` | `Emit` | Deterministic block-style YAML; the text always parses back to the same value |

The expanded key set `A` is an argument, never a constant of the library. The input is never
modified (a test per language checks it).

The machine response of `recognize` is the ADR 0007 one: each violation carries `code`, `phase`,
`source_location` and the canonical ADR 0006 `logical_path`, every validation error is reported,
and they are ordered by path, then code. The path is left out where ADR 0007 leaves it out: the
parse phase, a single position with no structure around it, a name that cannot be written (empty
or holding U+0000 to U+001F or U+007F), and a list element, whose identity the synthetic schema
does not declare. Only those path-less errors cannot be told apart, so only they collapse to one
per code.

## Supported subset

`parse` reads: one document; block and flow collections; quoted and block scalars (strings); plain
scalars `null`, `~`, empty, `true`/`false` (`true`, `True`, `TRUE`, and the same for `false`),
decimal integers that fit 64 bits (`+`/`-` allowed); every other plain scalar is a string. Mapping
keys are the text of a scalar. `emit` writes keys longer than 512 characters in the explicit
`? key` form, because YAML limits an implicit key to 1024.

## Explicitly not supported (each is an error, never a reinterpretation)

| Input | Answer |
|---|---|
| anchors, aliases, tags, merge keys (`<<`), non-scalar keys, more than one document | `UNSUPPORTED_SYNTAX` |
| plain scalars that look like floats (`1.5`, `.5`, `5.`, `1e3`, `.inf`, `.nan`), hex or octal (`0x1F`, `0o17`), integers with a leading zero, integers beyond 64 bits | `UNSUPPORTED_SYNTAX` |
| the characters U+0085, U+2028, U+2029 anywhere in the text | `UNSUPPORTED_SYNTAX` (see finding 2) |
| text the YAML library rejects | `YAML_SYNTAX` |
| more than 1 MiB, depth 64 or 100 000 nodes | `LIMIT_EXCEEDED` |
| a scalar where a mapping or a list is declared, a non-record list element, a list inside a record | `UNSUPPORTED_SYNTAX` (the slice has no rule) |

These three codes are **not** in `corpus/codes.yaml`; they mark the boundary of the slice and are
not part of the ADR 0007 machine response. The limits are implementation defaults, not decided
values. Also out of the slice: the other corpus kinds (`validate` 66, `path_*` 125, `diff` 66,
`collapse`, `expand`, `roundtrip`, `basis`, `equality`), the `shape` presence rules and type checks,
the Access vocabulary, schema loading from the pinned real schemas, source-preserving emit (P3b),
expand/collapse (P2), normalize (P3), diff/patch (P4) and materialize/receipt (P5).

## What agreement between the two languages is claimed for

* the 31 `recognize` cases of the corpus: class, `elements`, `opaque_value` and the complete
  machine response (code, phase, source location, logical path), compared in both languages;
* `testdata/p1/subset.json`: what `parse` reads and refuses and what `emit` writes (hand-written
  expectations, one file read by both languages);
* the positions of those inputs: second key, mapping (first key in block style, opening brace in flow
  style), BOM, CRLF, lone CR, non-ASCII, leading line breaks.

**Not claimed:** agreement on the error *code* for an arbitrary faulty input. It is known to
differ: `yaml.v3` reads the whole text before the tree is walked, so a later YAML syntax error hides
an earlier duplicate key, while the Rust parser stops at the first problem in event order and reports
the duplicate. `documented_divergence_duplicate_key_before_a_later_syntax_error` (Rust) and
`TestDocumentedDivergenceDuplicateKeyBeforeALaterSyntaxError` (Go) assert each language's actual
answer. The two libraries also have their own nesting limit and their own wording of a syntax error.

## Quality gates (docs/PHASES.md, "Quality gates")

All on the product code of this slice (`rust/src`, `go/*.go` without tests), strictly above 90%,
failing closed, in workflow `p1`. Numbers are from the final code on the development machine; CI
prints the same table.

| Gate | Tool (pinned) | Rust | Go |
|---|---|---|---|
| Line coverage | `cargo-llvm-cov` 0.9.1 / `go test -cover` (statements: Go has no line metric) | 99.19% (613/618) | 100.00% (311/311) |
| Branch coverage (`cic-branch/1`, ADR 0009) | `rustbranch` / `gobranch` | 97.20% (278/286) | 100.00% (258/258) |
| Mutation score | `cargo-mutants` 27.1.0 / `go-mutesting` v0.0.0-20261002081621-9ce5f731e321 | 100.00% (151 caught, 0 missed, 14 unviable) | 98.76% (318 killed, 4 survived) |
| Property tests | `proptest` / `rapid` | 4 properties, 512 cases each | the same 4, 1000 cases |
| Fuzzing | `cargo-fuzz` 0.13.2 / native `go test -fuzz` | target `parse`, 60 s in CI | `FuzzParse`, 60 s in CI |
| Corpus parity | the shared corpus runners | 31 of 31 `recognize` cases | 31 of 31 |

`tools/quality_gate.py` decides line coverage and mutation score (`check`), and `selftest` runs 21
seeded faults, one per reason (`REPORT_MISSING`, `REPORT_FORMAT`, `MEASUREMENT_EMPTY`,
`BELOW_THRESHOLD`, exactly 90% is red).

**The four Go survivors are equivalent mutants**, none is a missed behaviour: an end-of-input clamp
that returns the same line (`line >= len`), a CRLF look-ahead whose only difference is a line start
nothing can refer to, an assignment of the zero value (`out.Kind = KindNull`), and an operand that is
always false for the segments it guards.

**What each property checks** (Rust and Go identically): emit then parse gives back the value for any
value of the subset including any string and key (long ones too); emit is a fixed point under parse;
parse never panics on arbitrary text and its positions lie inside the text on character boundaries;
recognition follows the key set and leaves its input unchanged. **Fuzzing** checks on every input
that parse does not panic, that a document that parses survives emit then parse, and that its
positions lie inside the text; the seeds are the shared vectors.

### Proof that each gate can fail (what was run, and what it did)

| Gate | Seeded fault | Result |
|---|---|---|
| Line coverage | only the subset test runs | Rust 61.74% (376/609), Go 32.15% (100/311), both `BELOW_THRESHOLD`; reproduced in CI |
| Mutation score | `recognize` is mutated, only tests that do not use it run | Rust 0.00% (0/23), Go 23.53% (16/68), both `BELOW_THRESHOLD`; reproduced in CI |
| Branch coverage | Rust runs only the behaviour tests, Go runs one test | Rust 75.52% (216/286), Go 0.00% (0/258), both `BELOW_THRESHOLD`; reproduced in CI |
| Quality gate script | 21 faults | every reason turns it red (`quality_gate.py selftest`, CI) |
| Property tests | a seeded bug in `emit` (backslash not escaped), by hand, in both languages | all three round-trip properties fail with a minimal counterexample; not reproduced in CI |
| Fuzz job | a committed input starting with `CANARY` fails the target when `CIC_FUZZ_CANARY` is set | red with the switch, green without; reproduced in CI |
| Performance of duplicate keys | the previous linear scan put back by hand (Go) | 16.9 s against a 5 s bound; not reproduced in CI |

## What these gates found (all fixed, with a regression test or a vector each)

1. `emit` wrote the key `<<` plain, which `parse` reads as a merge key and refuses: emit then parse
   was not a round trip. Found by a mutation survivor that led to the seed, then to a property
   generator that now draws it. `<<` is always quoted.
2. `emit` wrote a key longer than 1024 characters as an implicit key, which YAML forbids; the Rust
   fuzz target found it in under a minute (`regression-long-key` is in the seed corpus). Keys longer
   than 512 characters use the explicit `? key` form, in both languages.
3. A remaining difference was removed rather than documented: U+0085 is a line break to `yaml.v3`
   and not to `saphyr-parser` (`a: 1<U+0085>b: 2` is two entries in Go and a syntax error in Rust,
   found by a probe), so texts containing U+0085, U+2028 or U+2029 are refused in both.

## Findings about the contract

1. `recognition.structured-partial-is-short-then-rejected` expected one error for two unknown fields
   and the two structured vectors lacked their `logical_path`. That contradicted ADR 0007. The
   vectors are corrected (`$.access` and `$.value`, in path order); the code was not narrowed to fit.
2. `saphyr-parser` rejects a tab after the `:` of a block mapping value (`b:\t2`); a tab inside flow
   collections is accepted and counts as one column.
3. The banners of `make corpus` and `make coverage` ("implementation conformance NOT RUN", "no Rust or
   Go code is measured yet") describe those structural tools and are stale next to the new runners.
