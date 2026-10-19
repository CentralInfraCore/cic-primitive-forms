//! YAML text to a [`Node`] tree with source positions.
//!
//! The supported subset is deliberately narrow and everything outside it is an explicit
//! [`Error::Unsupported`], never a silent reinterpretation: one document; no anchors,
//! aliases, tags or merge keys; mapping keys are scalars (a key is the scalar's text);
//! plain scalars resolve as `null`/`~`/empty, `true`/`false` (any of the three YAML 1.2
//! spellings of case), and decimal integers that fit `i64`; quoted and block scalars are
//! strings. A plain scalar that looks like a float, a hex/octal number, an integer with a
//! leading zero or an integer beyond `i64` is unsupported rather than guessed. The characters
//! U+0085, U+2028 and U+2029 are line breaks to some YAML readers and not to others, so a text
//! holding one is refused rather than read two ways.
//!
//! A duplicate mapping key is the one `parse`-phase error of the contract
//! (`DUPLICATE_KEY`, anchored at the second occurrence); only the first, in source
//! order, is reported.

use std::collections::HashSet;

use saphyr_parser::{Event, Parser, ScalarStyle};

use crate::error::{Error, Location, Phase, Violation};
use crate::node::{Entry, Node, Pos, Value};

/// Resource limits. They are an implementation boundary, not a contract value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub max_bytes: usize,
    pub max_depth: usize,
    pub max_nodes: usize,
}

impl Default for Limits {
    fn default() -> Limits {
        Limits {
            max_bytes: 1 << 20,
            max_depth: 64,
            max_nodes: 100_000,
        }
    }
}

/// Parses with the default [`Limits`].
///
/// # Errors
/// See [`parse_with`].
pub fn parse(text: &str) -> Result<Node, Error> {
    parse_with(text, Limits::default())
}

/// Parses one YAML document.
///
/// # Errors
/// [`Error::Contract`] for a duplicate key, [`Error::Unsupported`] outside the subset,
/// [`Error::Syntax`] when the YAML library rejects the text, [`Error::Limit`] when a limit
/// is exceeded.
pub fn parse_with(text: &str, limits: Limits) -> Result<Node, Error> {
    if text.len() > limits.max_bytes {
        return Err(Error::Limit(format!(
            "input is {} bytes, the limit is {}",
            text.len(),
            limits.max_bytes
        )));
    }
    let bom_len = if text.starts_with('\u{FEFF}') { 3 } else { 0 };
    let body = &text[bom_len..];
    if let Some(c) = body
        .chars()
        .find(|c| ['\u{85}', '\u{2028}', '\u{2029}'].contains(c))
    {
        return Err(Error::Unsupported(format!(
            "line separator U+{:04X} (a line break to some YAML readers only)",
            c as u32
        )));
    }
    prescan(body)?;
    read(body, bom_len, limits)
}

/// The tree of a text that `prescan` let through. A function of its own so a test can feed it
/// what `prescan` refuses and reach the checks behind it.
fn read(body: &str, bom_len: usize, limits: Limits) -> Result<Node, Error> {
    let table = Table::new(body, bom_len);
    let ends_with_break = body.ends_with(['\n', '\r']);

    let mut stack: Vec<Frame> = Vec::new();
    let mut root: Option<Node> = None;
    let mut docs = 0;
    let mut nodes = 0;
    // The first error that is not a syntax error. The Go peer reads the whole text before it walks
    // the tree, so a syntax error anywhere beats it; the scan therefore goes on, and only a syntax
    // error or a second document replaces it.
    let mut deferred: Option<Error> = None;
    for item in Parser::new_from_str(body) {
        let (event, span) = item.map_err(|e| Error::Syntax(e.info().to_string()))?;
        if let Event::DocumentStart(_) = event {
            docs += 1;
        }
        if docs > 1 {
            continue;
        }
        let at = table.pos(span.start.index());
        let mut state = State {
            stack: &mut stack,
            root: &mut root,
            nodes: &mut nodes,
            deferred: &mut deferred,
            limits,
            ends_with_break,
            table: &table,
        };
        state.step(event, at)?;
    }
    // Go reads every document before it walks the first, so a second one beats what the first
    // holds, and a syntax error anywhere beats both.
    if docs > 1 {
        return Err(Error::Unsupported("more than one document".into()));
    }
    if let Some(e) = deferred {
        return Err(e);
    }
    Ok(root.unwrap_or_else(|| Node::new(Value::Null)))
}

