//! Compiling a pinned CIC schema into the [`Shape`] tree that [`validate`](crate::validate) reads
//! (ADR 0002). The Rust peer of `go/schema.go`; the shared cases are `testdata/p1/schema.json`.
//!
//! The library receives a preprocessed schema, the digest of its exact bytes and the identity of the
//! grammar it conforms to. It never fetches, never resolves a reference and never looks for a newer
//! version. Anything the schema declares that this slice does not process is a structured
//! [`Error::Schema`] refusal that names the schema node; nothing is simplified silently.
//!
//! Read: `spec.<surface>.nodes` with `shape_type` `scalar` (string, integer, boolean), `composite`,
//! `collection` (list, set) and `opaque`, the `mandatory`/`optional` axis, `nullable`, `default`
//! (presence only), `min_items`, the list key rules of ADR 0002 and the case rules of `item_choice`.
//! Refused: `shape_type: choice`, the scalar types `number` and `bytes`, an `item_choice` under a
//! grammar profile without that feature, a structure outside ADR 0002, a reference that is not a
//! pinned `cic:<namespace>:<Name>@vX.Y.Z`. Passed through and not evaluated: `contract` (listed in
//! [`CompiledSchema::unevaluated_contracts`]), `description`, `role`, `capability` and the other
//! descriptive members.

use crate::access::sha256_hex;
use crate::error::{Error, SchemaProblem};
use crate::node::{Node, Value};
use crate::parse::parse;
use crate::validate::{Presence, ScalarType, Shape, ShapeType};

pub const SCHEMA_INVALID: &str = "SCHEMA_INVALID";
pub const SCHEMA_SHAPE_UNSUPPORTED: &str = "SCHEMA_SHAPE_UNSUPPORTED";
pub const SCHEMA_REF_UNPINNED: &str = "SCHEMA_REF_UNPINNED";
pub const SCHEMA_FEATURE_NOT_IN_GRAMMAR: &str = "SCHEMA_FEATURE_NOT_IN_GRAMMAR";
pub const SCHEMA_DIGEST_MISMATCH: &str = "SCHEMA_DIGEST_MISMATCH";
pub const GRAMMAR_PIN_UNKNOWN: &str = "GRAMMAR_PIN_UNKNOWN";
pub const SURFACE_UNSUPPORTED: &str = "SURFACE_UNSUPPORTED";

/// What a pinned grammar defines, as far as the schema reader needs it. Keyed by the pin name of
/// `corpus/pins.yaml` (`<repo>-<commit7>-<manifest8>`), so a re-pin renames it.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Profile {
    /// The grammar defines `item_choice` (cic-primitives PR #9). The primitives release v0.2.0 does
    /// not; the registry's `network-interface.yaml` already uses it (ADR 0002, finding 3).
    pub item_choice: bool,
}

/// devel at 73ae469: the temporary P0 compatibility profile of ADR 0002, not a release contract.
const PROFILES: &[(&str, Profile)] = &[(
    "cic-primitives-73ae469-71ee71ca",
    Profile { item_choice: true },
)];

/// The preprocessed, version-pinned schema the caller supplies.
#[derive(Debug, Clone, Copy)]
pub struct SchemaInput<'a> {
    /// The exact schema file.
    pub bytes: &'a [u8],
    /// The pinned digest of `bytes`, lower-case hex.
    pub sha256: &'a str,
    /// The grammar pin name (`corpus/pins.yaml`).
    pub grammar: &'a str,
    /// `config_surface`, named by the caller and never by the document. `state_surface` is refused: a
    /// missing mandatory field there is a coverage fact, not an authoring error (ADR 0003, rule 8), and an
    /// observation validated with authoring semantics would be stated wrongly.
    pub surface: &'a str,
}

/// A schema read for one surface.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledSchema {
    /// `identity.kind`, `identity.namespace` and `identity.version`.
    pub kind: String,
    pub namespace: String,
    pub version: String,
    /// The verified SHA-256 of the schema bytes, the grammar pin and the surface it was read under.
    pub digest: String,
    pub grammar: String,
    pub surface: String,
    /// `metadata.validatedBy` as written (`"name version"`), kept so a caller can see a schema that
    /// declares an older release than the grammar it is read under.
    pub declared_validated_by: String,
    /// The composite of the surface's nodes, mandatory.
    pub root: Shape,
    /// Whether any list of the surface is an `item_choice`.
    pub uses_item_choice: bool,
    /// The canonical paths of the nodes that declare a `contract`. Contracts are passed through, not
    /// evaluated: validation does not check them.
    pub unevaluated_contracts: Vec<String>,
}

