//! `read_diff` (P4, after P4-3; ADR 0008, "Canonical serialization"): the canonical bytes of a diff read back into events.
//!
//! It is strict and it refuses: the bytes must be exactly the writing ADR 0008 fixes, so that
//! `DiffResult { changes, representation }.canonical() == input` holds for everything it accepts. It reads no schema and decides nothing about meaning: it
//! checks the form of the bytes, the shape of every event and entry, the canonical text and the order of the paths, and that no event describes no change.
//! Representation entries are returned apart and are never events of a patch (`PatchDiff` does not carry them); an event kind the patch does not apply is
//! returned like any other, for the patch to refuse by name.
//!
//! `read_diff` reads a diff of version 1 and nothing else, so that the equation above holds for every success. A diff of another version cannot be
//! interpreted: it is refused with the reason `"version"`. Which version it is, is a different question, and [`read_diff_version`] answers it: it reads one
//! canonical JSON object with an integer `version` and says nothing about the rest. That is what a caller needs to refuse such a diff by name (the patch:
//! `PATCH_CONTEXT_MISMATCH`, with a `PatchDiff` of that version) and to tell it from bytes that are no diff at all.
//!
//! The error is a [`DiffReadError`]; its `reason` is one of the constants below. They are reasons of this reader, not codes of the registry of ADR 0007.
//! The Go peer is go/diffread.go.

use std::cmp::Ordering;
use std::fmt;

use crate::diff::{
    compare_scalars, seg_of, write_canonical, DeclChange, DiffEvent, DiffResult, EventKind,
    RepresentationChange,
};
use crate::node::{Entry, Node, Value};
use crate::patch::{PatchDiff, PatchEvent};
use crate::pathtext::parse_path;
use crate::recognize::{Path, Seg};

pub const READ_NOT_UTF8: &str = "not_utf8";
pub const READ_SYNTAX: &str = "syntax";
pub const READ_DEPTH: &str = "depth";
pub const READ_DUPLICATE_KEY: &str = "duplicate_key";
/// A float, an integer beyond signed 64 bits, a leading zero, a minus zero.
pub const READ_NUMBER: &str = "unsupported_number";
/// A key, a kind or a type the contract does not have.
pub const READ_SHAPE: &str = "shape";
/// Not a canonical path of ADR 0006, or the root.
pub const READ_PATH: &str = "path";
/// Events or entries not strictly in the order of ADR 0008, or one (path, kind) twice.
pub const READ_ORDER: &str = "order";
/// An event that describes no change.
pub const READ_NO_CHANGE: &str = "no_change";
/// Valid, but not the writing ADR 0008 fixes.
pub const READ_NOT_CANONICAL: &str = "not_canonical";
/// Canonical JSON with an integer version, but not the version this reader interprets ([`read_diff_version`] says which).
pub const READ_VERSION: &str = "version";

const MAX_DEPTH: usize = 64;

/// Why the bytes of a diff were refused, and at which byte offset the reader stood (0 when the refusal is about the whole document).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiffReadError {
    pub reason: &'static str,
    pub offset: usize,
}

impl fmt::Display for DiffReadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "diff not read: {} at byte {}", self.reason, self.offset)
    }
}

impl std::error::Error for DiffReadError {}

/// A diff read from its canonical bytes.
#[derive(Debug, Clone)]
pub struct CanonicalDiff {
    pub version: i64,
    pub changes: Vec<DiffEvent>,
    pub representation: Vec<RepresentationChange>,
}

impl CanonicalDiff {
    /// The diff as the patch takes it: the version and the events; the representation entries are not events.
    #[must_use]
    pub fn patch_diff(&self) -> PatchDiff {
        PatchDiff {
            version: self.version,
            changes: self.changes.iter().map(PatchEvent::from).collect(),
        }
    }
}

struct Reader<'a> {
    b: &'a [u8],
    at: usize,
}

type Parsed<T> = Result<T, DiffReadError>;