/// The tree under construction, for one event. The first error that is not a syntax error is
/// kept in `deferred` and the tree goes on being built (with a null in place of what failed), so
/// the scan still reaches a syntax error further on, which wins.
struct State<'a, 'b> {
    stack: &'a mut Vec<Frame>,
    root: &'a mut Option<Node>,
    nodes: &'a mut usize,
    deferred: &'a mut Option<Error>,
    limits: Limits,
    ends_with_break: bool,
    table: &'a Table<'b>,
}

impl State<'_, '_> {
    fn defer(&mut self, e: Error) {
        self.deferred.get_or_insert(e);
    }

    fn step(&mut self, event: Event, at: Pos) -> Result<(), Error> {
        match event {
            Event::Alias(_) => {
                self.defer(Error::Unsupported("alias".into()));
                self.place(Node::new(Value::Null));
            }
            Event::Scalar(value, style, anchor, tag) => {
                if anchor != 0 {
                    self.defer(Error::Unsupported("anchor".into()));
                }
                if tag.is_some() {
                    self.defer(Error::Unsupported("tag".into()));
                }
                let plain = style == ScalarStyle::Plain;
                // A plain empty scalar has no text of its own, so no position (ADR 0007). The parser
                // reports it as `~`; the source at its span is then not a `~`.
                let has_text = !plain || self.table.char_at(at) == value.chars().next();
                if let Some(Frame::Map { seen, key, .. }) = self.stack.last_mut() {
                    if key.is_none() {
                        if !has_text {
                            // yaml.v3 refuses an empty implicit key, saphyr reads it as `~`.
                            return Err(Error::Syntax("empty mapping key".into()));
                        }
                        let merge = plain && value == "<<";
                        let duplicate = !merge && !seen.insert(value.to_string());
                        *key = Some((value.into_owned(), at));
                        if merge {
                            self.defer(Error::Unsupported("merge key".into()));
                        } else if duplicate {
                            self.defer(Error::Contract(vec![Violation {
                                code: "DUPLICATE_KEY",
                                phase: Phase::Parse,
                                location: Location::At(at),
                                logical_path: None,
                            }]));
                        }
                        return Ok(());
                    }
                }
                *self.nodes += 1;
                if let Err(e) = check_nodes(*self.nodes, self.limits) {
                    self.defer(e);
                }
                let block = matches!(style, ScalarStyle::Literal | ScalarStyle::Folded);
                if block {
                    // The two readers disagree on the value of a block scalar that ends the text
                    // without a line break and on one made of line breaks only, and they locate
                    // it differently; the subset refuses the first two and fixes the position.
                    if !self.ends_with_break {
                        self.defer(Error::Unsupported(
                            "block scalar in a text without a final line break".into(),
                        ));
                    }
                    if value.chars().all(|c| c == '\n') {
                        self.defer(Error::Unsupported("block scalar without content".into()));
                    }
                }
                let v = if plain {
                    resolve_plain(&value).unwrap_or_else(|e| {
                        self.defer(e);
                        Value::Null
                    })
                } else {
                    Value::Str(value.into_owned())
                };
                let pos = if block { self.table.indicator(at) } else { at };
                self.place(Node {
                    value: v,
                    pos: has_text.then_some(pos),
                });
            }
            Event::SequenceStart(anchor, tag) => {
                let at = self.table.dash(at);
                self.open((at, anchor, tag.is_some()), false);
            }
            Event::MappingStart(anchor, tag) => {
                self.open((at, anchor, tag.is_some()), true);
            }
            Event::SequenceEnd | Event::MappingEnd => {
                let done = match self.stack.pop() {
                    Some(Frame::Seq { items, pos }) => Node {
                        value: Value::Seq(items),
                        pos: Some(pos),
                    },
                    Some(Frame::Map { entries, pos, .. }) => Node {
                        value: Value::Map(entries),
                        pos: Some(pos),
                    },
                    None => return Ok(()),
                };
                self.place(done);
            }
            Event::DocumentStart(_)
            | Event::Nothing
            | Event::StreamStart
            | Event::StreamEnd
            | Event::DocumentEnd => {}
        }
        Ok(())
    }