type Problem = SchemaProblem;

fn problem(code: &'static str, path: &str, detail: &str) -> Problem {
    SchemaProblem {
        code,
        path: path.to_string(),
        detail: detail.to_string(),
    }
}

fn get<'a>(n: &'a Node, key: &str) -> Option<&'a Node> {
    match &n.value {
        Value::Map(entries) => entries.iter().find(|e| e.key == key).map(|e| &e.value),
        _ => None,
    }
}

/// A string member, `""` when absent or not a string.
fn str_member<'a>(n: &'a Node, key: &str) -> &'a str {
    match get(n, key).map(|v| &v.value) {
        Some(Value::Str(s)) => s,
        _ => "",
    }
}

fn seq(n: &Node) -> Option<&[Node]> {
    match &n.value {
        Value::Seq(items) => Some(items),
        _ => None,
    }
}

/// `cic:<namespace>:<Name>@vX.Y.Z`: namespace `[a-z0-9_-]+`, name `[A-Za-z0-9_]+`, three decimal parts.
fn is_pinned_ref(s: &str) -> bool {
    let rest = match s.strip_prefix("cic:") {
        Some(r) => r,
        None => return false,
    };
    let (ns, rest) = match rest.split_once(':') {
        Some(p) => p,
        None => return false,
    };
    let (name, version) = match rest.split_once("@v") {
        Some(p) => p,
        None => return false,
    };
    let word = |t: &str, ok: fn(char) -> bool| !t.is_empty() && t.chars().all(ok);
    let parts: Vec<&str> = version.split('.').collect();
    word(ns, |c| {
        c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-'
    }) && word(name, |c| c.is_ascii_alphanumeric() || c == '_')
        && parts.len() == 3
        && parts.iter().all(|p| word(p, |c| c.is_ascii_digit()))
}

struct Compiler {
    profile: Profile,
    item_choice: bool,
    unevaluated: Vec<String>,
    problems: Vec<Problem>,
}

/// Verifies the schema bytes against the pinned digest, binds the grammar identity and reads the
/// surface into a [`Shape`] tree.
///
/// # Errors
/// [`Error::Binding`] (`SCHEMA_DIGEST_MISMATCH`, `GRAMMAR_PIN_UNKNOWN`, `SURFACE_UNSUPPORTED`), the
/// parse errors of the YAML subset, or [`Error::Schema`] (every refusal, in schema order).
pub fn compile_schema(input: &SchemaInput<'_>) -> Result<CompiledSchema, Error> {
    compile_with(input, PROFILES)
}