impl Reader<'_> {
    fn fail<T>(&self, reason: &'static str) -> Parsed<T> {
        Err(DiffReadError {
            reason,
            offset: self.at,
        })
    }

    fn peek(&self) -> Option<u8> {
        self.b.get(self.at).copied()
    }

    fn value(&mut self, depth: usize) -> Parsed<Node> {
        if depth > MAX_DEPTH {
            return self.fail(READ_DEPTH);
        }
        match self.peek() {
            Some(b'{') => self.object(depth),
            Some(b'[') => self.array(depth),
            Some(b'"') => self.string().map(|s| Node::new(Value::Str(s))),
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => self.literal(),
        }
    }

    fn literal(&mut self) -> Parsed<Node> {
        for (text, value) in [
            ("true", Value::Bool(true)),
            ("false", Value::Bool(false)),
            ("null", Value::Null),
        ] {
            if self.b[self.at..].starts_with(text.as_bytes()) {
                self.at += text.len();
                return Ok(Node::new(value));
            }
        }
        self.fail(READ_SYNTAX)
    }

    fn number(&mut self) -> Parsed<Node> {
        let start = self.at;
        if self.peek() == Some(b'-') {
            self.at += 1;
        }
        let digits = self.at;
        while self.peek().is_some_and(|c| c.is_ascii_digit()) {
            self.at += 1;
        }
        if self.at == digits {
            return self.fail(READ_SYNTAX);
        }
        if matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            return self.fail(READ_NUMBER);
        }
        let text = std::str::from_utf8(&self.b[start..self.at]).unwrap_or("");
        let leading_zero = self.b[digits] == b'0' && self.at - digits > 1;
        match text.parse::<i64>() {
            Ok(n) if !leading_zero && text != "-0" => Ok(Node::new(Value::Int(n))),
            _ => self.fail(READ_NUMBER),
        }
    }

    fn hex4(&mut self) -> Option<u32> {
        let digits = self.b.get(self.at..self.at + 4)?;
        self.at += 4; // the four bytes are consumed whether they are digits or not, as in the Go reader
        digits
            .iter()
            .try_fold(0u32, |n, c| Some(n * 16 + char::from(*c).to_digit(16)?))
    }

    fn string(&mut self) -> Parsed<String> {
        self.at += 1; // the opening quote
        let mut out: Vec<u8> = Vec::new();
        loop {
            let c = match self.peek() {
                Some(c) => c,
                None => return self.fail(READ_SYNTAX),
            };
            self.at += 1;
            match c {
                b'"' => return String::from_utf8(out).or_else(|_| self.fail(READ_NOT_UTF8)),
                0..=0x1f => return self.fail(READ_SYNTAX),
                b'\\' => {
                    let ch = self.escape()?;
                    let mut buf = [0u8; 4];
                    out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                }
                other => out.push(other),
            }
        }
    }

    fn escape(&mut self) -> Parsed<char> {
        let esc = match self.peek() {
            Some(c) => c,
            None => return self.fail(READ_SYNTAX),
        };
        self.at += 1;
        match esc {
            b'"' => Ok('"'),
            b'\\' => Ok('\\'),
            b'/' => Ok('/'),
            b'b' => Ok('\u{8}'),
            b'f' => Ok('\u{c}'),
            b'n' => Ok('\n'),
            b'r' => Ok('\r'),
            b't' => Ok('\t'),
            b'u' => self.unicode(),
            _ => self.fail(READ_SYNTAX),
        }
    }

    fn unicode(&mut self) -> Parsed<char> {
        let first = match self.hex4() {
            Some(n) => n,
            None => return self.fail(READ_SYNTAX),
        };
        let code = match first {
            0xd800..=0xdbff => {
                // a high surrogate must be followed by a low one; the pair is valid JSON for one character that ADR 0008 writes literally, so the
                // escape is never the canonical writing and is refused at once (the character it stands for would change nothing)
                if !self.b[self.at..].starts_with(b"\\u") {
                    return self.fail(READ_SYNTAX);
                }
                self.at += 2;
                return match self.hex4() {
                    Some(0xdc00..=0xdfff) => self.fail(READ_NOT_CANONICAL),
                    _ => self.fail(READ_SYNTAX),
                };
            }
            0xdc00..=0xdfff => return self.fail(READ_SYNTAX),
            other => other,
        };
        match char::from_u32(code) {
            Some(ch) => Ok(ch),
            None => self.fail(READ_SYNTAX),
        }
    }

    fn array(&mut self, depth: usize) -> Parsed<Node> {
        self.at += 1;
        let mut items = Vec::new();
        if self.peek() == Some(b']') {
            self.at += 1;
            return Ok(Node::new(Value::Seq(items)));
        }
        loop {
            items.push(self.value(depth + 1)?);
            match self.peek() {
                Some(b',') => self.at += 1,
                Some(b']') => {
                    self.at += 1;
                    return Ok(Node::new(Value::Seq(items)));
                }
                _ => return self.fail(READ_SYNTAX),
            }
        }
    }

    fn object(&mut self, depth: usize) -> Parsed<Node> {
        self.at += 1;
        let mut entries: Vec<Entry> = Vec::new();
        if self.peek() == Some(b'}') {
            self.at += 1;
            return Ok(Node::new(Value::Map(entries)));
        }
        loop {
            if self.peek() != Some(b'"') {
                return self.fail(READ_SYNTAX);
            }
            let key = self.string()?;
            if entries.iter().any(|e| e.key == key) {
                return self.fail(READ_DUPLICATE_KEY);
            }
            if self.peek() != Some(b':') {
                return self.fail(READ_SYNTAX);
            }
            self.at += 1;
            let value = self.value(depth + 1)?;
            entries.push(Entry {
                key,
                key_pos: None,
                value,
            });
            match self.peek() {
                Some(b',') => self.at += 1,
                Some(b'}') => {
                    self.at += 1;
                    return Ok(Node::new(Value::Map(entries)));
                }
                _ => return self.fail(READ_SYNTAX),
            }
        }
    }
}