    /// Starts a sequence or a mapping.
    fn open(&mut self, (at, anchor, tagged): (Pos, usize, bool), is_map: bool) {
        if anchor != 0 {
            self.defer(Error::Unsupported("anchor".into()));
        }
        if tagged {
            self.defer(Error::Unsupported("tag".into()));
        }
        if let Some(Frame::Map {
            key: key @ None, ..
        }) = self.stack.last_mut()
        {
            // The key is a collection: unsupported. A placeholder keeps the tree well formed.
            *key = Some((String::new(), at));
            self.defer(Error::Unsupported("non-scalar mapping key".into()));
        }
        *self.nodes += 1;
        if let Err(e) = check_nodes(*self.nodes, self.limits) {
            self.defer(e);
        }
        if self.stack.len() >= self.limits.max_depth {
            self.defer(Error::Limit(format!(
                "nesting is deeper than {}",
                self.limits.max_depth
            )));
        }
        if !is_map {
            // `- - a`: the parser gives the outer sequence the position of the inner one's `-`.
            let mut steps = 0;
            for frame in self.stack.iter_mut().rev() {
                match frame {
                    Frame::Seq { pos, .. } if pos.offset == at.offset => {
                        steps += 1;
                        *pos = self.table.dash_back(at, steps);
                    }
                    _ => break,
                }
            }
        }
        self.stack.push(if is_map {
            Frame::Map {
                entries: Vec::new(),
                seen: HashSet::new(),
                key: None,
                pos: at,
            }
        } else {
            Frame::Seq {
                items: Vec::new(),
                pos: at,
            }
        });
    }

    /// Hands a finished node to its parent, or makes it the root.
    fn place(&mut self, node: Node) {
        match self.stack.last_mut() {
            Some(Frame::Seq { items, .. }) => items.push(node),
            Some(Frame::Map { entries, key, .. }) => {
                let (k, key_pos) = key
                    .take()
                    .expect("a value always follows a key in a mapping");
                entries.push(Entry {
                    key: k,
                    key_pos: Some(key_pos),
                    value: node,
                });
            }
            None => *self.root = Some(node),
        }
    }
}

/// Refusals decided on the text alone, before either YAML library is asked: the two readers
/// disagree on these characters and lines, so the subset leaves them out instead of picking one.
fn prescan(body: &str) -> Result<(), Error> {
    if let Some(c) = body.chars().find(
        |&c| matches!(c, '\u{0}'..='\u{8}' | '\u{B}' | '\u{C}' | '\u{E}'..='\u{1F}' | '\u{7F}'),
    ) {
        return Err(Error::Unsupported(format!(
            "control character U+{:04X}",
            c as u32
        )));
    }
    if body.contains('\t') {
        return Err(Error::Unsupported("tab character".into()));
    }
    if body.contains('\u{FEFF}') {
        return Err(Error::Unsupported("byte order mark inside the text".into()));
    }
    scan_words(&body.chars().collect::<Vec<char>>())?;
    let mut leading = true;
    for line in body.split(['\n', '\r']) {
        if line.starts_with('%') {
            return Err(Error::Unsupported("directive".into()));
        }
        let t = line.trim_start_matches(' ');
        let marker = ["---", "..."].iter().find(|m| {
            t.strip_prefix(**m)
                .is_some_and(|rest| rest.is_empty() || rest.starts_with(' '))
        });
        if let Some(m) = marker {
            if *m == "..." || t.len() != line.len() || !leading {
                return Err(Error::Unsupported("document marker".into()));
            }
        }
        if !(t.is_empty() || t.starts_with('#')) {
            leading = false;
        }
    }
    Ok(())
}