pub(crate) fn compile_with(
    input: &SchemaInput<'_>,
    profiles: &[(&str, Profile)],
) -> Result<CompiledSchema, Error> {
    if sha256_hex(input.bytes) != input.sha256 {
        return Err(Error::Binding(SCHEMA_DIGEST_MISMATCH));
    }
    let profile = match profiles.iter().find(|(name, _)| *name == input.grammar) {
        Some((_, p)) => *p,
        None => return Err(Error::Binding(GRAMMAR_PIN_UNKNOWN)),
    };
    if input.surface != "config_surface" {
        return Err(Error::Binding(SURFACE_UNSUPPORTED));
    }
    let text = std::str::from_utf8(input.bytes).map_err(|e| Error::Syntax(e.to_string()))?;
    let doc = parse(text)?;
    let spec = match get(&doc, "spec") {
        Some(s) if matches!(s.value, Value::Map(_)) => s,
        _ => {
            return Err(Error::Schema(vec![problem(
                SCHEMA_INVALID,
                "spec",
                "a mapping is required",
            )]))
        }
    };
    let mut c = Compiler {
        profile,
        item_choice: false,
        unevaluated: Vec::new(),
        problems: Vec::new(),
    };
    c.refs(spec, input.surface)
        .map_err(|p| Error::Schema(vec![p]))?;
    let (mut kind, mut namespace, mut version) = (String::new(), String::new(), String::new());
    if let Some(id) = get(spec, "identity") {
        kind = str_member(id, "kind").to_string();
        namespace = str_member(id, "namespace").to_string();
        version = str_member(id, "version").to_string();
    }
    let mut declared_validated_by = String::new();
    if let Some(vb) = get(&doc, "metadata").and_then(|m| get(m, "validatedBy")) {
        declared_validated_by = format!("{} {}", str_member(vb, "name"), str_member(vb, "version"));
    }
    let surface = match get(spec, input.surface) {
        Some(s) if matches!(s.value, Value::Map(_)) => s,
        _ => {
            return Err(Error::Schema(vec![problem(
                SCHEMA_INVALID,
                input.surface,
                "the surface is not declared",
            )]))
        }
    };
    let nodes = match get(surface, "nodes").and_then(seq) {
        Some(n) => n,
        None => {
            return Err(Error::Schema(vec![problem(
                SCHEMA_INVALID,
                &format!("{}.nodes", input.surface),
                "a list is required",
            )]))
        }
    };
    let fields = c.fields(nodes, "$");
    if !c.problems.is_empty() {
        return Err(Error::Schema(c.problems));
    }
    Ok(CompiledSchema {
        kind,
        namespace,
        version,
        digest: input.sha256.to_string(),
        grammar: input.grammar.to_string(),
        surface: input.surface.to_string(),
        declared_validated_by,
        root: Shape {
            shape_type: ShapeType::Composite(fields),
            presence: Presence::Mandatory,
            nullable: false,
            has_default: false,
        },
        uses_item_choice: c.item_choice,
        unevaluated_contracts: c.unevaluated,
    })
}

fn check_ref(path: &str, v: Option<&Node>) -> Result<(), Problem> {
    match v {
        None => Ok(()),
        Some(n) => match &n.value {
            Value::Str(s) if is_pinned_ref(s) => Ok(()),
            _ => Err(problem(
                SCHEMA_REF_UNPINNED,
                path,
                "a reference must be cic:<namespace>:<Name>@vX.Y.Z",
            )),
        },
    }
}

fn bool_member(n: &Node, key: &str, path: &str) -> Result<Option<bool>, Problem> {
    match get(n, key) {
        None => Ok(None),
        Some(m) => match m.value {
            Value::Bool(b) => Ok(Some(b)),
            _ => Err(problem(
                SCHEMA_INVALID,
                &format!("{path}.{key}"),
                "a boolean is required",
            )),
        },
    }
}

fn int_member(n: &Node, key: &str, path: &str) -> Result<Option<usize>, Problem> {
    match get(n, key) {
        None => Ok(None),
        Some(m) => match m.value {
            Value::Int(i) if (0..=1 << 31).contains(&i) => Ok(Some(i as usize)),
            _ => Err(problem(
                SCHEMA_INVALID,
                &format!("{path}.{key}"),
                "a non-negative integer is required",
            )),
        },
    }
}

fn forbid(n: &Node, path: &str, keys: &[&str]) -> Result<(), Problem> {
    for k in keys {
        if get(n, k).is_some() {
            return Err(problem(
                SCHEMA_INVALID,
                path,
                &format!("{k} is not allowed here"),
            ));
        }
    }
    Ok(())
}

/// The names of the `item_fields` entries that carry `role: key`, in declaration order.
fn key_fields(list: &[Node]) -> Vec<String> {
    list.iter()
        .filter(|f| match get(f, "role").map(|r| &r.value) {
            Some(Value::Str(r)) => r == "key",
            _ => false,
        })
        .map(|f| str_member(f, "name").to_string())
        .collect()
}

/// A list of strings; `None` when `n` is not a list of strings.
fn names(n: &Node) -> Option<Vec<String>> {
    seq(n)?
        .iter()
        .map(|it| match &it.value {
            Value::Str(s) => Some(s.clone()),
            _ => None,
        })
        .collect()
}

fn same_set(a: &[String], b: &[String]) -> bool {
    let (mut x, mut y) = (a.to_vec(), b.to_vec());
    x.sort();
    y.sort();
    x == y && x.windows(2).all(|w| w[0] != w[1])
}

