//! The text of logical paths (ADR 0006): strict parse, canonical format and resolution against a schema. The Rust peer of
//! `go/pathtext.go`; the corpus kinds `path_parse`, `path_format` and `path_resolve` run in both languages.
//!
//! [`parse_path`] accepts only the canonical writing, so `format_path(parse_path(t)) == t` for every accepted `t`: a text
//! with a valid reading that is not canonical (a leading zero, `+7`, `-0`, a needless `\u` escape, `\/`) is refused, not
//! rewritten, because two spellings of one path would defeat byte-identical diffs. Offsets are in code points. The three
//! operations report a [`PathError`]: a code of the registry, its phase, the offset (`path_parse` only) and, for
//! `path_resolve`, the logical path up to and including the segment at fault. They have no source location.

use crate::error::{Location, Phase, Violation};
use crate::listitem::{Case, KeyComp, KeyValue};
use crate::recognize::{write_name, write_selector};
use crate::validate::{ScalarType, Shape, ShapeType};

/// A segment of a path: a field, or a ListItem (the key components of one list element).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathSeg {
    Field(String),
    Item(Vec<KeyComp>),
}

/// The failure of a path operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathError {
    pub code: &'static str,
    pub phase: Phase,
    pub offset: usize,
    pub logical_path: Option<String>,
}

impl PathError {
    /// The machine-response form: no source location applies.
    #[must_use]
    pub fn violation(&self) -> Violation {
        Violation {
            code: self.code,
            phase: self.phase,
            location: Location::NotApplicable,
            logical_path: self.logical_path.clone(),
            path_offset: (self.phase == Phase::PathParse).then_some(self.offset),
            operand: None,
        }
    }
}

const ESCAPABLE: &str = "\\.[]{}=,";

fn fail(code: &'static str, at: usize) -> PathError {
    PathError {
        code,
        phase: Phase::PathParse,
        offset: at,
        logical_path: None,
    }
}

fn is_digit(c: char) -> bool {
    c.is_ascii_digit()
}

/// Reads the canonical text of a path into segments.
///
/// # Errors
/// A [`PathError`] of phase `path_parse` with the offset in code points.
pub fn parse_path(text: &str) -> Result<Vec<PathSeg>, PathError> {
    let r: Vec<char> = text.chars().collect();
    if r.first() != Some(&'$') {
        return Err(fail("PATH_SYNTAX", 0));
    }
    let mut segs = Vec::new();
    let mut after_name = false; // a selector may only directly follow a field name
    let mut i = 1;
    // every round takes at least one character, so the text length bounds the rounds (and what they build)
    for _ in 0..r.len() {
        if i >= r.len() {
            break;
        }
        match r[i] {
            '.' => {
                let (name, next) = name(&r, i + 1, false)?;
                segs.push(PathSeg::Field(name));
                i = next;
                after_name = true;
            }
            '[' if after_name => {
                let (item, next) = selector(&r, i)?;
                segs.push(PathSeg::Item(item));
                i = next;
                after_name = false;
            }
            _ => return Err(fail("PATH_SYNTAX", i)),
        }
    }
    Ok(segs)
}

/// A field name, or the name of a selector component; the first character that ends it is not consumed.
///
/// The scans below are `for` loops over the text, never a manual index that a fault could stop advancing: what they build
/// is bounded by the length of the input.
fn name(r: &[char], start: usize, in_selector: bool) -> Result<(String, usize), PathError> {
    let mut b = String::new();
    let mut skip = 0usize; // characters already taken by an escape
    let mut end = r.len();
    for (i, &c) in r.iter().enumerate().skip(start) {
        if skip > 0 {
            skip -= 1;
            continue;
        }
        if c == '\\' {
            match r.get(i + 1) {
                Some(e) if ESCAPABLE.contains(*e) => {
                    b.push(*e);
                    skip = 1;
                }
                _ => return Err(fail("PATH_BAD_ESCAPE", i)),
            }
        } else if c < '\u{20}' || c == '\u{7f}' {
            return Err(fail("PATH_SYNTAX", i));
        } else if (!in_selector && (c == '.' || c == '[')) || (in_selector && c == '=') {
            end = i;
            break;
        } else if ESCAPABLE.contains(c) {
            return Err(fail("PATH_SYNTAX", i));
        } else {
            b.push(c);
        }
    }
    if b.is_empty() {
        return Err(fail("PATH_EMPTY_NAME", start));
    }
    Ok((b, end))
}