/// A word that starts with `!`, `&` or `*`, or a `?` that is not the emitter's explicit key, is a
/// tag, an anchor, an alias or an explicit key. The readers do not agree on the odd spellings of
/// these (yaml.v3 drops a bare `!` and reads the value as if it were not there), so they are
/// refused. A comment that touches a closing `]` or `}` is refused too. Quoted scalars, comments
/// and block scalar content are skipped: the words there are text.
fn scan_words(c: &[char]) -> Result<(), Error> {
    let mut quote: Option<char> = None;
    let mut block: Option<usize> = None;
    let mut line_start = true;
    let mut indent = 0;
    // Depth of the flow collections: `[`, `{` and `,` only separate words inside one (`a[&` is one plain
    // scalar in block context).
    let mut flow = 0;
    let mut i = 0;
    while i < c.len() {
        if line_start {
            line_start = false;
            indent = c[i..].iter().take_while(|&&x| x == ' ').count();
            let blank = c.get(i + indent).is_none_or(|x| *x == '\n' || *x == '\r');
            if let Some(parent) = block {
                if blank || indent > parent {
                    while i < c.len() && c[i] != '\n' && c[i] != '\r' {
                        i += 1;
                    }
                    line_start = true;
                    i += 1;
                    continue;
                }
                block = None;
            }
        }
        let ch = c[i];
        if ch == '\n' || ch == '\r' {
            line_start = true;
            i += 1;
            continue;
        }
        if let Some(q) = quote {
            if q == '"' && ch == '\\' {
                i += 2;
                continue;
            }
            if ch == q {
                if q == '\'' && c.get(i + 1) == Some(&'\'') {
                    i += 2;
                    continue;
                }
                quote = None;
            }
            i += 1;
            continue;
        }
        let prev = if i == 0 { '\n' } else { c[i - 1] };
        let word = starts_scalar(c, i, flow);
        if ch == '#' && "\n\r ".contains(prev) {
            while i < c.len() && c[i] != '\n' && c[i] != '\r' {
                i += 1;
            }
            continue;
        }
        if ch == '#' && (prev == ']' || prev == '}') {
            return Err(Error::Unsupported(
                "comment without a space before it".into(),
            ));
        }
        if (ch == '[' || ch == '{') && word {
            flow += 1;
        } else if (ch == ']' || ch == '}') && flow > 0 {
            flow -= 1;
        }
        if (ch == '"' || ch == '\'') && word {
            quote = Some(ch);
        } else if (ch == '|' || ch == '>') && word && block_header(c, i) {
            block = Some(indent);
        } else if word {
            let next = c.get(i + 1).copied().unwrap_or(' ');
            let blank = [' ', '\n', '\r'].contains(&next);
            match ch {
                '!' => return Err(Error::Unsupported("tag".into())),
                '?' if !block_explicit_key(c, i) => {
                    return Err(Error::Unsupported("explicit mapping key".into()))
                }
                '&' | '*' if !blank => return Err(Error::Unsupported("anchor or alias".into())),
                _ => {}
            }
        }
        i += 1;
    }
    Ok(())
}

