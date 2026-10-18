# ADR 0005: Shared conformance corpus contract (draft)

Status: **proposed.** Closes the "corpus format" and "runnable gate" items of
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
defaults: {adr: ADR-0004, grammar: cic-primitives-devel}
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
| `recognize` | `input` or `input_yaml` (raw text, for parse-phase cases) | `class`, `classes`, `validate`, `opaque_value`, `phase`, `code` |
| `validate` | `input` or `absent: true` | `valid`, `error`, `stays_absent`, `default_candidate`, `path`, `kind`, `is_inherit`, `preserved`, `upstream_conflict` |
| `roundtrip` | `input` or `absent: true` | `expanded`, `collapsed`, `fields` (typed values) |
| `collapse` | `input`, boolean `authored_expanded` | `collapse` (required), `fields` |
| `expand` | `input` | `fields` (required) |
| `equality` | `a`, `b` | `equal` (required) |

### Schema reference

`schema: {synthetic: {...}}` is an inline fixture notation, never a schema
definition: `position` (`scalar | opaque | {structured: [..]} | {list_of: ..}`),
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

A case is `negative` exactly when its expectation carries an error code (an
`error` or `code` key at any depth), `positive` otherwise; the gate cross-checks.
Codes must be listed in `corpus/codes.yaml`. **All codes are `provisional`** until
the error-contract ADR (issue #3 point 6) closes. A case that uses a code carries
`codes_status: provisional`, so the provisional state is machine readable and a
case becomes stable only when its codes do. A registry code no case uses is an
error (`CODE_UNUSED`), so renames cannot leave stale entries.

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

Current state: 3 suites, 83 cases, 22 rules, 27 faults.

## Migration of the existing vectors

The 83 vectors of ADR 0001, 0003 and 0004 were converted mechanically; inputs and
meanings are unchanged. Changes that are not mechanical:

- ids gain the `<suite>.` prefix; three ids lost an uppercase letter; inherit ids
  gain `key-`, `policy-` or `access-` where they would collide;
- `position: any` is spelled `opaque` (ADR 0002);
- presence round-trip expectations `{present, value}` became typed values;
- the two inherit expectations that carried an untyped `inherit_after` /
  `access_after` plus a separate type field became typed `fields`;
- `expanded-without-value-key-is-not-expanded` is a `recognize` case and
  `value-null-and-default-injection-are-separate` a `collapse` case, which is
  what they always tested.

## Not decided here

- The path grammar and the diff format. No case carries a path expression beyond
  the existing `path: a` hint of one validate case; the `path` key is part of the
  provisional error contract and will be revisited with it.
- Error codes, their names and source positions (P0 point 6).
- The runnable Rust and Go runners (P1). The format is designed so that both read
  the same files and may add kinds, never reinterpret existing ones.
- Branch-coverage measurement (P0 point 10).

## Consequences

- The checker needs Python and PyYAML (pinned in `tools/requirements.txt`). It is
  a P0 structural tool, not part of the library, and is expected to stay
  language-independent of the implementations.
- Adding a kind or an expectation key is an ADR-level change, because the key sets
  are closed.