fn selector(r: &[char], start: usize) -> Result<(Vec<KeyComp>, usize), PathError> {
    let mut i = start + 1;
    match r.get(i) {
        None => return Err(fail("PATH_SYNTAX", i)),
        Some(&c) if is_digit(c) || (c == '-' && r.get(i + 1).is_some_and(|d| is_digit(*d))) => {
            return Err(fail("PATH_INDEX_SELECTOR", start));
        }
        Some('{') => {}
        Some(_) => return Err(fail("PATH_SYNTAX", i)),
    }
    i += 1;
    let mut comps: Vec<KeyComp> = Vec::new();
    loop {
        let name_start = i;
        let (n, next) = name(r, i, true)?;
        if comps.iter().any(|c| c.name == n) {
            return Err(fail("PATH_DUPLICATE_COMPONENT", name_start));
        }
        i = next;
        if r.get(i) != Some(&'=') {
            return Err(fail("PATH_SYNTAX", i));
        }
        i += 1;
        let (value, next) = match r.get(i) {
            None => return Err(fail("PATH_SYNTAX", i)),
            Some('"') => {
                let (s, next) = string(r, i)?;
                (KeyValue::Str(s), next)
            }
            // anything else is read as an integer, which refuses what is not one
            Some(_) => {
                let (n, next) = integer(r, i)?;
                (KeyValue::Int(n), next)
            }
        };
        i = next;
        comps.push(KeyComp { name: n, value });
        match r.get(i) {
            Some(',') => i += 1,
            Some('}') => {
                i += 1;
                break;
            }
            _ => return Err(fail("PATH_SYNTAX", i)),
        }
    }
    if r.get(i) != Some(&']') {
        return Err(fail("PATH_SYNTAX", i));
    }
    Ok((comps, i + 1))
}

fn integer(r: &[char], start: usize) -> Result<(i64, usize), PathError> {
    let plus = r[start] == '+';
    let neg = r[start] == '-';
    let ds = if plus || neg { start + 1 } else { start };
    let count = r[ds..].iter().take_while(|c| is_digit(**c)).count();
    let end = ds + count;
    if count == 0 {
        return Err(fail("PATH_SYNTAX", end));
    }
    let digits: String = r[ds..end].iter().collect();
    if plus || (digits.len() > 1 && digits.starts_with('0')) || (neg && digits == "0") {
        return Err(fail("PATH_NONCANONICAL", start));
    }
    let text = if neg { format!("-{digits}") } else { digits };
    match text.parse::<i64>() {
        Ok(n) => Ok((n, end)),
        Err(_) => Err(fail("PATH_INTEGER_RANGE", start)),
    }
}