fn scalar_type(n: &Node, key: &str, path: &str) -> Result<ScalarType, Problem> {
    let at = format!("{path}.{key}");
    match get(n, key).map(|v| &v.value) {
        Some(Value::Str(s)) => match s.as_str() {
            "string" => Ok(ScalarType::String),
            "integer" => Ok(ScalarType::Integer),
            "boolean" => Ok(ScalarType::Boolean),
            "number" | "bytes" => Err(problem(
                SCHEMA_SHAPE_UNSUPPORTED,
                &at,
                &format!("scalar type {s:?} is not processed by this slice"),
            )),
            _ => Err(problem(
                SCHEMA_INVALID,
                &at,
                &format!("unknown scalar type {s:?}"),
            )),
        },
        _ => Err(problem(SCHEMA_INVALID, &at, "a scalar type is required")),
    }
}

impl Compiler {
    /// An unpinned reference of the parts the contract reads: the base, the identity and the selected
    /// surface.
    fn refs(&self, spec: &Node, surface: &str) -> Result<(), Problem> {
        if let Some(base) = get(spec, "base") {
            check_ref("spec.base.ref", get(base, "ref"))?;
        }
        if let Some(id) = get(spec, "identity") {
            for k in ["atomic_ref", "base"] {
                check_ref(&format!("spec.identity.{k}"), get(id, k))?;
            }
        }
        if let Some(s) = get(spec, surface) {
            check_ref(
                &format!("spec.{surface}.aggregate_ref"),
                get(s, "aggregate_ref"),
            )?;
            if let Some(nodes) = get(s, "nodes").and_then(seq) {
                for (i, n) in nodes.iter().enumerate() {
                    check_ref(
                        &format!("spec.{surface}.nodes[{i}].atomic_ref"),
                        get(n, "atomic_ref"),
                    )?;
                }
            }
        }
        Ok(())
    }

