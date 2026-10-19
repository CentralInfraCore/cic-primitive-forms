package forms

import (
	"testing"

	"gopkg.in/yaml.v3"
)

func aliasNode() *yaml.Node { return &yaml.Node{Kind: yaml.AliasNode} }

// The position table clamps a line the YAML library should never report; this reaches the
// clamp, which no document can.
func TestThePositionTableClampsALinePastTheEnd(t *testing.T) {
	tab := newTable("a: 1\nb: 2\n", 0)
	if got := *tab.pos(99, 1); got.Line != 3 || got.Offset != 10 {
		t.Errorf("%+v", got)
	}
	if got := *tab.pos(3, 1); got.Line != 3 {
		t.Errorf("%+v", got)
	}
}

// An alias cannot follow its anchor in a document that gets this far (the anchor is refused
// first), so the alias refusal is reached directly.
func TestAnAliasNodeIsRefused(t *testing.T) {
	w := &walker{table: newTable("", 0), limits: DefaultLimits()}
	if _, err := w.node(aliasNode(), 0); err == nil || err.(*Error).Kind != ErrUnsupported {
		t.Errorf("%v", err)
	}
}