/// Where a scalar can start: after the start of a line, a `:`, `-` or `?` followed by a space, or
/// inside a flow collection after `[`, `{` or `,`. Anywhere else a `!`, `&`, `*`, `?`, `"` or `'` is
/// a character of a plain scalar (`a *b` is one scalar).
fn starts_scalar(c: &[char], i: usize, flow: usize) -> bool {
    let mut j = i;
    while j > 0 && c[j - 1] == ' ' {
        j -= 1;
    }
    match j.checked_sub(1).map(|k| c[k]) {
        None | Some('\n' | '\r') => true,
        Some('[' | '{' | ',') => flow > 0,
        Some(':' | '-' | '?') => j < i,
        Some(_) => false,
    }
}

/// `|` or `>` at `i` followed by header digits and signs, then nothing but a comment.
fn block_header(c: &[char], i: usize) -> bool {
    let rest: String = c[i + 1..]
        .iter()
        .take_while(|&&x| x != '\n' && x != '\r')
        .collect();
    let rest = rest.trim_start_matches(['+', '-', '1', '2', '3', '4', '5', '6', '7', '8', '9']);
    let rest = rest.trim_start_matches(' ');
    rest.is_empty() || rest.starts_with('#')
}

/// The `? key` form the emitter writes for a key past the implicit-key limit, the only explicit
/// key the subset reads: the `?` starts its line, one space follows, then the key's own text,
/// which does not begin with another indicator.
fn block_explicit_key(chars: &[char], i: usize) -> bool {
    let starts_line = chars[..i]
        .iter()
        .rev()
        .take_while(|&&c| c != '\n' && c != '\r')
        .all(|&c| c == ' ');
    starts_line
        && chars.get(i + 1) == Some(&' ')
        && chars.get(i + 2).is_some_and(|c| !" \n\r-?:".contains(*c))
}

fn check_nodes(count: usize, limits: Limits) -> Result<(), Error> {
    if count > limits.max_nodes {
        Err(Error::Limit(format!(
            "more than {} nodes",
            limits.max_nodes
        )))
    } else {
        Ok(())
    }
}

enum Frame {
    Seq {
        items: Vec<Node>,
        pos: Pos,
    },
    Map {
        entries: Vec<Entry>,
        /// The keys of this mapping so far: a duplicate is found in expected O(1), not by
        /// comparing with every earlier key.
        seen: HashSet<String>,
        key: Option<(String, Pos)>,
        pos: Pos,
    },
}

/// Line starts and char boundaries of the BOM-free body, to turn the parser's char index
/// into the ADR 0007 coordinates.
struct Table<'a> {
    body: &'a str,
    bom_len: usize,
    char_to_byte: Vec<usize>,
    line_starts: Vec<usize>,
}