fn whole(reason: &'static str) -> DiffReadError {
    DiffReadError { reason, offset: 0 }
}

fn get<'a>(n: &'a Node, key: &str) -> Option<&'a Node> {
    match &n.value {
        Value::Map(entries) => entries.iter().find(|e| e.key == key).map(|e| &e.value),
        _ => None,
    }
}

/// The node's members when it is a map with exactly the given keys.
fn members<'a>(n: &'a Node, keys: &[&str]) -> Option<Vec<&'a Node>> {
    match &n.value {
        Value::Map(entries) if entries.len() == keys.len() => {
            keys.iter().map(|k| get(n, k)).collect()
        }
        _ => None,
    }
}

fn text(n: &Node) -> Option<&str> {
    match &n.value {
        Value::Str(s) => Some(s),
        _ => None,
    }
}

fn items(n: &Node) -> Option<&[Node]> {
    match &n.value {
        Value::Seq(v) => Some(v),
        _ => None,
    }
}

/// The key of a canonical, non-root path in the order of ADR 0007.
fn path_order(path: &str) -> Option<Path> {
    let segs = parse_path(path).ok()?;
    if segs.is_empty() {
        None
    } else {
        Some(segs.iter().map(seg_of).collect::<Vec<Seg>>())
    }
}

fn is_scalar(n: &Node) -> bool {
    !matches!(n.value, Value::Seq(_) | Value::Map(_))
}

/// Whether the scalars are in the order of ADR 0008 with no repeat.
fn strictly_sorted(v: &[Node]) -> bool {
    v.iter().all(is_scalar)
        && v.windows(2)
            .all(|w| compare_scalars(&w[0], &w[1]) == Ordering::Less)
}

fn kind_of(name: &str) -> Option<EventKind> {
    match name {
        "added" => Some(EventKind::Added),
        "removed" => Some(EventKind::Removed),
        "value_change" => Some(EventKind::ValueChange),
        "policy_declaration_change" => Some(EventKind::PolicyDeclarationChange),
        "set_change" => Some(EventKind::SetChange),
        _ => None,
    }
}