    /// The fields of a list of nodes; the first refusal of each node is recorded and the walk goes on
    /// to its siblings.
    fn fields(&mut self, list: &[Node], path: &str) -> Vec<(String, Shape)> {
        let mut out = Vec::new();
        let mut seen: Vec<&str> = Vec::new();
        for (i, n) in list.iter().enumerate() {
            let name = str_member(n, "name");
            if !matches!(n.value, Value::Map(_)) || name.is_empty() {
                self.problems.push(problem(
                    SCHEMA_INVALID,
                    &format!("{path}[{i}]"),
                    "a node must be a mapping with a non-empty string name",
                ));
                continue;
            }
            if seen.contains(&name) {
                self.problems.push(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.{name}"),
                    "duplicate field name",
                ));
                continue;
            }
            seen.push(name);
            match self.node(n, &format!("{path}.{name}")) {
                Ok(shape) => out.push((name.to_string(), shape)),
                Err(p) => self.problems.push(p),
            }
        }
        out
    }

    fn presence(n: &Node, path: &str) -> Result<Presence, Problem> {
        let mand = bool_member(n, "mandatory", path)?;
        let opt = bool_member(n, "optional", path)?;
        // shape.yaml: only the value true is valid for either member.
        if mand == Some(false) || opt == Some(false) {
            return Err(problem(
                SCHEMA_INVALID,
                path,
                "mandatory and optional take only the value true",
            ));
        }
        if mand.is_some() && opt.is_some() {
            return Err(problem(
                SCHEMA_INVALID,
                path,
                "mandatory and optional contradict each other",
            ));
        }
        // Neither written means mandatory (shape.yaml).
        Ok(if opt.is_some() {
            Presence::Optional
        } else {
            Presence::Mandatory
        })
    }

    fn node(&mut self, n: &Node, path: &str) -> Result<Shape, Problem> {
        let presence = Self::presence(n, path)?;
        let nullable = bool_member(n, "nullable", path)?.unwrap_or(false);
        let mut has_default = false;
        if let Some(d) = get(n, "default") {
            if presence == Presence::Mandatory {
                return Err(problem(
                    SCHEMA_INVALID,
                    path,
                    "a default is not allowed on a mandatory field (the field does not say optional)",
                ));
            }
            if matches!(d.value, Value::Null) {
                return Err(problem(SCHEMA_INVALID, path, "default: null is invalid"));
            }
            has_default = true;
        }
        if get(n, "contract").is_some() {
            self.unevaluated.push(path.to_string());
        }
        let shape_type = match get(n, "shape_type").map(|v| &v.value) {
            Some(Value::Str(s)) => s.as_str(),
            _ => {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.shape_type"),
                    "shape_type is required",
                ))
            }
        };
        let kind = match shape_type {
            "scalar" => {
                forbid(n, path, &["fields", "item_fields", "item_choice", "cases"])?;
                ShapeType::Scalar(scalar_type(n, "scalar_type", path)?)
            }
            "opaque" => {
                forbid(
                    n,
                    path,
                    &[
                        "scalar_type",
                        "fields",
                        "item_fields",
                        "item_choice",
                        "cases",
                        "collection_variant",
                    ],
                )?;
                ShapeType::Opaque
            }
            "composite" => {
                forbid(
                    n,
                    path,
                    &["scalar_type", "item_fields", "item_choice", "cases"],
                )?;
                match get(n, "fields").and_then(seq) {
                    Some(list) if !list.is_empty() => ShapeType::Composite(self.fields(list, path)),
                    _ => {
                        return Err(problem(
                            SCHEMA_INVALID,
                            &format!("{path}.fields"),
                            "a composite needs at least one field",
                        ))
                    }
                }
            }
            "collection" => self.collection(n, path)?,
            "choice" => {
                return Err(problem(
                    SCHEMA_SHAPE_UNSUPPORTED,
                    path,
                    "shape_type choice is not processed by this slice",
                ))
            }
            other => {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.shape_type"),
                    &format!("unknown shape_type {other:?}"),
                ))
            }
        };
        Ok(Shape {
            shape_type: kind,
            presence,
            nullable,
            has_default,
        })
    }

    fn collection(&mut self, n: &Node, path: &str) -> Result<ShapeType, Problem> {
        forbid(n, path, &["scalar_type", "fields", "cases"])?;
        let min_items = int_member(n, "min_items", path)?;
        int_member(n, "max_items", path)?;
        let variant = match get(n, "collection_variant").map(|v| &v.value) {
            Some(Value::Str(s)) if s == "list" || s == "set" => s.as_str(),
            _ => {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.collection_variant"),
                    "list or set is required",
                ))
            }
        };
        if variant == "set" {
            forbid(n, path, &["item_fields", "item_choice", "item_key"])?;
            let item = scalar_type(n, "item_scalar_type", path)?;
            return Ok(ShapeType::Set { item, min_items });
        }
        forbid(n, path, &["item_scalar_type"])?;
        match (get(n, "item_fields"), get(n, "item_choice")) {
            (Some(_), Some(_)) => Err(problem(
                SCHEMA_INVALID,
                path,
                "item_fields and item_choice are exclusive",
            )),
            (Some(fields), None) => {
                self.item_fields(n, fields, path)?;
                Ok(ShapeType::DeclaredList { min_items })
            }
            (None, Some(choice)) => {
                self.item_choice_cases(choice, path)?;
                Ok(ShapeType::DeclaredList { min_items })
            }
            (None, None) => Err(problem(
                SCHEMA_INVALID,
                path,
                "a list needs item_fields or item_choice",
            )),
        }
    }

    fn item_fields(&mut self, owner: &Node, list: &Node, path: &str) -> Result<(), Problem> {
        let items = match seq(list) {
            Some(i) if !i.is_empty() => i,
            _ => {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.item_fields"),
                    "at least one element field is required",
                ))
            }
        };
        self.fields(items, &format!("{path}[]"));
        let keys = key_fields(items);
        let item_key = get(owner, "item_key");
        if keys.is_empty() {
            return Err(problem(
                SCHEMA_INVALID,
                path,
                "a list needs a key: one item_fields entry with role: key",
            ));
        }
        match item_key {
            None if keys.len() == 1 => Ok(()),
            None => Err(problem(
                SCHEMA_INVALID,
                path,
                "several key fields need item_key",
            )),
            Some(k) => match names(k) {
                Some(n) if same_set(&n, &keys) => Ok(()),
                _ => Err(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.item_key"),
                    "item_key must name exactly the role: key fields",
                )),
            },
        }
    }

    fn item_choice_cases(&mut self, choice: &Node, path: &str) -> Result<(), Problem> {
        if !self.profile.item_choice {
            return Err(problem(
                SCHEMA_FEATURE_NOT_IN_GRAMMAR,
                &format!("{path}.item_choice"),
                "the pinned grammar does not define item_choice",
            ));
        }
        self.item_choice = true;
        let cases = match (&choice.value, get(choice, "cases").and_then(seq)) {
            (Value::Map(_), Some(c)) if c.len() >= 2 => c,
            _ => {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{path}.item_choice"),
                    "cases (at least two) are required",
                ))
            }
        };
        let mut seen_names: Vec<&str> = Vec::new();
        let mut seen_keys: Vec<Vec<String>> = Vec::new();
        for (i, cs) in cases.iter().enumerate() {
            let name = str_member(cs, "name");
            let cp = format!("{path}.item_choice.cases[{i}]");
            if name.is_empty() {
                return Err(problem(SCHEMA_INVALID, &cp, "a case needs a name"));
            }
            if seen_names.contains(&name) {
                return Err(problem(SCHEMA_INVALID, &cp, "duplicate case name"));
            }
            seen_names.push(name);
            let fields = match get(cs, "fields").and_then(seq) {
                Some(f) if !f.is_empty() => f,
                _ => {
                    return Err(problem(
                        SCHEMA_INVALID,
                        &format!("{cp}.fields"),
                        "at least one field is required",
                    ))
                }
            };
            self.fields(fields, &cp);
            let key = get(cs, "key").and_then(names).unwrap_or_default();
            if key.is_empty() || !same_set(&key, &key_fields(fields)) {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{cp}.key"),
                    "key must name exactly the role: key fields of the case",
                ));
            }
            if seen_keys.iter().any(|k| same_set(k, &key)) {
                return Err(problem(
                    SCHEMA_INVALID,
                    &format!("{cp}.key"),
                    "two cases resolve to the same key field set (C17)",
                ));
            }
            seen_keys.push(key);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A grammar profile without `item_choice` refuses a schema that uses it, instead of reading it
    /// (ADR 0002, finding 3). The only pin the library knows defines it, so the refusal is tested with a
    /// profile table of its own; the primitives release v0.2.0 is not a known pin (it would be
    /// `GRAMMAR_PIN_UNKNOWN`).
    #[test]
    fn item_choice_needs_the_grammar_to_define_it() {
        let schema = b"spec:\n  config_surface:\n    nodes:\n      - name: a\n        shape_type: collection\n        collection_variant: list\n        item_choice:\n          cases: []\n";
        let profiles = [
            ("old", Profile { item_choice: false }),
            ("new", Profile { item_choice: true }),
        ];
        let sha = sha256_hex(schema);
        let input = |grammar| SchemaInput {
            bytes: schema,
            sha256: &sha,
            grammar,
            surface: "config_surface",
        };
        match compile_with(&input("old"), &profiles) {
            Err(Error::Schema(p)) => {
                assert_eq!(p[0].code, SCHEMA_FEATURE_NOT_IN_GRAMMAR);
                assert_eq!(p[0].path, "$.a.item_choice");
            }
            other => panic!("got {other:?}"),
        }
        match compile_with(&input("new"), &profiles) {
            Err(Error::Schema(p)) => assert_eq!(p[0].code, SCHEMA_INVALID),
            other => panic!("the structure is checked instead: {other:?}"),
        }
    }

    #[test]
    fn a_pinned_reference_is_exactly_the_documented_form() {
        for ok in ["cic:core:Shape@v0.2.0", "cic:a-b_1:X_y@v10.0.123"] {
            assert!(is_pinned_ref(ok), "{ok}");
        }
        for bad in [
            "",
            "schemas/atomic/shape.yaml",
            "cic:core:Shape",
            "cic:core:Shape@v0.2",
            "cic:core:Shape@v0.2.0.1",
            "cic:core:Shape@v0.2.x",
            "cic:core:Shape@v0..0",
            "cic:Core:Shape@v0.2.0",
            "cic::Shape@v0.2.0",
            "cic:core:@v0.2.0",
            "cic:core:Sh-ape@v0.2.0",
            "cic:core:Shape@0.2.0",
            "xcic:core:Shape@v0.2.0",
        ] {
            assert!(!is_pinned_ref(bad), "{bad}");
        }
    }
}
