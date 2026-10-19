# P1: the implemented slices, their supported subset and their quality gates

Status: **three slices** - parse/recognize/emit (PR #22), presence validation (PR #23) and the reading of the `access-policy/1` values (D-018) - in Rust (`rust/`) and Go (`go/`), one shared corpus. It is
implementation, not a decision: it follows the accepted ADRs and re-opens none of them.

## What exists

| Function | Rust | Go | What it does |
|---|---|---|---|
| parse | `parse`, `parse_with` | `Parse`, `ParseWith` | One YAML document to a `Node` tree with ADR 0007 source positions; `DUPLICATE_KEY` (parse phase) |
| recognize / validate | `recognize` | `Recognize` | ADR 0001 recognition at a synthetic position, then the position validation of ADR 0003/0007 (`MAP_AT_SCALAR_POSITION`, `UNKNOWN_FIELD`, `EXPANDED_WITH_EXTRA_KEY`) |
| emit | `emit` | `Emit` | Deterministic block-style YAML; the text always parses back to the same value |
| access-policy/1 reading | `read_mode`, `read_rule`, `bind_grammar`, `pinned_grammar_version`, `is_cert_pattern` | `ReadMode`, `ReadRule`, `BindGrammar`, `PinnedGrammarVersion`, `IsCertPattern` | `InheritanceMode` in grammar 1 or 2, an `access`/`modify` value, the grammar version binding derived from the verified pinned schema (see below) |
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

## Access reading (corpus kind `validate`, `slot`; ADR 0004, cic-primitives D-018)

The normative source is the pinned `proposals/atom-grammar/P0-4-ACCESS-GRAMMAR.md` and `schemas/atomic/access.yaml`
(`corpus/pins.yaml`, `cic-primitives-73ae469-71ee71ca`). The module reads and checks values; it resolves
nothing (P3), decides no permission and applies no default.

* **Grammar version binding is fail-closed.** The version is not a document key and has no default. It is the
  `spec.access_grammar.version` of the pinned schema, derived **after** the SHA-256 of the schema bytes matched the
  pin (`GRAMMAR_DIGEST_MISMATCH` otherwise), and the processing context must supply the same integer.
  Missing is `GRAMMAR_VERSION_MISSING` (checked first), a value that is not a supported integer (`"1"`, `true`, `3`)
  is `UNKNOWN_GRAMMAR_VERSION`, a difference is `GRAMMAR_VERSION_CONFLICT`. One `Grammar` value binds a whole
  document. The corpus runners read `access.yaml` from a checkout of `cic-primitives` at the pinned commit
  (`CIC_PRIMITIVES_ROOT`) and **fail**, not skip, when it is absent or differs.
* **`InheritanceMode`.** Grammar 1: boolean `true`, boolean `false` and integer `0` are three distinct, losslessly
  represented values (`inherit`, `no-inherit`, `reset`); `1`, `"true"`, `"0"`, `null`, a list or a mapping is
  `INVALID_INHERIT`; an enum name is `INHERIT_ENUM_IN_GRAMMAR_1`. Grammar 2: the enum names; a legacy spelling is
  `LEGACY_INHERIT_IN_GRAMMAR_2`. No conversion between versions, and writing a mode back gives the spelling that was
  read (a bijection per version, tested). **Fail-closed in Go as well as Rust:** a `Grammar` that was not bound to a supported version (the zero value or any other) reads nothing (`UNKNOWN_GRAMMAR_VERSION`, the upstream code) and writes nothing; only the three states can be written, and an invalid or unknown `Mode` gives no Node and an error; the set of supported versions is returned as a copy, so a caller cannot extend it. Rust enforces the same by type: a `Grammar` exists only after `bind_grammar` succeeds, `Mode` is a closed enum, and the supported set is a constant.
* **`access` / `modify`.** The symbol `inherit` is not a CertPattern list; `[]` is an explicit "nobody" and never the
  symbol; `[inherit]` is `INVALID_CERT_PATTERN`; `null`, a bare pattern string, a boolean or a mapping is
  `INVALID_ACCESS_VALUE`. A CertPattern is the upstream expression `attr=value` pairs joined by `,`
  (`attr` = `[A-Za-z][A-Za-z0-9.-]*`, a value that does not start with `=`, `,` or whitespace and holds no `=` or `,`);
  its whitespace class is Python's `\s` (Unicode White_Space plus U+001C to U+001F), and both languages are checked
  against that expression as an oracle by property test and fuzz target.
* Violations are anchored at the value (registry anchor `value`), with phase `validate`, and carry no `logical_path`
  (a single position).

### Where each code comes from

| Code | In `corpus/codes.yaml` | Traced to |
|---|---|---|
| `INVALID_INHERIT`, `INVALID_ACCESS_VALUE`, `INVALID_CERT_PATTERN` | yes (provisional) | ADR 0004 and the pinned specification, "Value rules" and "Spelling and versions" |
| `INHERIT_ENUM_IN_GRAMMAR_1`, `LEGACY_INHERIT_IN_GRAMMAR_2` | **no** | pinned specification, "Spelling and versions" |
| `GRAMMAR_VERSION_MISSING`, `UNKNOWN_GRAMMAR_VERSION`, `GRAMMAR_VERSION_CONFLICT` | **no** | pinned specification, "Grammar version binding" |
| `GRAMMAR_DIGEST_MISMATCH` | yes (stable, ADR 0002) | the pinned schema's bytes differ from the pin |

The five upstream codes are emitted unchanged, not renamed or invented. No vector uses them, and the corpus gate
rejects a registry code no case uses, so they cannot enter the registry yet: this is a reported gap (finding 6).

## Explicitly not supported (each is an error, never a reinterpretation)

| Input | Answer |
|---|---|
| anchors, aliases, tags, merge keys (`<<`), non-scalar keys, more than one document | `UNSUPPORTED_SYNTAX` |
| plain scalars that look like floats (`1.5`, `.5`, `5.`, `1e3`, `.inf`, `.nan`), hex or octal (`0x1F`, `0o17`), integers with a leading zero, integers beyond 64 bits | `UNSUPPORTED_SYNTAX` |
| the characters U+0085, U+2028, U+2029 anywhere in the text | `UNSUPPORTED_SYNTAX` (see finding 2) |
| control characters (U+0000 to U+0008, U+000B, U+000C, U+000E to U+001F, U+007F), a tab anywhere, a byte order mark after the first character, a directive line (`%...`), a document end marker (`...`), a `---` that is not the first content line (indented ones too; inside a block scalar and, when indented, inside a quoted scalar, `---`, `...` and `%` are text and read as such) | `UNSUPPORTED_SYNTAX` (parser parity, below) |
| a word that starts with `!`, `&` or `*`, or with `?` (outside the emitter's own `? key` form), a `#` right after `]` or `}`, outside quotes, comments and block scalar content | `UNSUPPORTED_SYNTAX` (parser parity, below) |
| a block scalar in a text that does not end with a line break, or one that holds nothing but line breaks | `UNSUPPORTED_SYNTAX` (parser parity, below) |
| an empty implicit mapping key (`: a`, `{: a}`) | `YAML_SYNTAX`: yaml.v3 refuses it, so the Rust parser does too |
| text the YAML library rejects | `YAML_SYNTAX` |
| more than 1 MiB, depth 64 or 100 000 nodes | `LIMIT_EXCEEDED` |
| a scalar where a mapping or a list is declared, a non-record list element, a list inside a record | `UNSUPPORTED_SYNTAX` (the slice has no rule) |
| a value of the wrong type in presence validation (a string where an integer is declared, a list where a mapping is declared, ...) | `UNSUPPORTED_SYNTAX`: the registry has **no code** for it (finding 4) |
| an expanded Access node inside a validated document | `UNSUPPORTED_SYNTAX`: its `value` is validated with expand/collapse (P2) |
| effective permission, `no-inherit` against `reset` semantics, surface rules, system defaults, request context (P3), `conformance` origin (ADR 0010), expand/collapse of Access nodes (P2), grammar-version migration (`migrate_mode`) | not implemented |
| Shape types `number`, `bytes`, `set`, `choice`, `item_choice`, list element shapes, schema validity rules (`default` with `mandatory`, `default: null` with `nullable`) | not modelled |

These three codes are **not** in `corpus/codes.yaml`; they mark the boundary of the slice and are
not part of the ADR 0007 machine response. The limits are implementation defaults, not decided
values. Also out of the slice: the other corpus kinds (`validate` 66, `path_*` 125, `diff` 66,
`collapse`, `expand`, `roundtrip`, `basis`, `equality`), the `shape` presence rules and type checks,
the Access vocabulary, schema loading from the pinned real schemas, source-preserving emit (P3b),
expand/collapse (P2), normalize (P3), diff/patch (P4) and materialize/receipt (P5).

## Corpus coverage: run and `NOT RUN`

Exact: `the_run_and_not_run_accounting_is_exact` (Rust) and `TestTheRunAndNotRunAccountingIsExact` (Go) count every
case of the corpus (339), assert that each is either run or listed here, and assert **81 run + 258 NOT RUN**.

| Suite | Kind | Cases | Status |
|---|---|---|---|
| recognition | `recognize` | 30 | **run**, both languages, complete machine response |
| presence | `recognize` | 1 | **run** |
| presence | `validate` (shape) | 29 | **run**, complete machine response, input unchanged |
| inherit | `validate` (slot `inherit_key`, `inherit_policy`, `access_modify`) | 21 | **run**, complete machine response, typed value preserved, grammar bound from the verified pin |
| conformance | `validate` (slot `conformance`) | 16 | NOT RUN (ADR 0010, conformance basis) |
| conformance | `basis` | 16 | NOT RUN (ADR 0010) |
| conformance, presence, recognition | `collapse` | 15 | NOT RUN (P2) |
| inherit, presence | `roundtrip`, `expand`, `equality` | 11 | NOT RUN (P2) |
| paths | `path_parse` 57, `path_resolve` 34, `path_of` 18, `path_format` 9, `path_stability` 7 | 125 | NOT RUN |
| diff | `diff` 62, `diff_stability` 4 | 66 | NOT RUN (P4) |
| grammar | `grammar_manifest` | 9 | not run by these runners (checked by `make corpus` and `tools/grammar_identity.py`) |

A runner asserts the number it ran (31, 29, 21), so a vector that stops being run is a failure, and a green run cannot
be read as covering the rest.

## What agreement between the two languages is claimed for

* the 31 `recognize`, the 29 shape-based and the 21 inheritance `validate` cases of the corpus: class, `elements`,
  `opaque_value`, `default_candidate`, `stays_absent`, `preserved`, `kind`, `is_inherit` and the complete machine response (code, phase,
  source location, logical path), compared in both languages;
* `testdata/p1/subset.json` and `testdata/p1/access.json`: what `parse` reads and refuses, what `emit` writes, and the
  readings of `InheritanceMode`, `access`/`modify`, CertPatterns and the grammar version binding (hand-written
  expectations, one file each, read by both languages);
* the positions of those inputs: second key, mapping (first key in block style, opening brace in flow
  style), BOM, CRLF, lone CR, non-ASCII, leading line breaks.

* the 502 inputs of `testdata/p1/parity.json` (the shared regression corpus of the parser parity work, below):
  the complete outcome of `parse` (the tree with the position of every node and key, or the contract
  violation, or the refusal) is pinned and read by both languages.

**Not claimed:** general parity of the two parsers. The differential found real differences (below), the
closed ones have a pinned regression case each, and 13 hand-picked open ones (flow-context spellings
that saphyr reads and yaml.v3 refuses, or the reverse) are pinned with both outcomes and a test that
fails when one of them stops being a difference. The error *code* of a refusal may differ
(`YAML_SYNTAX` against `UNSUPPORTED_SYNTAX` is the same refusal for a different reason); that a text is
refused, and what a contract violation says, is compared. The two libraries also have their own
nesting limit and their own wording of a syntax error.

## Parser parity (Rust against Go)

`tools/parity` compares `parse` in the two languages on the same inputs: `gen_inputs.py` writes
30 005 deterministic inputs (the texts the repository holds, the pinned `access.yaml`, 450 hand-written
cases that YAML readers disagree on, and seeded random edits of them), `rust/tests/parity_dump.rs` and
`go/parity_dump_test.go` write the outcome of each as one JSON line (the tree with positions, the
contract violations, or the refusal code), `compare.py` groups what differs. A refusal against a refusal
(any non-contract codes) counts as agreement; everything else, an accept against a refuse, two
different trees or positions, a contract error against anything else, is a divergence.

| | divergent inputs of 30 005 |
|---|---|
| before (main, `2c58713`) | 3 134 in 12 groups |
| after | 287 in 6 groups, all in the open classes below (228 hold a flow collection or a quote followed by `#`, 59 are quoted scalars that continue on an unindented line or are followed by more text) (mutated variants of the 13 hand-pinned inputs. The figure belongs to the generated set: every regression case added to `gen_inputs.py` is also a seed of the random edits, so the set, and with it this count, changes with it (274, 299, 326, 300 and 287 at successive heads of this PR) and is not comparable with the 3 134 of the old code; what the gate holds fixed is each divergence's input and outcomes, not the count) |

What the differential found and what was done about it (each has pinned cases in `parity.json`):

* **Error priority.** See "The order in which problems are reported" below.
* **Positions.** The Rust parser located an indentless block sequence (`a:` then `- 1`) at its first item
  and a `- - a` chain at the inner dash, a block scalar at its first content line, and gave an implicit
  null in a flow collection (`{a}`) or an empty document a position. Now a block sequence is located at its
  `-`, a block scalar at its `|` or `>`, and a value the source does not spell has no position, as in Go.
* **Values.** A block scalar at the end of a text without a final line break, and one made of line
  breaks only, have different values in the two libraries: refused. Tags, anchors and aliases: yaml.v3
  drops a bare `!` (so `a: ! 1` is the integer 1) and reads `*` and `&` words its own way: a word that
  starts with one is refused in both languages by a text scan (`scan_words`) that skips quoted scalars,
  comments and block scalar content.
* **Inputs the readers do not agree are YAML.** Tabs, control characters, directives, document markers
  other than a leading `---`, a byte order mark inside the text, explicit `?` keys other than the
  emitter's own form, a comment touching `]` or `}`, an empty implicit key: refused in both languages
  by one pre-parse scan (`prescan`), before either YAML library is asked, in the same order.

### The order in which problems are reported

One rule, the same in both languages, tested with explicit expected codes in both
(`the_order_in_which_problems_are_reported_is_fixed` / `TestTheOrderInWhichProblemsAreReportedIsFixed`),
not by comparing the two with each other. The first step that finds a problem decides the answer:

1. **Size** over the limit: `LIMIT_EXCEEDED`.
2. **U+0085, U+2028, U+2029** anywhere: `UNSUPPORTED_SYNTAX`.
3. **The text scan** (`prescan`, `scan_words`), whole text, before either YAML library is asked:
   control characters, tabs, a byte order mark inside the text, then, in text order, directives,
   document markers, tags, anchors, aliases, explicit keys, a comment touching `]` or `}`:
   `UNSUPPORTED_SYNTAX`. One of these *anywhere* in the text hides a YAML syntax error *anywhere* in
   it: the scan does not know about syntax, and a text with a tab is refused for the tab even if it
   is also malformed.
4. **A YAML syntax error anywhere** in the text: `YAML_SYNTAX`. It beats everything below, wherever
   the problems are (a duplicate key, a `1.5`, a merge key, a non-scalar key before it or after it).
5. **A second document**: `UNSUPPORTED_SYNTAX`, ahead of what the first document holds. (No text reaches
   this step, since the scan refuses every `---` after the first content line; the check stays as a guard
   and is tested through the internal `read`.)
6. **The first problem in document order** of: an unsupported construct (`UNSUPPORTED_SYNTAX`), a
   duplicate key (`DUPLICATE_KEY`, the contract error), a node or depth limit (`LIMIT_EXCEEDED`).

So "a syntax error anywhere wins" holds for the problems of step 6 only; the problems of steps 1 to 3
are decided before the YAML syntax is looked at.

The scan reads a line as text, not as a marker or a directive, inside a block scalar and, if it is
indented, inside a quoted scalar; a `---` or `...` that starts a line inside a quoted scalar is
refused (the YAML spec ends the document there and the two readers disagree on what follows).
`marker_like_text_inside_scalars_is_content` (Rust) and `TestMarkerLikeTextInsideScalarsIsContent`
(Go) hold the YAML 1.2 values as expectations.

Open (pinned with both outcomes in `known_divergences`, see `testdata/p1/parity.json`): plain scalars in a
flow collection that hold an indicator character (`{a?}`, `[|]`, `{:1}`, `{aa:}`, `{a:}`, `[a:]`, `{-}`), a quoted scalar
followed directly by `#`, `{a: 1,a: 2:}`, a flow mapping continued on a line that is not indented
(`a: {b: <CR>1,1,` and similar), and a quoted scalar that continues on an unindented line. They are the
libraries disagreeing on what YAML 1.2 allows in flow context; the narrowing that closes them (a flow
scan in `scan_words`) is not done, and nothing in the repository's schemas, vectors or emitter output
uses these spellings.

The gate numbers of the parity work are the rows of "Quality gates" below (whole crate and module, not only the
parser). The one Rust mutation survivor is `read`, `docs > 1`, equivalent because the check after the loop gives the
same answer; time-outs count as detected. 33 seeded defects, 15 of them for this work, all caught. The property
tests found two real faults while this was written (`a[&: 1` and `a *b: 1`, plain scalars that the word
scan took for an anchor and an alias), both fixed and pinned. The fuzz job found a third (a key past 512 characters that starts with `-` or `:` is
emitted as `? -kkk…`, which the explicit-key rule refused): fixed, with a regression test in each language. A second fuzz run found a fourth (`a? *` is one plain scalar, the emitter writes it as a key, and the scan took its `*` for an alias because it counted a `?` after text as an indicator): `-` and `?` are indicators only where a scalar can start. Found by a brute-force check of every string of up to three characters of YAML indicators in four positions; that check is now a test in each language (`every_short_string_of_indicators_round_trips`), and a run over 3.4 million longer strings, with long keys, is clean.

Run it: `python3 tools/parity/gen_inputs.py in.hex --upstream "$CIC_PRIMITIVES_ROOT"`, then
`PARITY_IN=in.hex PARITY_OUT=rust.jsonl cargo test --test parity_dump` and
`PARITY_IN=in.hex PARITY_OUT=go.jsonl go test -run TestDumpParseOutcomes .`, then
`python3 tools/parity/compare.py in.hex rust.jsonl go.jsonl --allow testdata/p1/parity-known.txt`.
The `parity` job of `p1.yml` does exactly this. The allow list names each known divergence by its input *and*
by a digest of both outcomes, so it is red on a new divergence and equally on a known one whose answer on either
side has changed; `tools/parity/selftest.py` breaks the real outputs in those ways (and one that must stay
green, a fix) and requires the right verdict, in the same job. `make_corpus.py` rebuilds `parity.json` from `HAND` and `EXTRA` of `gen_inputs.py`.

## Quality gates (docs/PHASES.md, "Quality gates")

All on the product code of this slice (`rust/src`, `go/*.go` without tests), strictly above 90%,
failing closed, in workflow `p1`. Numbers are the CI measurements of PR #25 at commit `36809bab`, on all of
`rust/src` and `go/*.go`, not only on the parser. Later commits of the PR change documentation, tests, the corpus, the CI workflow (the `quality` job in shards, `docs/QUALITY-SHARDING.md`) and the PR text only;
`git diff 36809bab..HEAD -- rust/src ':(glob)go/*.go' ':(exclude)go/*_test.go'` (the product code, without the tests) is empty, which is what makes the numbers the ones of the head.

| Gate | Tool (pinned) | Rust | Go |
|---|---|---|---|
| Line coverage | `cargo-llvm-cov` 0.9.1 / `go test -cover` (statements: Go has no line metric) | 98.69% (1278/1295) | 100.00% (600/600) |
| Branch coverage (`cic-branch/1`, ADR 0009) | `rustbranch` / `gobranch` | 97.23% (633/651) | 100.00% (730/730) |
| Mutation score | `cargo-mutants` 27.1.0 / `go-mutesting` v0.0.0-20261002081621-9ce5f731e321 | 99.77% (432 of 433 viable mutants caught or timed out, 1 missed) | 97.67% (798 killed, 19 survived) |
| Property tests | `proptest` / `rapid` | 14 properties, 512 cases each | the same properties (13 tests: "no value is a valid mode in both grammars" is part of the type-preservation test), 1000 cases |
| Fuzzing | `cargo-fuzz` 0.13.2 / native `go test -fuzz` | targets `parse`, `validate` and `access`, 60 s each in CI | `FuzzParse`, `FuzzValidate` and `FuzzAccess`, 60 s each in CI |
| Corpus parity | the shared corpus runners | 31 of 31 `recognize`, 29 of 29 presence and 21 of 21 inheritance `validate` | the same |

`tools/quality_gate.py` decides line coverage and mutation score (`check`), and `selftest` runs 21
seeded faults, one per reason (`REPORT_MISSING`, `REPORT_FORMAT`, `MEASUREMENT_EMPTY`,
`BELOW_THRESHOLD`, exactly 90% is red).

**The 19 Go survivors at `36809bab`** (798 of 817 mutants killed; the CI count, reproduced exactly by the six local
shards of `tools/mutation_shard_exec.sh` and by `go-mutesting ./...` before): 7 in `access.go`, `emit.go` and
`recognize.go`, 12 in `parse.go`. All are equivalent mutants (the behaviour cannot differ) except one, which a test
now kills:

* `access.go`, `emit.go`, `recognize.go`: the value returned next to an error that the caller never reads
  (`UNKNOWN_GRAMMAR_VERSION`, two mutants); the numeric value of the `ModeInvalid` sentinel; the early `return nil`
  of a lookup in a node that has no entries; the control-character exclusion of `plainSafe` in `emit.go` (two mutants),
  redundant since `Parse` refuses control characters and tabs and folds line breaks, which the round trip it is
  defined by sees; an operand that is always false for the segments it guards (`s.isIdx`).
* `parse.go`: an end-of-input clamp that returns the same line (`line >= len`); a CRLF look-ahead whose only
  difference is a line start nothing can refer to; the assignment of the zero value (`out.Kind = KindNull`); the "no
  block" sentinel `-1` against `-2` (two mutants); a slice end equal to its start; the `break` against `continue` of a
  loop over two candidates that exclude each other; the look-ahead for `''` at the end of an unterminated string; a
  longer slice for a prefix test whose next condition looks at the character after the marker; the leading flag
  updated inside a quoted scalar, where it is already false (two mutants).
* The one that was not equivalent: the reset of the block-scalar state after the block ends (`_ = block`). A line
  indented deeper than the block scalar was, after it, was still taken for its content. Covered now by the case
  `k:\n  a: |\n    x\n  b:\n    - [? q]\n` in each language and in the corpus.

An earlier run of 33 survivors (at `9bf7d1aa`) had 14 more, which the tests added since kill: the end of a block
scalar, a marker or an explicit key at the end of the text, a `&` followed by a lone CR, a `%` at the start of a line
inside a quoted scalar, and the key anchor behind the scan (`the_edges_of_the_text_scan_give_the_yaml_answer`,
`the_checks_behind_the_prescan_still_refuse`, the corpus). The comment skip of `scan_words` was redundant (a word after
text is never a scalar start) and is gone with its mutants.

**What each property checks** (Rust and Go identically): emit then parse gives back the value for any
value of the subset including any string and key (long ones too); emit is a fixed point under parse;
parse never panics on arbitrary text and its positions lie inside the text on character boundaries;
recognition follows the key set and leaves its input unchanged. For validation: a document built to conform to a random shape has no violation; leaving out one mandatory field gives only `MISSING_MANDATORY` errors, at most one at that path; the default candidates are exactly the absent optional fields that declare a default; for any document against any shape the answer is unsupported or a response of one phase, ordered, with no repeated (path, code), and the input is unchanged. For the access reading: whatever reads is written back to the same spelling, to the type, and no value is a valid mode in both grammars; every mode has one spelling per version and reads back, and the other version's reading refuses it; the version binding agrees with an independently written oracle (missing before unknown before conflict); `is_cert_pattern` agrees with the upstream regular expression for any string (a regex oracle in each language); and reading never changes its input. **Fuzzing** checks on every input
that parse does not panic, that a document that parses survives emit then parse, and that its
positions lie inside the text (`parse`); and that validation of whatever parses against a fixed shape that
uses every rule leaves the document alone and gives an ordered, unique, single-phase response (`validate`);
and that reading an `access`/`modify` value and an `InheritanceMode` under both grammars answers with a known code, writes back the spelling it read, accepts only valid CertPatterns and leaves the document alone (`access`; the Go target also checks `IsCertPattern` against the upstream expression on the raw text); the seeds are the shared vectors and the presence and access inputs.

### Proof that each gate can fail (what was run, and what it did)

| Gate | Seeded fault | Result |
|---|---|---|
| Line coverage | only the subset test runs | Rust 50.55% (595/1177), Go 32.33% (194/600), both `BELOW_THRESHOLD`; reproduced in CI |
| Mutation score | `recognize` is mutated, only tests that do not use it run | Rust 0.00% (0/23), Go 23.53% (16/68), both `BELOW_THRESHOLD`; reproduced in CI |
| Branch coverage | Rust runs only the behaviour tests, Go runs one test | Rust 63.59% (414/651), Go 0.00% (0/730), both `BELOW_THRESHOLD`; reproduced in CI |
| Quality gate script | 21 faults | every reason turns it red (`quality_gate.py selftest`, CI) |
| Property tests | a seeded bug in `emit` (backslash not escaped), by hand, in both languages | all three round-trip properties fail with a minimal counterexample; not reproduced in CI |
| Fuzz job | a committed input starting with `CANARY` fails each of the six targets when `CIC_FUZZ_CANARY` is set | red with the switch, green without; reproduced in CI |
| Seeded defects (`tools/seeded_defects.py`) | 18 deliberate defects in the access reading (7 in Rust, 11 in Go; the Go set adds an unbound `Grammar` read or written as grammar 2, an invalid `Mode` written as `reset`, and a caller-changeable supported-version set): `0` coerced to `false`, a missing version silently defaulting to 1, the pin used without verifying its digest, a string without `=` accepted as a CertPattern, the empty list read as the symbol, grammar 2 accepting the legacy boolean, a violation dropping its position | every defect is caught by the suites (the unmodified copy passes first); reproduced in CI (job `seeded-defects`) |
| Seeded defects for the parser parity (same script) | 15 more, 9 in Rust and 6 in Go: the last non-syntax error reported instead of the first, a block sequence or scalar located at its first item or content line, a word starting with `!` not refused, a tab accepted, a block scalar without content accepted, an empty implicit key read as `~`, the content of a block scalar scanned for markers, an indented marker inside a quoted scalar taken for a document marker, a syntax error in a second document reported as a second document | all 33 defects of the script are caught; reproduced in CI (job `seeded-defects`) |
| Parity gate (`tools/parity/selftest.py`) | 25 breaks of the real outputs: a known divergence answering differently on one side or both, an allow line removed, an agreeing input that now differs, an unknown error code or `NOT_AN_ERROR_TYPE` on one side or both (also identical on both, also for an allowed input), and ten malformed outcome structures; and one that must stay green (a fix) | each turns the gate red with its word (`CHANGED`, `NEW`, `INVALID`), the fix stays green; reproduced in CI (job `parity`) |
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
6. **D-018 compatibility (details in the ADR 0004 and ADR 0002 amendments).** Recognition and the six-key set are
   unchanged; ADR 0004's reading rules hold under grammar 1; the old `upstream_conflict` expectation of
   `inherit.policy-zero-ok-despite-boolean-declaration` no longer applies (removed, id kept). **Gap:** five upstream
   codes (`INHERIT_ENUM_IN_GRAMMAR_1`, `LEGACY_INHERIT_IN_GRAMMAR_2`, `GRAMMAR_VERSION_MISSING`,
   `UNKNOWN_GRAMMAR_VERSION`, `GRAMMAR_VERSION_CONFLICT`) have no registry entry because no vector uses them, and the
   three `INVALID_*` codes are still `provisional`; both are decisions for the ADR owner.
7. Upstream lists `0.0` as `INVALID_INHERIT`. A float spelling is refused earlier by the YAML subset, so from text the
   value never reaches the reading.
8. The corpus runners now need a checkout of `cic-primitives` at the pinned commit (`CIC_PRIMITIVES_ROOT`) to derive the
   grammar version from the verified schema; CI provides it through `.github/actions/upstream-pinned`.
