# ADR 0005: Shared conformance corpus contract

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end). Closes the "corpus format" and "runnable gate" items of
issue #3 for the *structure* of the corpus. It decides nothing about error codes,
the path grammar or the diff format, and it implements no library semantics.

## Why now

Four ADRs have vectors, each in its own provisional shape. Every later P0 decision
(error contract, paths, diff) will add vectors; they need one format and one gate
before they arrive, not after.

## The boundary this ADR keeps

`tools/corpus_check.py` checks the **shape** of fixtures: envelope, required
fields, unique ids, resolvable references, typed expectations. It contains no
recognition, validation, collapse or expand logic and must not grow any. It is not
a pseudo Rust/Go implementation, and its success means one thing only: the corpus
is well formed.

It prints `implementation conformance NOT RUN (rust: none, go: none)` on every
green run, so a structural pass cannot be read as an implementation pass.
Implementation conformance starts to exist when the P1 runner exists, one per
language, reading the same files.

## File layout

```
corpus/pins.yaml              pinned external sources (grammar, real schemas)
corpus/codes.yaml             error code registry with a status per code
corpus/<suite>/vectors.yaml   one suite per directory
tools/corpus_check.py         the structural gate and its self-test
tools/corpus_faults.yaml      seeded faults, one or more per checker rule
```

Any other YAML file under `corpus/` is an error (`UNEXPECTED_FILE`).

## Suite file

```yaml
corpus_format: 1
suite: inherit                 # equals the directory name
status: provisional            # provisional | stable
defaults: {adr: ADR-0004, grammar: cic-primitives-73ae469-6a188078}
cases: [ ... ]
```

The envelope keys are closed. `defaults` may only hold `adr` and `grammar`; a case
may override them.

## Case

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `<suite>.<slug>`, lowercase, unique across the whole corpus. Ids are stable: a rename is a new case. |
| `kind` | yes | closed set below |
| `polarity` | yes | `positive` or `negative`; must agree with the expectation (below) |
| `adr` | via defaults | `ADR-NNNN`, resolved to a file in `docs/adr/` |
| `grammar` | via defaults | key in `pins.yaml` `grammar` |
| `schema` | yes | exactly one of `synthetic` or `pin` |
| input fields | by kind | see below |
| `expect` | yes | closed key set per kind |
| `codes_status` | when codes are used | must equal the registry status of the codes used |
| `description` | no | prose |

### Kinds