fn blank(kind: EventKind, path: &str, order: Path) -> DiffEvent {
    DiffEvent {
        kind,
        path: path.to_string(),
        previous: None,
        next: None,
        changed: Vec::new(),
        added: Vec::new(),
        removed: Vec::new(),
        order,
    }
}

/// The members of an event in the order of the canonical keys of its kind.
fn event_of(n: &Node) -> Result<DiffEvent, &'static str> {
    let kind = get(n, "kind")
        .and_then(text)
        .and_then(kind_of)
        .ok_or(READ_SHAPE)?;
    let keys: &[&str] = match kind {
        EventKind::Added => &["kind", "next", "path"],
        EventKind::Removed => &["kind", "path", "previous"],
        EventKind::ValueChange => &["kind", "next", "path", "previous"],
        EventKind::PolicyDeclarationChange => &["changed", "kind", "path"],
        EventKind::SetChange => &["added", "kind", "path", "removed"],
    };
    let m = members(n, keys).ok_or(READ_SHAPE)?;
    // `members` returns the nodes in the order of `keys`; a key that is not in the list is a mistake of this function, not of the input
    let at = |key: &str| {
        m[keys
            .iter()
            .position(|k| *k == key)
            .expect("a key of the list")]
    };
    let path_text = text(at("path")).ok_or(READ_SHAPE)?;
    let mut e = blank(kind, path_text, Vec::new());
    match kind {
        EventKind::Added => e.next = Some(at("next").clone()),
        EventKind::Removed => e.previous = Some(at("previous").clone()),
        EventKind::ValueChange => {
            e.previous = Some(at("previous").clone());
            e.next = Some(at("next").clone());
        }
        EventKind::PolicyDeclarationChange => {
            let changed = match &at("changed").value {
                Value::Map(entries) if !entries.is_empty() => entries,
                _ => return Err(READ_SHAPE),
            };
            for f in changed {
                let side = members(&f.value, &["next", "previous"]).ok_or(READ_SHAPE)?;
                e.changed.push(DeclChange {
                    field: f.key.clone(),
                    previous: side[1].clone(),
                    next: side[0].clone(),
                });
            }
        }
        EventKind::SetChange => {
            e.added = items(at("added")).ok_or(READ_SHAPE)?.to_vec();
            e.removed = items(at("removed")).ok_or(READ_SHAPE)?.to_vec();
        }
    }
    // (the path is judged after the members, as the Go peer does, so that both name the same reason for an event with two defects)
    e.order = path_order(path_text).ok_or(READ_PATH)?;
    Ok(e)
}

/// Whether the event describes no difference.
fn no_change(e: &DiffEvent) -> bool {
    match e.kind {
        EventKind::ValueChange => match (&e.previous, &e.next) {
            (Some(p), Some(n)) => p.same(n),
            _ => false,
        },
        EventKind::PolicyDeclarationChange => e.changed.iter().any(|c| c.previous.same(&c.next)),
        EventKind::SetChange => e.added.is_empty() && e.removed.is_empty(),
        _ => false,
    }
}

fn representation_of(n: &Node) -> Result<RepresentationChange, &'static str> {
    let m = members(n, &["kind", "next", "path", "previous"]).ok_or(READ_SHAPE)?;
    if text(m[0]) != Some("representation_change") {
        return Err(READ_SHAPE);
    }
    let writing = |n: &Node| match text(n) {
        Some("short") => Some("short"),
        Some("expanded") => Some("expanded"),
        _ => None,
    };
    let next = writing(m[1]).ok_or(READ_SHAPE)?;
    let previous = writing(m[3]).ok_or(READ_SHAPE)?;
    let path = text(m[2]).ok_or(READ_SHAPE)?;
    let order = path_order(path).ok_or(READ_PATH)?;
    Ok(RepresentationChange {
        path: path.to_string(),
        previous,
        next,
        order,
    })
}

