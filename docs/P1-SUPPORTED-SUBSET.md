# P1: the implemented slice and its supported subset

Status: **first vertical slice**, Rust (`rust/`) and Go (`go/`), one shared corpus. It is
implementation, not a decision: it follows the accepted ADRs and re-opens none of them.

## What exists

| Function | Rust | Go | What it does |
|---|---|---|---|
| parse | `parse`, `parse_with` | `Parse`, `ParseWith` | One YAML document to a `Node` tree with ADR 0007 source positions; `DUPLICATE_KEY` (parse phase) |
| recognize / validate | `recognize` | `Recognize` | ADR 0001 recognition at a synthetic position, then the position validation of ADR 0003/0007 (`MAP_AT_SCALAR_POSITION`, `UNKNOWN_FIELD`, `EXPANDED_WITH_EXTRA_KEY`) |
| emit | `emit` | `Emit` | Deterministic block-style YAML; the text always parses back to the same value |

The expanded key set `A` is an argument, never a constant of the library. The input is
never modified (a test per language checks it).

## Evidence

- **Shared corpus.** Every case of kind `recognize` (31: 30 in `recognition`, 1 in `presence`) runs in both
  languages, loaded with an independent YAML library, and compares class, `elements`, `opaque_value` and the
  complete error list including phase and source location. The runner counts the other kinds and prints them as
  `NOT RUN`; a green run does not cover them (`rust/tests/corpus.rs`, `go/corpus_test.go`).
- **Shared subset vectors** (`testdata/p1/subset.json`): what `parse` reads, what it refuses, what `emit`
  writes. Hand-written expectations, read by both languages. These are not corpus cases (the corpus format is
  closed by ADR 0005).
- Every document of the recognize cases survives `emit` then `parse` in both languages.
- **Branch coverage** (ADR 0009, `cic-branch/1`, the pinned `gobranch` and `rustbranch`) is gated in CI on
  product code, fail closed: workflow `p1`. At the time of writing 95.62% (Rust) and 93.58% (Go).

## Supported subset

`parse` reads: one document; block and flow collections; quoted and block scalars (strings); plain scalars
`null`, `~`, empty, `true`/`false` (`true`, `True`, `TRUE`, and the same for `false`), decimal integers that fit
64 bits (`+`/`-` allowed); every other plain scalar is a string. Mapping keys are the text of a scalar.

Refused with an explicit error, never reinterpreted (`UNSUPPORTED_SYNTAX`): anchors, aliases, tags, merge keys
(`<<`), non-scalar keys, more than one document, plain scalars that look like floats (`1.5`, `.5`, `1e3`, `.inf`,
`.nan`), hex/octal (`0x1F`, `0o17`), integers with a leading zero, integers beyond 64 bits. `YAML_SYNTAX` when the
YAML library rejects the text. `LIMIT_EXCEEDED` past the limits (1 MiB, depth 64, 100 000 nodes).

The three codes above are **not** in `corpus/codes.yaml`; they mark the boundary of this slice and are not part
of the ADR 0007 machine response. The limits are implementation defaults, not decided values.

Positions: the vectors' coordinates are reproduced by both YAML libraries (`saphyr-parser`, `yaml.v3`):
second-key, mapping (first key in block style, opening brace in flow style), BOM, CRLF, lone CR, non-ASCII.

`recognize` works at the synthetic positions of the corpus (`scalar`, `opaque`, `structured`, `list_of` records).
A shape it has no rule for (a scalar where a mapping or a list is declared) is `UNSUPPORTED_SYNTAX`, not accepted.

## Findings

1. **One error where ADR 0007 says "all".** `recognition.structured-partial-is-short-then-rejected` feeds two
   unknown keys (`access`, `value`) and expects one `UNKNOWN_FIELD` without a `logical_path`. ADR 0007 says
   `validate` reports all errors and that `(logical_path, code)` is unique in a response. Both fit only if the
   response carries no path and so holds one error per code, which is what this slice does (the one with the
   smallest path). Reporting every unknown field needs `logical_path` in the response, and no vector asserts it.
2. The two YAML libraries differ outside the vectors. `yaml.v3` parses the whole text first, so a syntax error
   later in the text hides an earlier duplicate key (the Rust parser reports the duplicate); this difference
   remains. U+0085, U+2028 and U+2029 are line breaks to `yaml.v3` and not to `saphyr-parser`
   (`a: 1<U+0085>b: 2` is two entries in Go and a syntax error in Rust, found by a probe), so a text containing
   one is refused in both languages (`UNSUPPORTED_SYNTAX`) instead of being read two ways. Both libraries have
   their own nesting limit, which answers `YAML_SYNTAX` before ours for absurd depth.
3. `saphyr-parser` does not accept a tab after the `:` of a block mapping value (`b:\t2`); a tab inside flow
   collections is accepted and counted as one column.

## Not in this slice (stated, not hidden)

- Other corpus kinds: `validate` (66), `path_*` (125), `diff` (66), `collapse`, `expand`, `roundtrip`, `basis`,
  `equality`, `grammar_manifest` (the last is run by `make corpus`'s own tooling).
- The `shape` presence rules and type checks, Access vocabulary (`inherit`), `conformance` origin.
- Schema loading from the pinned real schemas; positions come from the synthetic model only.
- The remaining ADR 0009 / PHASES.md gates: mutation score above 90%, property tests, fuzzing, line coverage
  reporting. They are follow-up work and are **not** satisfied by this change.
- Source-preserving emit (P3b), expand/collapse (P2), normalize (P3), diff/patch (P4), materialize/receipt (P5).