| Kind | Input fields | Expectation keys |
|---|---|---|
| `recognize` | `input` or `input_yaml` (raw source text) | `class`, `elements` (non-empty list, one `{field: class}` map per input element, fields equal to the record's), `errors`, `opaque_value` |
| `validate` | exactly one of `input`, `absent: true`, `input_yaml` | `valid`, `errors`, `stays_absent`, `default_candidate`, `kind`, `is_inherit`, `preserved`, `upstream_conflict` |
| `roundtrip` | `input` or `absent: true` | `expanded`, `collapsed`, `fields` (typed values) |
| `collapse` | `input`, boolean `authored_expanded` | `collapse` (required), `fields` |
| `expand` | `input` | `fields` (required) |
| `equality` | `a`, `b` | `equal` (required) |
| `path_parse` | `path` (text) | `segments` or `errors` |
| `path_format` | `segments` | `text` or `errors` |
| `path_resolve` | `path` | `target` and `primitive_position`, or `errors` |
| `path_of` | `document`, `at` | `path` or `errors` |
| `path_stability` | `variants` (at least two `{document or document_yaml, at}`) | `path` or `errors` |
| `diff` | `a` or `a_yaml`, `b` or `b_yaml`, optional `scope`, optional `schema_b` | `changes`, `representation`, `canonical`, or `errors` |
| `diff_stability` | `variants` (at least two `{a|a_yaml, b|b_yaml}`), optional `scope`, optional `schema_b` | same as `diff` |

### Schema reference

`schema: {synthetic: {...}}` is an inline fixture notation, never a schema
definition: `position` (`scalar | opaque | {structured: [..]} | {list_of: {record: {field: position}}}`; a list element is a record, not a position, ADR 0002),
`shape` (the ADR 0003 shorthand) or `slot` (`inherit_key | inherit_policy |
access_modify`, the atom slot under test). `schema: {pin: <id>}` points at a real
schema pinned in `pins.yaml`. Pins carry repo, commit, path and sha256 (or repo,
ref and commit for the grammar); nothing is vendored (ADR 0002 licensing).

### Typed expectations

An input is the native YAML value, so `null`, `false`, `0`, `""`, `[]` and `{}`
keep their YAML type. Absence is never a value: it is `absent: true` instead of
`input`, and exactly one of the two must be present, so a missing `input` cannot
be mistaken for `input: null`.

Every *expected data value* is a typed value:

```yaml
{type: absent}                  # no key, no value
{type: null, value: null}
{type: boolean, value: false}
{type: integer, value: 0}
{type: list, value: []}
{type: map, value: {}}
```

`type` is one of `absent null boolean integer float string list map`. The gate
compares the declared type with the actual YAML type strictly (a boolean is not an
integer, `0` is not `false`) and rejects `{type: absent, value: ...}`. The YAML
loader is strict as well: duplicate keys, non-string keys and unquoted YAML 1.1
spellings (`yes`, `on`, `0755`, `1_000`, sexagesimal, dates) are errors, so a
fixture reads the same in every YAML implementation.

### Polarity and codes

A case is `negative` exactly when its expectation carries `errors`, `positive`
otherwise; the gate cross-checks. Codes are read only from `expect.errors[*].code`.
Typed values are data and are never searched, so a map with `code` or `error` keys is
not a code. The error object (code, phase, source location, logical path, path
offset) and the registry (`corpus/codes.yaml`: status, phases with anchors, meaning,
trigger) are defined by ADR 0007, which also moved `corpus_format` to 2. A case that
uses a code carries `codes_status` equal to the registry status, so the provisional
state stays machine readable and a case is stable only when its codes are. A
registry code no case uses (`CODE_UNUSED`), or a registry phase no case exercises
(`PHASE_UNUSED`), is an error, so renames cannot leave stale entries.

## Amendment for ADR 0006 (path kinds)

The five `path_*` kinds above are added. Their rules:

- The envelope may carry `trees`, a map of named synthetic schema trees; a case
  refers to one with `schema: {synthetic: {tree: <name>}}` or inlines a tree. A tree
  is a fixture notation, not a schema: nodes are `{scalar: T}`, `{set: T}`,
  `{composite: {..}}`, `{list: {key, fields}}`, `{list: {choice: [{name, key,
  fields}]}}` or `opaque`, `T` one of `string integer boolean number bytes`. The
  gate checks that list keys name distinct scalar fields and that choice cases differ
  in name and in key set. Lexical kinds (`path_parse`, `path_format`) take
  `synthetic: {lexical: true}`.
- Segments are `{field: name}` and `{item: [{name, key: <typed value>}]}`; an item
  directly follows a field. A parsed key is a string or an integer, and a parsed
  name is non-empty without control characters. A `path_format` input may hold
  anything, because the formatter has to reject what it cannot write.
- `at` is a list of field names (strings) and physical list indices (integers) into
  the document; Access wrappers are looked through by the runner.
- A `path_stability` case lists variants of one logical object and fails the gate if
  two variants have identical documents (compared type-strictly), because such a case
  would prove nothing. A variant that must differ only in YAML mapping order holds
  raw `document_yaml` text instead of a parsed `document`, since a parsed mapping has
  no order to vary.
- An expectation holds exactly one of its success key and `errors`.

## Amendment for ADR 0008 (diff kinds)

`diff` and `diff_stability` use the `trees` of the suite (`schema: {synthetic: {tree:
name}}`). An operand is a parsed document or raw YAML text; `scope` is a list of
canonical logical paths; `schema_b`, if present, must differ from `schema` (a context
mismatch case). Variants of a stability case must differ in their operands. The
expectation is `changes`, `representation` and `canonical` together, or `errors`;
events, their order and the canonical bytes are checked by the rules of ADR 0008. An
error about one view carries `operand`.

## Amendment for ADR 0012 (the edit kinds)

Two kinds are added (the cases of P3b, `docs/P3B-SOURCE-PRESERVING-EDIT.md`). Both take the raw source text, which may hold a byte order mark, a lone CR or CRLF line breaks, or non-ASCII text: exactly one of `input_yaml` (text) and `input_hex` (the bytes, hex-encoded; for what a text field cannot spell, a lone CR, a BOM, invalid UTF-8).

| Kind | Input fields | Expectation keys |
|---|---|---|
| `source_span` | `input_yaml` or `input_hex`, `schema`, `path` (canonical ADR 0006 text) | exactly one of `span` (`{start, end, style}`: byte offsets, half-open, of the value scalar of the position, and `plain`, `single_quoted` or `double_quoted`) and `errors` |
| `edit` | `input_yaml` or `input_hex`, `schema`, `path`, `value` (`{string: s}`, `{integer: n}`, `{boolean: b}` or `{null: true}`) | exactly one of `output_yaml` or `output_hex` (the exact bytes of the edited text) and `errors` (one error, phase `edit` or the earlier phase that refused) |

Outputs and spans are compared as **bytes** and offsets; an `edit` case also carries the `span_before` and `span_after` it expects. An error carries the code, the phase and the source position of its anchor (ADR 0007). A case with a text field that
contains only what a text can spell may use `output_yaml`; the gate rejects a case whose expected bytes it cannot reproduce from the given field. The accounting stays exact.

## The gate and its proof

```
make corpus        # = python3 tools/corpus_check.py
```

One command runs the structural check on every suite and then the self-test. The
self-test copies the corpus, applies each fault of `tools/corpus_faults.yaml`
(an exact text replacement or an added file) and requires the named rule to turn
red. It also requires the unmodified corpus to be clean, each replacement to match
exactly once (so a stale fault cannot pass silently), and **every rule of the
checker to have at least one fault**. A new rule without a fault fails the gate.

The self-test also asserts loader invariants first: an empty scalar is `null`
(equal to `null`), `''` stays a string, `0` is an integer, `false` a boolean.

`class` and the `elements` classes are limited to `short`, `expanded`, `error`.

Current state: see `make corpus` (the figures change with every suite).

## Migration of the existing vectors

The 83 vectors of ADR 0001, 0003 and 0004 were converted mechanically. Changes that
are not mechanical:

- `list-elements-recognized-individually` treated whole list elements as
  primitives, which ADR 0002 superseded. It is replaced by
  `list-record-fields-recognized-individually` and
  `list-record-is-not-recognized-as-a-whole`: the record's fields are recognized,
  the record is not;

- ids gain the `<suite>.` prefix; three ids lost an uppercase letter; inherit ids
  gain `key-`, `policy-` or `access-` where they would collide;
- `position: any` is spelled `opaque` (ADR 0002);
- presence round-trip expectations `{present, value}` became typed values;
- the two inherit expectations that carried an untyped `inherit_after` /
  `access_after` plus a separate type field became typed `fields`;
- `expanded-without-value-key-is-not-expanded` is a `recognize` case and
  `value-null-and-default-injection-are-separate` a `collapse` case, which is
  what they always tested.

## Amendment for the grammar identity (ADR 0002)

- **Grammar pin format.** A pin in `corpus/pins.yaml` has `repo`, `ref`, `commit`, `files`
  (the accepted seven paths, each with the SHA-256 of its raw Git blob bytes, sorted by
  the UTF-8 bytes of the path) and `manifest_sha256`. The pin name is
  `<repo>-<commit7>-<manifest8>`: the identity is in the name, so a re-pin cannot keep
  its name. Structural faults are `PIN_INVALID`; a `manifest_sha256` that does not
  match the recomputed digest is `PIN_DIGEST`. Both are offline and need no network.
- **Kind `grammar_manifest`.** Input `files` (path, sha256) and `declared` (a digest),
  schema `{synthetic: {lexical: true}}` (no schema); expectation `valid: true` or the
  error `GRAMMAR_DIGEST_MISMATCH`. The gate recomputes the digest of every case
  (`MANIFEST_CONSISTENCY`, `MANIFEST_FIXTURE`), so a fixture cannot declare a wrong
  outcome.
- **The workflow is part of the contract.** `UPSTREAM_VERIFY` fails `make corpus` unless
  the `corpus` workflow reads the pinned commit with `grammar_identity.py commit`, checks
  out `CentralInfraCore/cic-primitives` at the commit from that step and runs
  `grammar_identity.py verify` afterwards. The three steps are compared **whole** (the
  exact command, the exact `with` fields, no extra keys, in that order), never by a
  fragment of their text, so a command that only mentions the verifier is not the
  verifier; the job and the workflow set no `env`, `defaults`, `if` or
  `continue-on-error`, and the workflow runs on `pull_request` and `push`.
- The reproduction from upstream bytes is `tools/grammar_identity.py verify` (CI, and
  `make grammar-verify UPSTREAM=...` locally); its self-test (`make grammar-selftest`,
  a throwaway Git repository with seeded faults for every reason) needs no network.

## Not decided here

- Nothing about the grammar identity is pending in this ADR: it is decided in ADR 0002
  and implemented as described in the amendment above.
- A case against the real pinned schema. The `pin` reference path is exercised by a
  seeded fault only; no semantic case uses it before the schema-driven runner exists.
- The diff format, the path grammar and the error contract: decided in ADR 0008, ADR
  0006 and ADR 0007; this ADR only carries their corpus amendments.
- The runnable Rust and Go runners (P1). The format is designed so that both read
  the same files and may add kinds, never reinterpret existing ones.
- Branch-coverage measurement (P0 point 10).

## Consequences

- The checker needs Python and PyYAML (pinned in `tools/requirements.txt`). It is
  a P0 structural tool, not part of the library, and is expected to stay
  language-independent of the implementations.
- Adding a kind or an expectation key is an ADR-level change, because the key sets
  are closed.

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

Conditions and limits:

- **Follow-up (implemented in a separate change, awaiting the owner's review).** `pins.yaml` carries the grammar identity decided in ADR 0002 (the seven-file manifest and its digest), with structural rules, seeded faults and a mandatory CI reproduction from upstream; see the amendment for the grammar identity.
- The gate checks structure only and prints that implementation conformance was not run. Its Python tooling is a P0 structural tool, independent of the implementations.
- The ADR carries the amendments of ADR 0006 to 0010 in one document; the format version is 2.