/// A canonical double-quoted JSON string: `\"` `\\` `\b` `\f` `\n` `\r` `\t`, `\u00xx` in lower-case hex for the other
/// control characters, everything else literal.
fn string(r: &[char], start: usize) -> Result<(String, usize), PathError> {
    let mut b = String::new();
    let mut skip = 0usize; // characters already taken by an escape
    for (i, &c) in r.iter().enumerate().skip(start + 1) {
        if skip > 0 {
            skip -= 1;
            continue;
        }
        if c == '"' {
            return Ok((b, i + 1));
        }
        if c < '\u{20}' {
            return Err(fail("PATH_NONCANONICAL", i));
        }
        if c != '\\' {
            b.push(c);
            continue;
        }
        match r.get(i + 1) {
            None => return Err(fail("PATH_BAD_ESCAPE", i)),
            Some(&e @ ('"' | '\\')) => {
                b.push(e);
                skip = 1;
            }
            Some(&e @ ('b' | 'f' | 'n' | 'r' | 't')) => {
                b.push(match e {
                    'b' => '\u{8}',
                    'f' => '\u{c}',
                    'n' => '\n',
                    'r' => '\r',
                    _ => '\t',
                });
                skip = 1;
            }
            Some('/') => return Err(fail("PATH_NONCANONICAL", i)),
            Some('u') => {
                let hex: String = r
                    .get(i + 2..i + 6)
                    .map_or_else(String::new, |h| h.iter().collect());
                if hex.chars().count() != 4 || !hex.chars().all(|h| h.is_ascii_hexdigit()) {
                    return Err(fail("PATH_BAD_ESCAPE", i));
                }
                let v = u32::from_str_radix(&hex, 16).expect("four hex digits");
                if hex != hex.to_lowercase() || v >= 0x20 || [8, 9, 10, 12, 13].contains(&v) {
                    return Err(fail("PATH_NONCANONICAL", i));
                }
                b.push(char::from_u32(v).expect("below 0x20"));
                skip = 5;
            }
            Some(_) => return Err(fail("PATH_BAD_ESCAPE", i)),
        }
    }
    Err(fail("PATH_SYNTAX", r.len()))
}

fn format_fail(code: &'static str) -> PathError {
    PathError {
        code,
        phase: Phase::PathFormat,
        offset: 0,
        logical_path: None,
    }
}

/// The canonical text of segments, or the error of the one that cannot be written: an empty field or component name
/// (`PATH_EMPTY_NAME`), a control character in a name (`PATH_SYNTAX`), a key value with no encoding
/// (`PATH_KEY_UNSUPPORTED`, `PATH_INTEGER_RANGE`).
///
/// # Errors
/// A [`PathError`] of phase `path_format`.
pub fn format_path(segs: &[PathSeg]) -> Result<String, PathError> {
    let check = |n: &str| -> Option<&'static str> {
        if n.is_empty() {
            Some("PATH_EMPTY_NAME")
        } else if n.chars().any(|c| c <= '\u{1f}' || c == '\u{7f}') {
            Some("PATH_SYNTAX")
        } else {
            None
        }
    };
    let mut out = String::from("$");
    for s in segs {
        match s {
            PathSeg::Field(f) => {
                if let Some(code) = check(f) {
                    return Err(format_fail(code));
                }
                out.push('.');
                write_name(&mut out, f);
            }
            PathSeg::Item(item) => {
                if item.is_empty() {
                    return Err(format_fail("PATH_SYNTAX"));
                }
                for c in item {
                    if let Some(code) = check(&c.name) {
                        return Err(format_fail(code));
                    }
                    match c.value {
                        KeyValue::Boolean(_) => return Err(format_fail("PATH_KEY_UNSUPPORTED")),
                        KeyValue::OutOfRange => return Err(format_fail("PATH_INTEGER_RANGE")),
                        _ => {}
                    }
                }
                if !write_selector(&mut out, item) {
                    return Err(format_fail("PATH_SYNTAX"));
                }
            }
        }
    }
    Ok(out)
}

/// What a path addresses in a schema: `target` is root, scalar, composite, list, set, opaque or item, and `primitive`
/// whether it is a primitive position (a field is; the root and a list element are not, ADR 0002).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Resolved {
    pub target: &'static str,
    pub primitive: bool,
}

fn target_of(s: &Shape) -> &'static str {
    match s.shape_type {
        ShapeType::Scalar(_) => "scalar",
        ShapeType::Composite(_) => "composite",
        ShapeType::Set { .. } => "set",
        ShapeType::Opaque => "opaque",
        ShapeType::List { .. } | ShapeType::DeclaredList { .. } => "list",
    }
}

