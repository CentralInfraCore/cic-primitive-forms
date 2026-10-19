# P1: the implemented slices, their supported subset and their quality gates

Status: **two slices** - parse/recognize/emit (PR #22) and presence validation - in Rust (`rust/`) and Go (`go/`), one shared corpus. It is
implementation, not a decision: it follows the accepted ADRs and re-opens none of them.

## What exists

| Function | Rust | Go | What it does |
|---|---|---|---|
| parse | `parse`, `parse_with` | `Parse`, `ParseWith` | One YAML document to a `Node` tree with ADR 0007 source positions; `DUPLICATE_KEY` (parse phase) |
| recognize / validate | `recognize` | `Recognize` | ADR 0001 recognition at a synthetic position, then the position validation of ADR 0003/0007 (`MAP_AT_SCALAR_POSITION`, `UNKNOWN_FIELD`, `EXPANDED_WITH_EXTRA_KEY`) |
| emit | `emit` | `Emit` | Deterministic block-style YAML; the text always parses back to the same value |
| presence validation | `validate` | `Validate` | Absent / null / value against a Shape: `mandatory`, `optional`, `nullable`, scalar type, `min_items`, composite fields; every error with `logical_path`; default *candidates* (see below) |

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

## Presence validation (ADR 0003, ADR 0007)

`validate(node, shape, A)` takes `Option<&Node>` / `*Node`: **nil is a field that is absent, `Null` is a field
that is present with the value null.** They are different states with different answers.

| Declared | Absent | `null` (or an empty scalar) | Other value |
|---|---|---|---|
| `mandatory`, not nullable | `MISSING_MANDATORY` | `NULL_NOT_ALLOWED` | by type |
| `mandatory`, `nullable` | `MISSING_MANDATORY` | valid | by type |
| `optional`, not nullable | valid, stays absent | `NULL_NOT_ALLOWED` | by type |
| `optional`, `nullable` | valid, stays absent | valid | by type |

* **A default is a candidate and nothing more.** An absent optional field that declares a default is listed
  in `default_candidates` (`$` for the root, otherwise the canonical path). Nothing is substituted, no Role
  eligibility is decided (P3), and `null`, `""`, `0` and `false` never trigger it.
* **Types.** `string`, `integer`, `boolean` scalars; a list with `min_items` (inclusive); a composite with
  `fields`; an opaque field takes any value. A mapping where a scalar is declared is
  `MAP_AT_SCALAR_POSITION`. An unknown key of a composite is `UNKNOWN_FIELD`.
* **Anchors (ADR 0007).** A missing mandatory field is anchored at its *parent* mapping (an absent field has
  no text); an explicit `null` or `~` at the *value*; an empty scalar (`a:`), which has no text of its own, at
  the *key*; `MIN_ITEMS_NOT_MET` at the sequence; `UNKNOWN_FIELD` at the key.
* **Response.** Every error of the phase, ordered by canonical `logical_path` segment by segment (so `$.a.x`
  comes before `$.a b`), then code. Recognition (ADR 0001) runs first at every node.
* The fixture notation `schema.synthetic.shape` is bound in the corpus runners to the real Shape members
  (`shape_type`, `scalar_type`, `mandatory`/`optional`, `nullable`, `default`, `fields`, `min_items`); the
  library takes those members and defines no schema of its own.
* Out of scope on purpose: `state_surface`. A missing observation there is a coverage fact (ADR 0003 rule 8),
  not an authoring-validation error and not the observation state `absent`; the library does not decide it.

## Explicitly not supported (each is an error, never a reinterpretation)

| Input | Answer |
|---|---|
| anchors, aliases, tags, merge keys (`<<`), non-scalar keys, more than one document | `UNSUPPORTED_SYNTAX` |
| plain scalars that look like floats (`1.5`, `.5`, `5.`, `1e3`, `.inf`, `.nan`), hex or octal (`0x1F`, `0o17`), integers with a leading zero, integers beyond 64 bits | `UNSUPPORTED_SYNTAX` |
| the characters U+0085, U+2028, U+2029 anywhere in the text | `UNSUPPORTED_SYNTAX` (see finding 2) |
| text the YAML library rejects | `YAML_SYNTAX` |
| more than 1 MiB, depth 64 or 100 000 nodes | `LIMIT_EXCEEDED` |
| a scalar where a mapping or a list is declared, a non-record list element, a list inside a record | `UNSUPPORTED_SYNTAX` (the slice has no rule) |
| a value of the wrong type in presence validation (a string where an integer is declared, a list where a mapping is declared, ...) | `UNSUPPORTED_SYNTAX`: the registry has **no code** for it (finding 4) |
| an expanded Access node inside a validated document | `UNSUPPORTED_SYNTAX`: its `value` is validated with expand/collapse (P2) |
| Shape types `number`, `bytes`, `set`, `choice`, `item_choice`, list element shapes, schema validity rules (`default` with `mandatory`, `default: null` with `nullable`) | not modelled |

These three codes are **not** in `corpus/codes.yaml`; they mark the boundary of the slice and are
not part of the ADR 0007 machine response. The limits are implementation defaults, not decided
values. Also out of the slice: the other corpus kinds (`validate` 66, `path_*` 125, `diff` 66,
`collapse`, `expand`, `roundtrip`, `basis`, `equality`), the `shape` presence rules and type checks,
the Access vocabulary, schema loading from the pinned real schemas, source-preserving emit (P3b),
expand/collapse (P2), normalize (P3), diff/patch (P4) and materialize/receipt (P5).

## Corpus coverage: run and `NOT RUN`

| Suite | Kind | Cases | Status |
|---|---|---|---|
| recognition, presence | `recognize` | 31 | **run**, both languages, complete machine response |
| presence | `validate` (shape) | 29 | **run**, both languages, complete machine response, input unchanged |
| conformance, inherit | `validate` (slot) | 37 | NOT RUN (Access vocabulary, `conformance` origin) |
| recognition, presence, conformance | `collapse` | 15 | NOT RUN (P2) |
| presence, inherit | `roundtrip`, `expand`, `equality` | 11 | NOT RUN (P2) |
| conformance | `basis` | 16 | NOT RUN |
| paths | `path_parse`, `path_format`, `path_resolve`, `path_of`, `path_stability` | 125 | NOT RUN |
| diff | `diff`, `diff_stability` | 66 | NOT RUN (P4) |
| grammar | `grammar_manifest` | 9 | not run by these runners (checked by `make corpus`) |

Each runner prints the run count and the `NOT RUN` tally, and asserts the number it ran (31 and 29), so a
vector that stops being run is a failure and a green run cannot be read as covering the rest.

## What agreement between the two languages is claimed for

* the 31 `recognize` and the 29 shape-based `validate` cases of the corpus: class, `elements`,
  `opaque_value`, `default_candidate`, `stays_absent` and the complete machine response (code, phase,
  source location, logical path), compared in both languages;
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
| Line coverage | `cargo-llvm-cov` 0.9.1 / `go test -cover` (statements: Go has no line metric) | 99.23% (771/777) | 100.00% (388/388) |
| Branch coverage (`cic-branch/1`, ADR 0009) | `rustbranch` / `gobranch` | 97.35% (331/340) | 100.00% (333/333) |
| Mutation score | `cargo-mutants` 27.1.0 / `go-mutesting` v0.0.0-20261002081621-9ce5f731e321 | 100.00% (174 caught, 0 missed, 16 unviable) | 98.96% (380 killed, 4 survived) |
| Property tests | `proptest` / `rapid` | 8 properties, 512 cases each | the same 8, 1000 cases |
| Fuzzing | `cargo-fuzz` 0.13.2 / native `go test -fuzz` | targets `parse` and `validate`, 60 s each in CI | `FuzzParse` and `FuzzValidate`, 60 s each in CI |
| Corpus parity | the shared corpus runners | 31 of 31 `recognize`, 29 of 29 `validate` | the same |

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
recognition follows the key set and leaves its input unchanged. For validation: a document built to conform to a random shape has no violation; leaving out one mandatory field gives only `MISSING_MANDATORY` errors, at most one at that path; the default candidates are exactly the absent optional fields that declare a default; for any document against any shape the answer is unsupported or a response of one phase, ordered, with no repeated (path, code), and the input is unchanged. **Fuzzing** checks on every input
that parse does not panic, that a document that parses survives emit then parse, and that its
positions lie inside the text (`parse`); and that validation of whatever parses against a fixed shape that
uses every rule leaves the document alone and gives an ordered, unique, single-phase response (`validate`);
the seeds are the shared vectors and the presence inputs.

### Proof that each gate can fail (what was run, and what it did)

| Gate | Seeded fault | Result |
|---|---|---|
| Line coverage | only the subset test runs | Rust 49.22% (378/768), Go 26.29% (102/388), both `BELOW_THRESHOLD`; reproduced in CI |
| Mutation score | `recognize` is mutated, only tests that do not use it run | Rust 0.00% (0/23), Go 23.53% (16/68), both `BELOW_THRESHOLD`; reproduced in CI |
| Branch coverage | Rust runs only the behaviour tests, Go runs one test | Rust 64.41% (219/340), Go 0.00% (0/333), both `BELOW_THRESHOLD`; reproduced in CI |
| Quality gate script | 21 faults | every reason turns it red (`quality_gate.py selftest`, CI) |
| Property tests | a seeded bug in `emit` (backslash not escaped), by hand, in both languages | all three round-trip properties fail with a minimal counterexample; not reproduced in CI |
| Fuzz job | a committed input starting with `CANARY` fails each of the four targets when `CIC_FUZZ_CANARY` is set | red with the switch, green without; reproduced in CI |
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
3. The parser reports an empty value (`a:`) as the scalar `~` with an empty span. An explicit `~` has a span of one character. The slice tells them apart by the span, so only the explicit one has a position and the empty one is anchored at its key, as `presence.text-null-empty-scalar-anchors-key` requires. In Go the empty scalar is a node with no text at all.
4. **A value of the wrong type has no error code.** The registry has `MAP_AT_SCALAR_POSITION` for a mapping at a scalar position, but nothing for a string where an integer is declared, a scalar where a list or a mapping is declared, and so on. ADR 0003 says the Shape type contracts decide validity; the codes for those violations are not defined, and no vector asks for one. The slice refuses them explicitly (`UNSUPPORTED_SYNTAX`) instead of inventing a code. This is a gap for the ADR owner, not decided here.
5. The banners of `make corpus` and `make coverage` ("implementation conformance NOT RUN", "no Rust or
   Go code is measured yet") describe those structural tools and are stale next to the new runners.