impl<'a> Table<'a> {
    fn new(body: &'a str, bom_len: usize) -> Table<'a> {
        let mut char_to_byte: Vec<usize> = body.char_indices().map(|(i, _)| i).collect();
        char_to_byte.push(body.len());
        let bytes = body.as_bytes();
        let mut line_starts = vec![0];
        let mut i = 0;
        while i < bytes.len() {
            match bytes[i] {
                b'\n' => line_starts.push(i + 1),
                b'\r' => {
                    if bytes.get(i + 1) == Some(&b'\n') {
                        i += 1;
                    }
                    line_starts.push(i + 1);
                }
                _ => {}
            }
            i += 1;
        }
        Table {
            body,
            bom_len,
            char_to_byte,
            line_starts,
        }
    }

    fn byte_of(&self, p: Pos) -> usize {
        p.offset - self.bom_len
    }

    /// The char at a position, if any.
    fn char_at(&self, p: Pos) -> Option<char> {
        self.body[self.byte_of(p)..].chars().next()
    }

    fn pos_of_byte(&self, byte: usize) -> Pos {
        let line = self.line_starts.partition_point(|&s| s <= byte);
        let start = self.line_starts[line - 1];
        Pos {
            line,
            column: self.body[start..byte].chars().count() + 1,
            offset: self.bom_len + byte,
        }
    }

    /// A block sequence entry is located at its `-`; the parser reports the first item's text.
    fn dash(&self, p: Pos) -> Pos {
        if matches!(self.char_at(p), Some('[' | '-')) {
            return p;
        }
        let byte = self.byte_of(p);
        let line_start = self.line_starts[p.line - 1];
        let before = &self.body[line_start..byte];
        match before.trim_end_matches([' ', '\t']).strip_suffix('-') {
            Some(head) => self.pos_of_byte(line_start + head.len()),
            None => p,
        }
    }

    /// The `-` that is `steps` entries before the one at `p` on the same line (`- - a`); `p` itself
    /// when the line does not have that many.
    fn dash_back(&self, p: Pos, steps: usize) -> Pos {
        let line_start = self.line_starts[p.line - 1];
        let mut head = &self.body[line_start..self.byte_of(p)];
        for _ in 0..steps {
            match head.trim_end_matches(' ').strip_suffix('-') {
                Some(h) => head = h,
                None => return p,
            }
        }
        self.pos_of_byte(line_start + head.len())
    }

    /// A block scalar is located at its indicator; the parser reports the first content line.
    fn indicator(&self, p: Pos) -> Pos {
        let mut line = p.line;
        while line > 1 {
            line -= 1;
            let start = self.line_starts[line - 1];
            let end = self.line_starts[line];
            let text = self.body[start..end].trim_end_matches(['\r', '\n']);
            if text.trim().is_empty() {
                continue;
            }
            // `|` or `>`, header digits and signs, an optional comment.
            let head = text.split(" #").next().unwrap_or(text).trim_end();
            let cut = head.trim_end_matches(|c: char| c.is_ascii_digit() || c == '+' || c == '-');
            return match cut.chars().last() {
                Some('|' | '>') => self.pos_of_byte(start + cut.len() - 1),
                _ => p,
            };
        }
        p
    }

    fn pos(&self, char_index: usize) -> Pos {
        let last = self.char_to_byte.len() - 1;
        let byte = self.char_to_byte[char_index.min(last)];
        let line = self.line_starts.partition_point(|&s| s <= byte);
        let start = self.line_starts[line - 1];
        Pos {
            line,
            column: self.body[start..byte].chars().count() + 1,
            offset: self.bom_len + byte,
        }
    }
}

fn resolve_plain(s: &str) -> Result<Value, Error> {
    match s {
        "" | "~" | "null" | "Null" | "NULL" => return Ok(Value::Null),
        "true" | "True" | "TRUE" => return Ok(Value::Bool(true)),
        "false" | "False" | "FALSE" => return Ok(Value::Bool(false)),
        _ => {}
    }
    if let Some(digits) = decimal_digits(s) {
        if digits.len() > 1 && digits.starts_with('0') {
            return Err(Error::Unsupported(format!(
                "integer with a leading zero: {s}"
            )));
        }
        return s
            .strip_prefix('+')
            .unwrap_or(s)
            .parse::<i64>()
            .map(Value::Int)
            .map_err(|_| Error::Unsupported(format!("integer beyond i64: {s}")));
    }
    if is_float_like(s) || is_radix_number(s) {
        return Err(Error::Unsupported(format!("number spelling: {s}")));
    }
    Ok(Value::Str(s.to_string()))
}

/// The digits of `[+-]?[0-9]+`, if `s` is exactly that.
fn decimal_digits(s: &str) -> Option<&str> {
    let digits = s.strip_prefix(['+', '-']).unwrap_or(s);
    if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
        Some(digits)
    } else {
        None
    }
}

fn is_radix_number(s: &str) -> bool {
    ["0x", "0o"].iter().any(|p| {
        s.strip_prefix(p)
            .is_some_and(|rest| !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_hexdigit()))
    })
}

