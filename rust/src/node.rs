//! The document model: values with optional source positions.

/// A source position inside the YAML text (ADR 0007): `line` 1-based, `column` 1-based
/// in Unicode code points, `offset` 0-based in bytes of the original UTF-8 input (a
/// leading BOM included).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pos {
    pub line: usize,
    pub column: usize,
    pub offset: usize,
}

/// One mapping entry. Keys are the text of their scalar.
#[derive(Debug, Clone)]
pub struct Entry {
    pub key: String,
    pub key_pos: Option<Pos>,
    pub value: Node,
}

/// A YAML value. Absent is not a value (ADR 0003): it is a missing entry.
#[derive(Debug, Clone)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Seq(Vec<Node>),
    Map(Vec<Entry>),
}

/// A value and, when it came from source text, where it starts.
#[derive(Debug, Clone)]
pub struct Node {
    pub value: Value,
    pub pos: Option<Pos>,
}

impl Node {
    /// A node that has no source text.
    #[must_use]
    pub fn new(value: Value) -> Node {
        Node { value, pos: None }
    }

    /// A mapping node without positions, entries in the given order.
    #[must_use]
    pub fn map(entries: Vec<(&str, Node)>) -> Node {
        Node::new(Value::Map(
            entries
                .into_iter()
                .map(|(key, value)| Entry {
                    key: key.to_string(),
                    key_pos: None,
                    value,
                })
                .collect(),
        ))
    }

    /// Whether two nodes hold the same value. Positions and mapping order are not
    /// part of the value.
    #[must_use]
    pub fn same(&self, other: &Node) -> bool {
        match (&self.value, &other.value) {
            (Value::Null, Value::Null) => true,
            (Value::Bool(a), Value::Bool(b)) => a == b,
            (Value::Int(a), Value::Int(b)) => a == b,
            (Value::Str(a), Value::Str(b)) => a == b,
            (Value::Seq(a), Value::Seq(b)) => {
                a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.same(y))
            }
            (Value::Map(a), Value::Map(b)) => {
                a.len() == b.len()
                    && a.iter().all(|e| {
                        b.iter()
                            .find(|o| o.key == e.key)
                            .is_some_and(|o| o.value.same(&e.value))
                    })
            }
            _ => false,
        }
    }
}