fn changes_of(list: &[Node]) -> Result<Vec<DiffEvent>, &'static str> {
    let mut out: Vec<DiffEvent> = Vec::with_capacity(list.len());
    for raw in list {
        let e = event_of(raw)?;
        if let Some(last) = out.last() {
            let by_path = last.order.cmp(&e.order);
            if by_path == Ordering::Greater
                || (by_path == Ordering::Equal && last.kind.as_str() >= e.kind.as_str())
            {
                return Err(READ_ORDER);
            }
        }
        if no_change(&e) {
            return Err(READ_NO_CHANGE);
        }
        let overlap = e.added.iter().any(|x| e.removed.iter().any(|y| x.same(y)));
        if !strictly_sorted(&e.added) || !strictly_sorted(&e.removed) || overlap {
            return Err(READ_SHAPE);
        }
        out.push(e);
    }
    Ok(out)
}

fn representation_list(list: &[Node]) -> Result<Vec<RepresentationChange>, &'static str> {
    let mut out: Vec<RepresentationChange> = Vec::with_capacity(list.len());
    for raw in list {
        let r = representation_of(raw)?;
        if out.last().is_some_and(|last| last.order >= r.order) {
            return Err(READ_ORDER);
        }
        if r.previous == r.next {
            return Err(READ_NO_CHANGE);
        }
        out.push(r);
    }
    Ok(out)
}

/// Reads the canonical bytes of a diff. See the comment at the top of the file.
///
/// # Errors
/// A [`DiffReadError`] with the reason the bytes are not exactly the canonical writing of a diff of ADR 0008.
/// What `read_diff` and `read_diff_version` share and what does not depend on the version: valid UTF-8, one JSON value that is an object with an integer
/// `version` in range, and exactly the canonical writing of that value.
fn read_root(b: &[u8]) -> Result<(Node, i64), DiffReadError> {
    if std::str::from_utf8(b).is_err() {
        return Err(whole(READ_NOT_UTF8));
    }
    let mut r = Reader { b, at: 0 };
    let root = r.value(0)?;
    if r.at != b.len() {
        return r.fail(READ_SYNTAX);
    }
    let version = match get(&root, "version").map(|v| &v.value) {
        Some(Value::Int(n)) if *n >= 0 && *n <= i64::from(i32::MAX) => *n,
        _ => return Err(whole(READ_SHAPE)),
    };
    let mut generic = String::new();
    write_canonical(&mut generic, &root);
    if generic.as_bytes() != b {
        return Err(whole(READ_NOT_CANONICAL));
    }
    Ok((root, version))
}

/// Reads the version of a diff and nothing else: the bytes must be one canonical JSON object with an integer `version` from 0 to 2^31-1. It says nothing about
/// the rest of the object, so a diff of another version, which [`read_diff`] refuses with the reason `"version"`, can be named and refused by its caller.
///
/// # Errors
/// The reason is one of the `READ_*` constants; the offset is where the reader stood, 0 when the refusal is about the whole document.
pub fn read_diff_version(b: &[u8]) -> Result<i64, DiffReadError> {
    read_root(b).map(|(_, version)| version)
}

/// Reads the canonical bytes of a diff of version 1. See the comment at the top of the file.
///
/// # Errors
/// The reason is one of the `READ_*` constants; the offset is where the reader stood, 0 when the refusal is about the whole document.
pub fn read_diff(b: &[u8]) -> Result<CanonicalDiff, DiffReadError> {
    let (root, version) = read_root(b)?;
    if version != 1 {
        return Err(whole(READ_VERSION));
    }
    let top = members(&root, &["changes", "representation", "version"]).ok_or(whole(READ_SHAPE))?;
    let changes = items(top[0])
        .ok_or(whole(READ_SHAPE))
        .and_then(|l| changes_of(l).map_err(whole))?;
    let representation = items(top[1])
        .ok_or(whole(READ_SHAPE))
        .and_then(|l| representation_list(l).map_err(whole))?;
    let out = CanonicalDiff {
        version,
        changes,
        representation,
    };
    let again = DiffResult {
        changes: out.changes.clone(),
        representation: out.representation.clone(),
    }
    .canonical();
    if again.as_bytes() != b {
        return Err(whole(READ_NOT_CANONICAL));
    }
    Ok(out)
}