/// Why a field segment finds no fields: a list that was not selected, or something that has no children.
fn no_fields_code(cur: &Cur<'_>) -> &'static str {
    match cur {
        Cur::Field(sh)
            if matches!(
                sh.shape_type,
                ShapeType::List { .. } | ShapeType::DeclaredList { .. }
            ) =>
        {
            "PATH_LIST_NOT_SELECTED"
        }
        _ => "PATH_NO_CHILDREN",
    }
}

/// Where a resolution stands: the root or a list element's record (both have fields), or a field of the schema.
enum Cur<'a> {
    Fields(&'a [(String, Shape)]),
    Field(&'a Shape),
}

/// Resolves segments against `root` (a composite) by the identity of the schema (ADR 0006 "Resolution against the
/// schema"): the key of the list, or of the `item_choice` case the component names select.
///
/// # Errors
/// A [`PathError`] of phase `path_resolve` with the logical path up to and including the segment at fault.
pub fn resolve_path(root: &Shape, segs: &[PathSeg]) -> Result<Resolved, PathError> {
    let root_fields: &[(String, Shape)] = match &root.shape_type {
        ShapeType::Composite(f) => f,
        _ => &[],
    };
    let mut cur = Cur::Fields(root_fields);
    let mut target = "root";
    let mut up_to: Vec<PathSeg> = Vec::new();
    for s in segs {
        up_to.push(s.clone());
        let at_fault = |code: &'static str| PathError {
            code,
            phase: Phase::PathResolve,
            offset: 0,
            logical_path: format_path(&up_to).ok(),
        };
        // the fields a field segment looks in: the root, a record, or a composite field
        let fields: Option<&[(String, Shape)]> = match &cur {
            Cur::Fields(f) => Some(f),
            Cur::Field(sh) => match &sh.shape_type {
                ShapeType::Composite(f) => Some(f),
                _ => None,
            },
        };
        match s {
            PathSeg::Field(name) => {
                let fields = match fields {
                    Some(f) => f,
                    None => return Err(at_fault(no_fields_code(&cur))),
                };
                let next = match fields.iter().find(|(n, _)| n == name) {
                    Some((_, next)) => next,
                    None => return Err(at_fault("UNKNOWN_FIELD")),
                };
                target = target_of(next);
                cur = Cur::Field(next);
            }
            PathSeg::Item(item) => {
                let elements = match &cur {
                    Cur::Field(sh) => match &sh.shape_type {
                        ShapeType::Set { .. } => return Err(at_fault("PATH_SET_ELEMENT")),
                        ShapeType::DeclaredList { elements, .. } => elements,
                        _ => return Err(at_fault("PATH_NOT_A_LIST")),
                    },
                    Cur::Fields(_) => return Err(at_fault("PATH_NOT_A_LIST")),
                };
                let names: Vec<&str> = item.iter().map(|k| k.name.as_str()).collect();
                let case: &Case = match elements
                    .cases
                    .iter()
                    .find(|c| c.key.iter().map(String::as_str).collect::<Vec<_>>() == names)
                {
                    Some(c) => c,
                    None => return Err(at_fault("PATH_SELECTOR_MISMATCH")),
                };
                for k in item {
                    let shape = case
                        .fields
                        .iter()
                        .find(|(n, _)| *n == k.name)
                        .map(|(_, f)| f);
                    let want_int = match shape.map(|f| &f.shape_type) {
                        Some(ShapeType::Scalar(ScalarType::String)) => false,
                        Some(ShapeType::Scalar(ScalarType::Integer)) => true,
                        _ => return Err(at_fault("PATH_KEY_UNSUPPORTED")),
                    };
                    if matches!(k.value, KeyValue::Int(_)) != want_int {
                        return Err(at_fault("PATH_KEY_TYPE"));
                    }
                }
                target = "item";
                cur = Cur::Fields(&case.fields);
            }
        }
    }
    Ok(Resolved {
        target,
        primitive: target != "root" && target != "item",
    })
}