fn is_float_like(s: &str) -> bool {
    let t = s.strip_prefix(['+', '-']).unwrap_or(s);
    if [".inf", ".Inf", ".INF"].contains(&t) || [".nan", ".NaN", ".NAN"].contains(&s) {
        return true;
    }
    let (mantissa, exp) = match t.find(['e', 'E']) {
        Some(i) => (&t[..i], Some(&t[i + 1..])),
        None => (t, None),
    };
    if let Some(e) = exp {
        let e = e.strip_prefix(['+', '-']).unwrap_or(e);
        if e.is_empty() || !e.bytes().all(|b| b.is_ascii_digit()) {
            return false;
        }
    }
    let (int, frac) = match mantissa.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (mantissa, None),
    };
    let all_digits = |x: &str| x.bytes().all(|b| b.is_ascii_digit());
    if !all_digits(int) || !frac.is_none_or(all_digits) {
        return false;
    }
    match frac {
        Some(f) => !int.is_empty() || !f.is_empty(),
        None => !int.is_empty() && exp.is_some(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The table clamps a char index the YAML library should never report; this reaches the
    // clamp, which no document can.
    #[test]
    fn the_position_table_clamps_an_index_past_the_end() {
        let t = Table::new("a: 1\nb", 3);
        assert_eq!(
            t.pos(99),
            Pos {
                line: 2,
                column: 2,
                offset: 3 + 6
            }
        );
        assert_eq!(
            t.pos(5),
            Pos {
                line: 2,
                column: 1,
                offset: 3 + 5
            }
        );
    }

    // `prescan` refuses these before the YAML library is asked; the checks behind it stay, in case
    // a spelling gets past it, and are reached here directly.
    #[test]
    fn the_checks_behind_the_prescan_still_refuse() {
        for (text, what) in [
            ("a: &x 1\nb: *x\n", "anchor"),
            ("a: !!str x\n", "tag"),
            ("&x [a]\n", "anchor"),
            ("!!seq [a]\n", "tag"),
            ("a: 1\n---\nb: 2\n", "more than one document"),
            ("? [a]\n: b\n", "non-scalar mapping key"),
        ] {
            match read(text, 0, Limits::default()) {
                Err(Error::Unsupported(m)) => assert!(m.contains(what), "{text:?}: {m}"),
                other => panic!("{text:?}: {other:?}"),
            }
        }
    }

    #[test]
    fn an_alias_and_a_stray_end_are_handled_without_a_tree() {
        let table = Table::new("", 0);
        let (mut stack, mut root, mut nodes, mut deferred) = (Vec::new(), None, 0, None);
        let mut state = State {
            stack: &mut stack,
            root: &mut root,
            nodes: &mut nodes,
            deferred: &mut deferred,
            limits: Limits::default(),
            ends_with_break: true,
            table: &table,
        };
        let at = Pos {
            line: 1,
            column: 1,
            offset: 0,
        };
        state.step(Event::SequenceEnd, at).unwrap();
        assert!(state.deferred.is_none() && state.root.is_none());
        state.step(Event::Alias(1), at).unwrap();
        assert!(matches!(deferred, Some(Error::Unsupported(m)) if m == "alias"));
        assert!(matches!(
            root,
            Some(Node {
                value: Value::Null,
                ..
            })
        ));
    }

    // The three fallbacks of the position helpers, for a position that is not where the parser
    // reports it from.
    #[test]
    fn the_position_helpers_fall_back_to_the_reported_position() {
        let t = Table::new("a: b\n  c\n- x\n", 0);
        let at = |line, column, offset| Pos {
            line,
            column,
            offset,
        };
        // An indented dash: the position of the dash, not of the item.
        let t2 = Table::new("a:\n  - x\n  - - y\n", 0);
        assert_eq!(t2.dash(at(2, 5, 7)), at(2, 3, 5));
        assert_eq!(t2.dash_back(at(3, 7, 15), 1), at(3, 5, 13));
        assert_eq!(t2.dash_back(at(3, 7, 15), 2), at(3, 3, 11));
        // Not after a `-`: the position stays.
        assert_eq!(t.dash(at(1, 4, 3)), at(1, 4, 3));
        // After a `-`: the dash.
        assert_eq!(t.dash(at(3, 3, 11)), at(3, 1, 9));
        assert_eq!(t.dash_back(at(3, 3, 11), 1), at(3, 1, 9));
        // Fewer dashes than steps: the position stays.
        assert_eq!(t.dash_back(at(3, 3, 11), 2), at(3, 3, 11));
        // The line before is not a block scalar header: the position stays.
        assert_eq!(t.indicator(at(3, 1, 9)), at(3, 1, 9));
        assert_eq!(t.indicator(at(1, 1, 0)), at(1, 1, 0));
    }

    // What survived the mutation run: the reason of a refusal, and the places where a word scan
    // has to go on or stop.
    #[test]
    fn the_scans_stop_and_go_on_where_the_yaml_does() {
        let reason = |t: &str| match parse(t) {
            Err(Error::Unsupported(m)) => m,
            other => panic!("{t:?}: {other:?}"),
        };
        // A marker ends the leading position, and so does content before it.
        assert_eq!(reason("a: 1\n---\nb: 2\n"), "document marker");
        assert_eq!(reason("---\n---\n"), "document marker");
        // A backslash is an escape in double quotes, a character in single quotes.
        assert!(parse("a: \"x\\\"[? c]\"\n").is_ok());
        assert_eq!(reason("a: 'x\\'\nb: [? c]\n"), "explicit mapping key");
        assert_eq!(reason("a: \"x\"\nb: [? c]\n"), "explicit mapping key");
        assert_eq!(reason("a: \"x\\\"y\"\nb: [? c]\n"), "explicit mapping key");
        assert_eq!(reason("a: 'x''y'\nb: [? c]\n"), "explicit mapping key");
        for ok in [
            "a: \"x\\\"\n  ? - y\"\n",
            "a: 'x''\n  ? - y'\n",
            "a: '\n  ? - y'\n",
            "a: >\n  ? - y\n",
            "a: | # c\n  ? - y\n",
            "a: 1\r? y\r: z\r",
        ] {
            assert!(parse(ok).is_ok(), "{ok:?}");
        }
        for (text, why) in [
            ("a: \"x\\\"\"\n? - y\n", "explicit mapping key"),
            ("a: 'x'''\n? - y\n", "explicit mapping key"),
            ("a: 1 # c\r? - y\r", "explicit mapping key"),
            ("a: 1 # c\n? - y\n", "explicit mapping key"),
            ("a: |\r  x\r? - y\r", "explicit mapping key"),
            ("a: |\n  x\n? - y\n", "explicit mapping key"),
            ("a|\n  ? - y\n", "explicit mapping key"),
            ("]#\n", "comment without a space before it"),
            ("}#\n", "comment without a space before it"),
            (" ? - y\n", "explicit mapping key"),
            ("  a: [? c]\n", "explicit mapping key"),
            ("? ", "explicit mapping key"),
            ("?x", "explicit mapping key"),
        ] {
            assert_eq!(reason(text), why, "{text:?}");
        }
        // Inside a plain scalar these characters are text.
        for ok in [
            "a *b: 1\n",
            "x: a !b\n",
            "x: a &b c\n",
            "x: a ?b\n",
            "k: a, *b\n",
            "aAB# *: 1\n",
        ] {
            assert!(parse(ok).is_ok(), "{ok:?}");
        }
        // `|` that is not a block scalar header does not start one.
        assert_eq!(reason("a: [|]\n  b: [? c]\n"), "explicit mapping key");
        // A whitespace-only line less indented than the block does not end it.
        assert!(parse("k:\n  a: |\n      x\n \n      !y\n").is_ok());
        assert_eq!(
            reason("k:\n  a: |\n      x\n\n  b: [? c]\n"),
            "explicit mapping key"
        );
        // The same with lone CR line breaks.
        assert!(parse("k:\r  a: |\r      x\r \r      !y\r").is_ok());
    }
}
