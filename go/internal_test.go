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

// Outside the package a Grammar can only come from BindGrammar, so a version the contract does not define
// has to be built here. Such a Grammar is not grammar 2: it reads nothing and writes nothing.
func TestAGrammarOfAnUnknownVersionIsNeitherReadNorWritten(t *testing.T) {
	for _, version := range []int64{-1, 0, 3, 100} {
		g := Grammar{version: version}
		for _, n := range []*Node{{Kind: KindStr, Str: "inherit"}, {Kind: KindBool, Bool: true}, {Kind: KindInt}} {
			mode, v := ReadMode(n, g)
			if v == nil || v.Code != "UNKNOWN_GRAMMAR_VERSION" || mode != ModeInvalid {
				t.Errorf("version %d read %+v: %v %+v", version, n, mode, v)
			}
		}
		for _, m := range []Mode{ModeInherit, ModeNoInherit, ModeReset} {
			if n, err := WriteMode(m, g); n != nil || err == nil {
				t.Errorf("version %d wrote mode %d: %+v", version, m, n)
			}
		}
	}
}
