package forms

// Reading the Role of a schema node (cic-primitives role.yaml and proposals/atom-grammar/README.md, section 3; ADR 0011).
//
// Role is three orthogonal axes, not a flat list: authority (exactly one of config, state, operational; config when
// not written), structural (zero or more of key, reference) and lifecycle (zero or one of derived, volatile). A node is
// written in the short form (`role: config`, `key`, `state`, `operational`, `derived`, `volatile`; `reference` has no
// short form) or in the long form (a mapping of the axes). Every other value, and every other member, is an unknown
// Role: SCHEMA_INVALID, never read as `config`.

import "slices"

type roleAxes struct {
	authority  string
	structural []string
	lifecycle  string
}

// takesDefault is ADR 0011: a schema default applies to an absent field if and only if the authority is config
// and the node has no structural and no lifecycle value. A lifecycle value excludes the authority config (the short
// forms fix it to state, readLongRole refuses the combination), so the authority covers it.
func (r roleAxes) takesDefault() bool {
	return r.authority == "config" && len(r.structural) == 0
}

var (
	authorities = []string{"config", "state", "operational"}
	structurals = []string{"key", "reference"}
	lifecycles  = []string{"derived", "volatile"}
)

// shortRoles are the short forms and what they stand for. `key` does not fix the authority: it adds the structural
// value and the authority comes from the surrounding surface, which is config for the config_surface this slice reads.
var shortRoles = map[string]roleAxes{
	"config":      {authority: "config"},
	"state":       {authority: "state"},
	"operational": {authority: "operational"},
	"key":         {authority: "config", structural: []string{"key"}},
	"derived":     {authority: "state", lifecycle: "derived"},
	"volatile":    {authority: "state", lifecycle: "volatile"},
}

// readRole reads the `role` member of the node n; a node that does not write one is config.
func readRole(n *Node, path string) (roleAxes, *Error) {
	r := lookup(n, "role")
	path += ".role"
	if r == nil {
		return roleAxes{authority: "config"}, nil
	}
	switch r.Kind {
	case KindStr:
		if axes, ok := shortRoles[r.Str]; ok {
			return axes, nil
		}
		return roleAxes{}, schemaErr(SchemaInvalid, path, "%q is not a Role (reference has no short form and must be written out)", r.Str)
	case KindMap:
		return readLongRole(r, path)
	}
	return roleAxes{}, schemaErr(SchemaInvalid, path, "a Role is a string or a mapping of its axes")
}

func readLongRole(r *Node, path string) (roleAxes, *Error) {
	out := roleAxes{authority: "config"}
	for _, e := range r.Entries {
		p := path + "." + e.Key
		switch e.Key {
		case "authority":
			if !slices.Contains(authorities, e.Value.Str) { // Str is empty for every kind but a string
				return out, schemaErr(SchemaInvalid, p, "authority is exactly one of config, state, operational")
			}
			out.authority = e.Value.Str
		case "structural":
			if e.Value.Kind != KindSeq {
				return out, schemaErr(SchemaInvalid, p, "structural is a list of key and reference")
			}
			for _, it := range e.Value.Items {
				if !slices.Contains(structurals, it.Str) || slices.Contains(out.structural, it.Str) {
					return out, schemaErr(SchemaInvalid, p, "structural lists key and reference, each at most once")
				}
				out.structural = append(out.structural, it.Str)
			}
		case "lifecycle":
			if !slices.Contains(lifecycles, e.Value.Str) {
				return out, schemaErr(SchemaInvalid, p, "lifecycle is derived or volatile")
			}
			out.lifecycle = e.Value.Str
		default:
			return out, schemaErr(SchemaInvalid, p, "not an axis of Role")
		}
	}
	// The combination rules of the atom grammar: derived and volatile exclude config, and a key is neither.
	if out.lifecycle != "" && out.authority == "config" {
		return out, schemaErr(SchemaInvalid, path, "derived and volatile exclude the authority config")
	}
	if out.lifecycle != "" && slices.Contains(out.structural, "key") {
		return out, schemaErr(SchemaInvalid, path, "a key is neither derived nor volatile")
	}
	return out, nil
}
