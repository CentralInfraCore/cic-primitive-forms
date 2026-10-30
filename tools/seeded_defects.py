#!/usr/bin/env python3
"""Seeded defects: the test suites must catch an implementation that is deliberately wrong.

A gate that cannot fail proves nothing, and neither does a test suite that has never been shown to fail on
the defects it exists to catch. For each defect below, this copies the Rust crate (or the Go module) to a
temporary directory, applies one textual change (it must match exactly once, so a stale entry cannot pass
silently), runs the tests that cover the code and requires them to FAIL - on a defect that builds: a failure that is a build
error is DOES NOT BUILD and counts against the run (#94). It first requires the unmodified copy to pass. Nothing in the repository
is modified.

    seeded_defects.py rust|go|all [--changed-from SCOPE_JSON] [--stale-only] [--match SUBSTRING] [--shard K/N]

`--changed-from` (CI, in a diff) runs only the defects of the product files that tools/diff_gate.py's scope.json lists; the others are reported `not run`.

`--stale-only` applies every entry to its file and runs no test: seconds, and it says which entries no longer match the code (after a
refactor, run it before the slow run). `--match` runs only the defects whose name or file contains the substring (the baseline still
runs); a partial run is for the development loop, CI runs them all.

`--shard K/N` (K from 0) runs every N-th defect of each language, starting at K: N processes with K = 0..N-1 cover every defect exactly once
(run the N processes side by side by hand). Each process builds its own copy of the module, and the baseline runs in each.

The first defects are about the grammar-1 spelling of `inherit`, the version binding, the pin verification and
the CertPattern reading (cic-primitives D-018, ADR 0004): the places where a wrong implementation looks
right on the happy path. The last ones are about the parser parity of the two languages (error priority,
positions, the pre-parse refusals): the shared regression corpus must turn red on each.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (language, name, file, [(old, new), ...])
DEFECTS = [
    ("rust", "integer 0 is read as no-inherit (0 coerced to false)", "src/access.rs",
     [("(Value::Int(0), _) => Ok(Mode::Reset),", "(Value::Int(0), _) => Ok(Mode::NoInherit),")]),
    ("rust", "a missing version silently falls back to grammar 1", "src/access.rs",
     [("None | Some(Value::Null) => Err(binding(\"GRAMMAR_VERSION_MISSING\")),", "None | Some(Value::Null) => Ok(1),"),
      ("if is_missing(context) || is_missing(pinned) {", "if false {")]),
    ("rust", "the pinned schema is used without verifying its digest", "src/access.rs",
     [("if sha256_hex(schema) != expected_sha256 {", "if false {")]),
    ("rust", "a string without '=' is a CertPattern", "src/access.rs",
     [("        None => false,\n    })", "        None => true,\n    })")]),
    ("rust", "the empty list is read as the symbol inherit", "src/access.rs",
     [("            Ok(Rule::List(patterns))", "            if patterns.is_empty() { Ok(Rule::Symbol) } else { Ok(Rule::List(patterns)) }")]),
    ("rust", "grammar 2 accepts the legacy boolean spelling", "src/access.rs",
     [("None if legacy => Err(invalid(\"LEGACY_INHERIT_IN_GRAMMAR_2\", node)),", "None if legacy => Ok(Mode::Inherit),")]),
    ("rust", "a violation drops its source position", "src/access.rs",
     [("location: node.pos.map_or(Location::Unavailable, Location::At),", "location: Location::Unavailable,")]),
    # P3: spec.policy_surface and identity.base of the compiled schema (go/policy.go, rust/src/policy.rs).
    ("rust", "two surface rules for the same surface and operation are not a conflict", "src/policy.rs",
     [("if declared.iter().any(|(s, o)| *s == rule.surface && *o == op) {", "if false {")]),
    ("rust", "an inherit member declares an empty rule (nobody) instead of no rule", "src/policy.rs",
     [("Ok(Rule::Symbol) => continue, // inherit declares no rule for this operation", "Ok(Rule::Symbol) => Vec::new(),")]),
    ("rust", "default_access accepts any symbol, owner included", "src/policy.rs",
     [("Value::Str(s) if s == symbol => return Some(PolicyRule::Symbol(symbol)),", "Value::Str(s) if s == \"open\" || s == \"owner\" => return Some(PolicyRule::Symbol(if s == \"open\" { \"open\" } else { \"owner\" })),")]),
    ("rust", "an unknown member of PolicySurface is ignored", "src/policy.rs",
     [("_ => problems.push(invalid(&path, \"not a member of PolicySurface\")),", "_ => {}")]),
    ("rust", "an explicit inherit_policy is not marked as declared", "src/policy.rs",
     [("out.inherit_policy_declared = true;", "out.inherit_policy_declared = false;")]),
    ("rust", "identity.base is dropped from the compiled schema", "src/schema.rs",
     [("base = str_member(id, \"base\").to_string();", "base = String::new();")]),
    ("go", "two surface rules for the same surface and operation are not a conflict", "policy.go",
     [("case declared[key]:", "case false:")]),
    ("go", "an inherit member declares an empty rule (nobody) instead of no rule", "policy.go",
     [("case r.Symbol: // inherit declares no rule for this operation\n", "case r.Symbol:\n\t\t\t\t\tdeclared[key] = true\n\t\t\t\t\tif e.Key == \"access\" {\n\t\t\t\t\t\trule.Access = &PolicyRule{Patterns: []string{}}\n\t\t\t\t\t} else {\n\t\t\t\t\t\trule.Modify = &PolicyRule{Patterns: []string{}}\n\t\t\t\t\t}\n")]),
    ("go", "default_access accepts any symbol, owner included", "policy.go",
     [("if n.Str == symbol { // Str is empty for every kind but a string\n\t\treturn &PolicyRule{Symbol: symbol}", "if n.Str == \"open\" || n.Str == \"owner\" {\n\t\treturn &PolicyRule{Symbol: n.Str}")]),
    ("go", "an unknown member of PolicySurface is ignored", "policy.go",
     [("\t\tdefault:\n\t\t\tc.record(schemaErr(SchemaInvalid, path, \"not a member of PolicySurface\"))", "\t\tdefault:")]),
    ("go", "an explicit inherit_policy is not marked as declared", "policy.go",
     [("out.InheritPolicy, out.InheritPolicyDeclared = mode, true", "out.InheritPolicy, out.InheritPolicyDeclared = mode, false")]),
    ("go", "identity.base is dropped from the compiled schema", "schema.go",
     [("out.Base = strMember(id, \"base\")", "out.Base = \"\"")]),
    # P3 second unit: schema defaults (go/defaults.go, go/role.go, rust/src/defaults.rs, rust/src/role.rs).
    ("rust", "a present field is overwritten by its default", "src/defaults.rs",
     [("visit(&mut entries[i].value, shape, a, path, found, unwritable);", "if let Some(d) = &shape.default { entries[i].value = d.0.clone(); }\n                visit(&mut entries[i].value, shape, a, path, found, unwritable);")]),
    ("rust", "an inserted default is reported as authored", "src/defaults.rs",
     [("found.push((path.clone(), VALUE_DEFAULT));", "found.push((path.clone(), VALUE_AUTHORED));")]),
    ("rust", "the fields of a present composite are not defaulted", "src/defaults.rs",
     [("(ShapeType::Composite(fields), _) => composite(value, fields, a, path, found, unwritable),", "(ShapeType::Composite(_), _) => {}")]),
    ("rust", "the fields of a list element are not defaulted", "src/defaults.rs",
     [("composite(rec, &case.fields, a, path, found, unwritable);", "let _ = (&case, &rec);")]),
    ("rust", "a document with violations is defaulted anyway", "src/defaults.rs",
     [("(Some(doc), true) => doc,", "(Some(doc), _) => doc,")]),
    ("rust", "a default on a node whose Role does not take one is accepted", "src/role.rs",
     [("self.authority == \"config\" && self.structural.is_empty()", "true")]),
    ("rust", "an unknown short Role is read as config", "src/role.rs",
     [("        _ => return None,", "        _ => (\"config\", vec![], None),")]),
    ("rust", "a default that is not a valid value of its node is accepted", "src/schema.rs",
     [("if !default_fits(&shape, d) {", "if false {")]),
    ("go", "a present field is overwritten by its default", "defaults.go",
     [("\t\t\td.record(fp, ValueOriginAuthored)\n\t\t\td.visit(value, f.Shape, fp)", "\t\t\tif f.Shape.Default != nil {\n\t\t\t\t*value = *cloneTree(f.Shape.Default)\n\t\t\t}\n\t\t\td.record(fp, ValueOriginAuthored)\n\t\t\td.visit(value, f.Shape, fp)")]),
    ("go", "an inserted default is reported as authored", "defaults.go",
     [("d.record(fp, ValueOriginDefault)", "d.record(fp, ValueOriginAuthored)")]),
    ("go", "the fields of a present composite are not defaulted", "defaults.go",
     [("\tcase shape.Kind == ShapeComposite:\n\t\td.composite(value, shape.Fields, path)", "\tcase shape.Kind == ShapeComposite:")]),
    ("go", "the fields of a list element are not defaulted", "defaults.go",
     [("d.composite(rec, c.Fields, append(slices.Clone(path), seg{item: id, isItem: true}))", "_, _, _ = rec, c, id")]),
    ("go", "a document with violations is defaulted anyway", "defaults.go",
     [("if len(v.Violations) > 0 || doc == nil {", "if doc == nil {")]),
    ("go", "a default on a node whose Role does not take one is accepted", "role.go",
     [("return r.authority == \"config\" && len(r.structural) == 0", "return true")]),
    ("go", "an unknown short Role is read as config", "role.go",
     [("\t\treturn roleAxes{}, schemaErr(SchemaInvalid, path, \"%q is not a Role (reference has no short form and must be written out)\", r.Str)", "\t\treturn roleAxes{authority: \"config\"}, nil")]),
    ("go", "a default that is not a valid value of its node is accepted", "schema.go",
     [("!defaultFits(s, declared)", "false")]),
    # P3 third unit: normalize and denormalize (go/normalize.go, go/expand.go, rust/src/normalize.rs, rust/src/expand.rs).
    ("rust", "an inserted default is reported as authored", "src/expand.rs",
     [("self.origins.push((origin_path, VALUE_DEFAULT));", "self.origins.push((origin_path, VALUE_AUTHORED));")]),
    ("rust", "a default is inserted for a field that is not optional", "src/expand.rs",
     [("(Presence::Optional, Some(d)) if entry(n, name).is_none() => d,", "(_, Some(d)) if entry(n, name).is_none() => d,")]),
    ("rust", "a default is inserted over a value that is present", "src/expand.rs",
     [("(Presence::Optional, Some(d)) if entry(n, name).is_none() => d,", "(Presence::Optional, Some(d)) => d,")]),
    ("rust", "denormalize keeps the values the normalization made", "src/expand.rs",
     [("if !self.strip.is_empty() {", "if false {")]),
    ("rust", "renormalize gives the inserted default a provenance entry", "src/normalize.rs",
     [("        if kept == VALUE_DEFAULT {\n            continue; // the author did not write it: no provenance\n        }\n", "")]),
    ("go", "an inserted default is reported as authored", "expand.go",
     [("Violation{LogicalPath: logicalPath(fp), Code: ValueOriginDefault}", "Violation{LogicalPath: logicalPath(fp), Code: ValueOriginAuthored}")]),
    ("go", "a default is inserted for a field that is not optional", "expand.go",
     [("if f.Shape.Presence != Optional || f.Shape.Default == nil || lookup(n, f.Name) != nil {", "if f.Shape.Default == nil || lookup(n, f.Name) != nil {")]),
    ("go", "a default is inserted over a value that is present", "expand.go",
     [("if f.Shape.Presence != Optional || f.Shape.Default == nil || lookup(n, f.Name) != nil {", "if f.Shape.Presence != Optional || f.Shape.Default == nil {")]),
    ("go", "denormalize keeps the values the normalization made", "expand.go",
     [("if w.strip[logicalPath(append(slices.Clone(path), seg{field: e.Key}))] {", "if false {")]),
    ("go", "renormalize gives the inserted default a provenance entry", "normalize.go",
     [("\t\tif kept == ValueOriginDefault {\n\t\t\tcontinue // the author did not write it: no provenance\n\t\t}\n", "")]),
    # P3 fourth unit: PolicyResolution (go/resolve.go, go/policyresolution.go, rust/src/resolve.rs, rust/src/policyresolution.rs).
    ("rust", "the reset barrier is ignored: the system default follows a reset", "src/resolve.rs",
     [("    if barrier {\n        return EffectiveRule {", "    if false {\n        return EffectiveRule {")]),
    ("rust", "an unknown ancestor policy is treated as transparent", "src/resolve.rs",
     [("        if o.policy == ObjectPolicy::Unknown {\n            return unknown_rule(REASON_POLICY_CONTEXT_UNKNOWN);\n        }\n        if let Some(mut hit)", "        if let Some(mut hit)")]),
    ("rust", "a no-inherit carrier does not stop the field rules above it", "src/resolve.rs",
     [("Mode::NoInherit => field_open = false,", "Mode::NoInherit => field_open = true,")]),
    ("rust", "the modify system default is open", "src/resolve.rs",
     [("            RuleKind::Owner\n        } else {", "            RuleKind::Open\n        } else {")]),
    ("rust", "a policy-inherited rule keeps the layer of the ancestor's own rule", "src/resolve.rs",
     [("            hit.layer = LAYER_POLICY_INHERITED;\n", "")]),
    ("go", "the reset barrier is ignored: the system default follows a reset", "resolve.go",
     [("\tif barrier {\n\t\treturn EffectiveRule{Kind: RuleNone", "\tif barrier && false {\n\t\treturn EffectiveRule{Kind: RuleNone")]),
    ("go", "an unknown ancestor policy is treated as transparent", "resolve.go",
     [("\t\to = next\n\t\tif o.Policy.State == PolicyUnknown {\n\t\t\treturn unknownRule(ReasonPolicyContextUnknown)\n\t\t}\n", "\t\to = next\n")]),
    ("go", "a no-inherit carrier does not stop the field rules above it", "resolve.go",
     [("\t\tcase ModeNoInherit:\n\t\t\tfieldOpen = false", "\t\tcase ModeNoInherit:\n\t\t\tfieldOpen = true")]),
    ("go", "the modify system default is open", "resolve.go",
     [("return EffectiveRule{Kind: RuleOwner, Layer: LayerSystem}", "return EffectiveRule{Kind: RuleOpen, Layer: LayerSystem}")]),
    ("go", "a policy-inherited rule keeps the layer of the ancestor's own rule", "resolve.go",
     [("\t\t\thit.Layer = LayerPolicyInherited\n", "")]),
    ("go", "a surface rule of another surface applies", "resolve.go",
     [("if sr.Surface == surface && r != nil {", "if r != nil {")]),
    ("go", "an explicit empty field rule is read as no rule", "resolve.go",
     [("\tif field != nil {\n\t\treturn fromRule(field, LayerField, fieldPath)", "\tif field != nil && (field.Symbol != \"\" || len(field.Patterns) > 0) {\n\t\treturn fromRule(field, LayerField, fieldPath)")]),
    ("rust", "a list element does not take the rule of its list field", "src/policyresolution.rs",
     [("self.child(&etext, parent, mode, &access, &modify, text);", "self.child(&etext, parent, mode, &None, &None, text);")]),
    ("go", "a list element does not take the rule of its list field", "policyresolution.go",
     [("w.child(etext, parent, mode, access, modify, text)", "w.child(etext, parent, mode, nil, nil, text)")]),
    # P3 closing round: the composed operation (go/normalizewithpolicy.go, rust/src/composed.rs).
    ("rust", "the composed result ignores the objects the caller marked unknown", "src/composed.rs",
     [("        g,\n        unknown,\n    )?;", "        g,\n        &[],\n    )?;")]),
    ("rust", "the composed result does not name its schema", "src/composed.rs",
     [("schema_digest: c.digest.clone()", "schema_digest: String::new()")]),
    ("go", "the composed result ignores the objects the caller marked unknown", "normalizewithpolicy.go",
     [("c.Surface, a, g, unknown)", "c.Surface, a, g, nil)")]),
    ("go", "the composed result does not name its schema", "normalizewithpolicy.go",
     [("SchemaDigest: c.Digest}", "SchemaDigest: \"\"}")]),
    # P3b step E2: source_span (rust/src/sourcespan.rs, go/sourcespan.go).
    ("rust", "the span of a single-quoted scalar stops before its closing quote", "src/sourcespan.rs",
     [("Some(b'\\'') => return Ok((i + 1, SpanStyle::SingleQuoted)),", "Some(b'\\'') => return Ok((i, SpanStyle::SingleQuoted)),")]),
    ("rust", "a doubled quote ends a single-quoted scalar", "src/sourcespan.rs",
     [("Some(b'\\'') if at(i + 1) == Some(b'\\'') => i += 2,", "Some(b'\\'') if false => i += 2,")]),
    ("rust", "an escape in a double-quoted scalar skips nothing", "src/sourcespan.rs",
     [("Some(b'\\\\') => i += 2,", "Some(b'\\\\') => i += 1,")]),
    ("rust", "a plain scalar keeps its trailing spaces", "src/sourcespan.rs",
     [("start + line[..cut].trim_end_matches(' ').len()", "start + line[..cut].len()")]),
    ("rust", "a comment is part of a plain scalar", "src/sourcespan.rs",
     [("let cut = line.find(\" #\").unwrap_or(line.len());", "let cut = line.len();")]),
    ("rust", "a scalar in a flow collection is not recognised", "src/sourcespan.rs",
     [("matches!(bytes.get(p.offset), Some(b'{' | b'['))", "false")]),
    ("rust", "the read-back of the span is skipped", "src/sourcespan.rs",
     [("read_back(token, node, style).map_err(|code| failure(code, node.pos, canonical))?;", "let _ = (token, style);")]),
    ("rust", "a block scalar is scanned as a plain one", "src/sourcespan.rs",
     [("Some(b'|' | b'>') => Err(\"EDIT_BLOCK_SCALAR\"),", "Some(b'|' | b'>') if false => Err(\"EDIT_BLOCK_SCALAR\"),")]),
    ("rust", "a list element is found by its place, not by its identity", "src/sourcespan.rs",
     [(".filter(|(_, id)| id == comps)", ".filter(|_| true)")]),
    ("rust", "an expanded wrapper is not looked through", "src/sourcespan.rs",
     [("Ok(Class::Expanded) => (entry(n, \"value\"), true),", "Ok(Class::Expanded) => (Some(n), false),")]),
    ("rust", "a plain scalar that continues on the next line is called a defect", "src/sourcespan.rs",
     [("            if s.strip_prefix(token).is_some_and(|rest| !rest.is_empty()) =>", "            if false && s.strip_prefix(token).is_some_and(|rest| !rest.is_empty()) =>")]),
    ("go", "the span of a single-quoted scalar stops before its closing quote", "sourcespan.go",
     [("return i + 1, SpanSingleQuoted, \"\"", "return i, SpanSingleQuoted, \"\"")]),
    ("go", "a doubled quote ends a single-quoted scalar", "sourcespan.go",
     [("case c == '\\'' && at(i+1) == '\\'':", "case false && c == '\\'' && at(i+1) == '\\'':")]),
    ("go", "an escape in a double-quoted scalar skips nothing", "sourcespan.go",
     [("case c == '\\\\':\n\t\t\t\ti += 2", "case c == '\\\\':\n\t\t\t\ti += 1")]),
    ("go", "a plain scalar keeps its trailing spaces", "sourcespan.go",
     [("strings.TrimRight(line, \" \")", "line")]),
    ("go", "a comment is part of a plain scalar", "sourcespan.go",
     [("line = line[:strings.Index(line+\" #\", \" #\")]", "line = line[:len(line)]")]),
    ("go", "a scalar in a flow collection is not recognised", "sourcespan.go",
     [("if strings.IndexAny(text[f.container.Pos.Offset:], \"{[\") == 0 {", "if false {")]),
    ("go", "the read-back of the span is skipped", "sourcespan.go",
     [("if code := readBack(text[start:end], n, style); code != \"\" {", "if code := \"\"; code != \"\" {")]),
    ("go", "a block scalar is scanned as a plain one", "sourcespan.go",
     [("case '|', '>':\n\t\treturn 0, \"\", \"EDIT_BLOCK_SCALAR\"", "case '|', '>':\n\t\tif false {\n\t\t\treturn 0, \"\", \"EDIT_BLOCK_SCALAR\"\n\t\t}")]),
    ("go", "a list element is found by its place, not by its identity", "sourcespan.go",
     [("return code == \"\" && slices.Equal(got, id)", "return code == \"\" && (slices.Equal(got, id) || true)")]),
    ("go", "an expanded wrapper is not looked through", "sourcespan.go",
     [("if class, viol := classify(n, a); viol == nil && class == ClassExpanded {", "if class, viol := classify(n, a); viol == nil && class == ClassExpanded && false {")]),
    ("go", "a plain scalar that continues on the next line is called a defect", "sourcespan.go",
     [("ok && rest != \"\" {", "ok && rest != \"\" && false {")]),
    ("rust", "a default is inserted at a position whose logical path cannot be written", "src/expand.rs",
     [("            if let Err(e) = path_text(path) {\n                path.pop();\n                return Err(e);\n            }\n", "")]),
    ("go", "a default is inserted at a position whose logical path cannot be written", "expand.go",
     [("if _, err := pathText(fp); err != nil { // the origin of an inserted value", "if _, err := pathText(fp); err != nil && false { // the origin of an inserted value")]),
    ("rust", "the object's own default is cut by its own reset", "src/resolve.rs",
     [("    if let Some(hit) = local_rule(cur, surface, op) {\n        return hit;", "    if let Some(hit) = local_rule(cur, surface, op).filter(|_| policy_mode(cur) != Some(Mode::Reset)) {\n        return hit;")]),
    ("go", "the object's own default is cut by its own reset", "resolve.go",
     [("if hit, ok := localRule(cur, surface, op); ok {\n\t\treturn hit", "if hit, ok := localRule(cur, surface, op); ok && policyMode(cur) != ModeReset {\n\t\treturn hit")]),
    ("go", "integer 0 is read as no-inherit (0 coerced to false)", "access.go",
     [("case n.Kind == KindInt && n.Int == 0:\n\t\t\treturn ModeReset, nil", "case n.Kind == KindInt && n.Int == 0:\n\t\t\treturn ModeNoInherit, nil")]),
    ("go", "a missing version silently falls back to grammar 1", "access.go",
     [("func isMissing(v *Node) bool { return v == nil || v.Kind == KindNull }", "func isMissing(v *Node) bool { return false }"),
      ("func checked(v *Node) (int64, error) {", "func checked(v *Node) (int64, error) {\n\tif v == nil || v.Kind == KindNull {\n\t\treturn 1, nil\n\t}")]),
    ("go", "the pinned schema is used without verifying its digest", "access.go",
     [("if SHA256Hex(schema) != expectedSHA256 {", "if false {")]),
    ("go", "a string without '=' is a CertPattern", "access.go",
     [("if !ok || !isAttr(attr) || strings.Contains(value, \"=\") {", "if !ok {\n\t\t\tcontinue\n\t\t}\n\t\tif !isAttr(attr) || strings.Contains(value, \"=\") {")]),
    ("go", "the empty list is read as the symbol inherit", "access.go",
     [("return Rule{Patterns: patterns}, nil", "if len(patterns) == 0 {\n\t\t\treturn Rule{Symbol: true}, nil\n\t\t}\n\t\treturn Rule{Patterns: patterns}, nil")]),
    ("go", "grammar 2 accepts the legacy boolean spelling", "access.go",
     [("\treturn ModeInvalid, invalid(\"LEGACY_INHERIT_IN_GRAMMAR_2\", n)", "\treturn ModeInherit, nil")]),
    ("go", "a violation drops its source position", "access.go",
     [("Location: loc(n.Pos)}", "Location: Location{}}")]),
    ("go", "an unbound or unknown Grammar is read as grammar 2", "access.go",
     [("\tif !isSupported(g.version) {\n\t\treturn ModeInvalid, invalid(", "\tif false {\n\t\treturn ModeInvalid, invalid(")]),
    ("go", "an unbound or unknown Grammar is written as grammar 2", "access.go",
     [("\tif !isSupported(g.version) {\n\t\treturn nil, binding(", "\tif false {\n\t\treturn nil, binding(")]),
    ("go", "an invalid Mode is written as reset", "access.go",
     [("\tif m < ModeInherit || m > ModeReset {\n\t\treturn nil, unsupported(", "\tif false {\n\t\treturn nil, unsupported(")]),
    ("go", "a caller can change the supported versions", "access.go",
     [("func SupportedGrammars() []int64 { return slices.Clone(supportedGrammars) }", "func SupportedGrammars() []int64 { return supportedGrammars }")]),
    # Parser parity (docs/P1-SUPPORTED-SUBSET.md, "Parser parity"): the shared regression corpus must turn red.
    ("rust", "the last non-syntax error is reported, not the first", "src/parse.rs",
     [("        self.deferred.get_or_insert(e);", "        *self.deferred = Some(e);")]),
    ("rust", "a block sequence is located at its first item", "src/parse.rs",
     [("                let at = self.table.dash(at);", "                let at = at;")]),
    ("rust", "a block scalar is located at its first content line", "src/parse.rs",
     [("let pos = if block { self.table.indicator(at) } else { at };", "let pos = at;")]),
    ("rust", "a word starting with ! is not refused", "src/parse.rs",
     [("'!' => return Err(Error::Unsupported(\"tag\".into())),", "'!' => {}")]),
    ("rust", "a tab is accepted", "src/parse.rs",
     [("    if body.contains('\\t') {", "    if false {")]),
    ("rust", "a block scalar without content is accepted", "src/parse.rs",
     [("if value.chars().all(|c| c == '\\n') {", "if false {")]),
    ("rust", "an empty implicit key is read as ~", "src/parse.rs",
     [("                        if !has_text {", "                        if false {")]),
    ("go", "a tab is accepted", "parse.go",
     [("\tif strings.Contains(body, \"\\t\") {", "\tif false {")]),
    ("go", "a word starting with ! is not refused", "parse.go",
     [("\t\t\tcase ch == '!':", "\t\t\tcase false:")]),
    ("go", "a block scalar without content is accepted", "parse.go",
     [("\t\t\tif strings.Trim(n.Value, \"\\n\") == \"\" {", "\t\t\tif false {")]),
    ("rust", "the content of a block scalar is scanned for markers", "src/parse.rs",
     [("                if blank || indent > parent {", "                if false {")]),
    ("rust", "an indented marker inside a quoted scalar is a document marker", "src/parse.rs",
     [("            indent == 0\n        } else {", "            true\n        } else {")]),
    ("go", "the content of a block scalar is scanned for markers", "parse.go",
     [("\t\t\t\tif blank || indent > block {", "\t\t\t\tif (blank || indent > block) && false {")]),
    ("go", "an indented marker inside a quoted scalar is a document marker", "parse.go",
     [("\t\t\trefused = indent == 0", "\t\t\trefused = true")]),
    ("go", "a syntax error in a second document is reported as a second document", "parse.go",
     [("\tcase !errors.Is(err, io.EOF):\n\t\treturn nil, &Error{Kind: ErrSyntax, Detail: err.Error()}", "\tcase !errors.Is(err, io.EOF):\n\t\treturn nil, unsupported(\"more than one document\")")]),
    # Schema compile (ADR 0002, docs/P1-SUPPORTED-SUBSET.md "Schema-bound validation"): the shared schema cases and the
    # real registry schema must turn red on each.
    ("rust", "a schema is compiled without verifying its digest", "src/schema.rs",
     [("if sha256_hex(input.bytes) != input.sha256 {", "if false {")]),
    ("rust", "a default on a field that does not say optional is accepted", "src/schema.rs",
     [("if presence == Presence::Mandatory {", "if false {")]),
    ("rust", "an unknown grammar pin is read as a known one", "src/schema.rs",
     [("None => return Err(Error::Binding(GRAMMAR_PIN_UNKNOWN)),", "None => profiles[0].1,")]),
    ("rust", "item_choice is read under a grammar that does not define it", "src/schema.rs",
     [("if !self.profile.item_choice {", "if false {")]),
    ("rust", "a list without a key is accepted", "src/schema.rs",
     [("if keys.is_empty() {", "if keys.is_empty() && false {"), ("None if keys.len() == 1 => keys,", "None if keys.len() <= 1 => keys,")]),
    ("rust", "the scalar types number and bytes are read as unknown", "src/schema.rs",
     [("\"number\" | \"bytes\" => Err(problem(", "\"number_\" | \"bytes_\" => Err(problem(")]),
    ("rust", "an unpinned reference is accepted", "src/schema.rs",
     [("Value::Str(s) if is_pinned_ref(s) => Ok(()),", "Value::Str(_) => Ok(()),")]),
    ("go", "a schema is compiled without verifying its digest", "schema.go",
     [("if SHA256Hex(in.Bytes) != in.SHA256 {", "if false {")]),
    ("go", "a default on a field that does not say optional is accepted", "schema.go",
     [("if s.Presence == Mandatory {", "if false {")]),
    ("go", "an unknown grammar pin is read as a known one", "schema.go",
     [("profile, ok := profiles[in.Grammar]\n\tif !ok {", "profile, ok := profiles[in.Grammar]\n\tif !ok && false {")]),
    ("go", "item_choice is read under a grammar that does not define it", "schema.go",
     [("if !c.profile.ItemChoice {", "if false {")]),
    ("go", "a list without a key is accepted", "schema.go",
     [("case len(keys) == 0:\n\t\treturn nil, schemaErr(SchemaInvalid, path, \"a list needs a key: one item_fields entry with role: key\")", "case len(keys) == 0:\n\t\torder = keys")]),
    ("go", "the scalar types number and bytes are read as unknown", "schema.go",
     [("if v.Str == \"number\" || v.Str == \"bytes\" {", "if false {")]),
    ("go", "an unpinned reference is accepted", "schema.go",
     [("if v.Kind != KindStr || !pinnedRef.MatchString(v.Str) {", "if false {")]),
    # List elements (ADR 0002/0006): identity from the declared key, case selection, duplicates, canonical paths.
    ("rust", "a duplicate identity is not reported", "src/validate.rs",
     [("if seen.contains(&seg) && !reported.contains(&seg) {", "if false {")]),
    ("rust", "an item_choice record that matches several cases takes the first", "src/listitem.rs",
     [("if found.is_some() {", "if false {")]),
    ("rust", "an Access wrapper around a key value is not looked through", "src/listitem.rs",
     [("Ok(Class::Expanded) => entry(n, \"value\"),", "Ok(Class::Expanded) => Some(n),")]),
    ("rust", "a boolean key is given an encoding", "src/listitem.rs",
     [("Some(ShapeType::Scalar(t)) if *t != ScalarType::Boolean => *t,", "Some(ShapeType::Scalar(t)) => *t,")]),
    ("go", "a duplicate identity is not reported", "validate.go",
     [("if seen[k] == 2 {", "if false {")]),
    ("go", "an item_choice record that matches several cases takes the first", "listitem.go",
     [("if c != nil {", "if false {")]),
    ("go", "an Access wrapper around a key value is not looked through", "listitem.go",
     [("if class, _ := classify(n, a); class == ClassExpanded {", "if false {")]),
    ("go", "a boolean key is given an encoding", "listitem.go",
     [("shape.Kind != ShapeScalar || shape.Scalar == ScalarBoolean", "shape.Kind != ShapeScalar")]),
    ("go", "integer identities are ordered as text", "recognize.go",
     [("fmt.Sprintf(\"1%020d\", uint64(c.Int)^(1<<63))", "fmt.Sprintf(\"1%d\", c.Int)")]),
    ("go", "a key of the wrong type is anchored at the record, not at the value", "listitem.go",
     [("default:\n\t\t\treturn nil, nil, CodeKeyType, raw", "default:\n\t\t\treturn nil, nil, CodeKeyType, rec")]),
    ("rust", "a key of the wrong type is anchored at the record, not at the value", "src/listitem.rs",
     [("            _ => return Err((KEY_TYPE, raw)),", "            _ => return Err((KEY_TYPE, rec)),")]),
    ("go", "a duplicate identity is anchored at the first element, not the second", "validate.go",
     [("if seen[k] == 2 {", "if seen[k] == 1 {")]),
    # Expand and collapse (ADR 0001 "Round trip", ADR 0010): the law, the explicit settings, the authored form.
    ("rust", "an explicit policy setting is dropped by collapse", "src/expand.rs",
     [("if !entry(n, k).is_some_and(|got| got.same(&d)) {", "if false {")]),
    ("rust", "an authored expanded position is shortened", "src/expand.rs",
     [("|| authored_expanded || basis == BASIS_BINDING", "|| basis == BASIS_BINDING")]),
    ("rust", "a binding_attested position is shortened", "src/expand.rs",
     [("|| authored_expanded || basis == BASIS_BINDING", "|| authored_expanded")]),
    ("rust", "a value that would be read as expanded is shortened", "src/expand.rs",
     [("Some(value) if matches!(classify(value, a), Ok(Class::Short)) => value.clone(),", "Some(value) => value.clone(),")]),
    ("go", "an explicit policy setting is dropped by collapse", "expand.go",
     [("if got := entry(n, k); got == nil || !got.Same(d) {", "if got := entry(n, k); (got == nil || !got.Same(d)) && false {")]),
    ("go", "an authored expanded position is shortened", "expand.go",
     [("if authoredExpanded || basis == BasisBinding {", "if basis == BasisBinding {")]),
    ("go", "a binding_attested position is shortened", "expand.go",
     [("if authoredExpanded || basis == BasisBinding {", "if authoredExpanded {")]),
    ("go", "a value that would be read as expanded is shortened", "expand.go",
     [("if class, _ := classify(value, a); class != ClassShort {", "if false {")]),
    # The two contracts of the P2 architecture review: an undeclared key is refused, a contradictory context is refused.
    ("rust", "an undeclared key passes through expand and collapse", "src/expand.rs",
     [("                None => {\n                    let mut p = path.clone();", "                None => {\n                    out.push(Entry { key: e.key.clone(), key_pos: e.key_pos, value: e.value.clone() });\n                    continue;\n                }\n                Some(_) if false => {\n                    let mut p = path.clone();")]),
    ("rust", "a contradictory context is collapsed anyway", "src/expand.rs",
     [("    ctx.check(shape)?;\n    let mut w = Walker::plain(\n        a,\n        g,\n        prov.clone(),", "    let mut w = Walker::plain(\n        a,\n        g,\n        prov.clone(),")]),
    ("rust", "a position outside the attested set is treated as attested", "src/expand.rs",
     [("let basis = if self.attested.contains(&text) {", "let basis = if self.attested.is_empty() || self.attested.contains(&text) {")]),
    ("go", "an undeclared key passes through expand and collapse", "expand.go",
     [("\t\tif i < 0 {\n", "\t\tif i < 0 {\n\t\t\tout.Entries = append(out.Entries, Entry{Key: e.Key, KeyPos: e.KeyPos, Value: cloneTree(e.Value)})\n\t\t\tcontinue\n\t\t}\n\t\tif false {\n")]),
    ("go", "a contradictory context is collapsed anyway", "expand.go",
     [("\tif err := ctx.check(shape); err != nil {\n\t\treturn nil, err\n\t}", "\tif false {\n\t\treturn nil, nil\n\t}")]),
    ("go", "a position outside the attested set is treated as attested", "expand.go",
     [("\tif w.attested[text] {", "\tif len(w.attested) == 0 || w.attested[text] {")]),
    # Paths as text and the attested positions (ADR 0006, ADR 0010): resolved against the schema, strict, with the right location.
    ("rust", "an attested position that the schema does not resolve is accepted", "src/expand.rs",
     [("            let resolved =\n                resolve_path(root, &segs).map_err(|e| Error::Contract(vec![e.violation()]))?;", "            let resolved = resolve_path(root, &segs).unwrap_or(crate::pathtext::Resolved { target: \"scalar\", primitive: true });")]),
    ("rust", "the context error has an unavailable source location", "src/expand.rs",
     [("        location: Location::NotApplicable,", "        location: Location::Unavailable,")]),
    ("rust", "a path integer with a leading zero is accepted", "src/pathtext.rs",
     [("if plus || (digits.len() > 1 && digits.starts_with('0')) || (neg && digits == \"0\") {", "if plus || (neg && digits == \"0\") {")]),
    ("go", "an attested position that the schema does not resolve is accepted", "expand.go",
     [("\t\tres, perr := ResolvePath(root, segs)\n\t\tif perr != nil {", "\t\tres, perr := ResolvePath(root, segs)\n\t\tif perr != nil {\n\t\t\tres = Resolved{Target: \"scalar\", Primitive: true}\n\t\t\tperr = nil\n\t\t}\n\t\tif false {")]),
    ("go", "the context error has an unavailable source location", "expand.go",
     [("Phase: PhaseValidate, Location: Location{NotApplicable: true}}}}", "Phase: PhaseValidate}}}")]),
    ("go", "a path integer with a leading zero is accepted", "pathtext.go",
     [("case plus, len(digits) > 1 && digits[0] == '0', neg && digits == \"0\":", "case plus, neg && digits == \"0\":")]),
    ("rust", "the path_offset of a path_parse error is dropped from the machine response", "src/pathtext.rs",
     [("path_offset: (self.phase == Phase::PathParse).then_some(self.offset),", "path_offset: None,")]),
    ("rust", "a path_offset is invented for another phase", "src/pathtext.rs",
     [("path_offset: (self.phase == Phase::PathParse).then_some(self.offset),", "path_offset: Some(self.offset),")]),
    ("go", "the path_offset of a path_parse error is dropped from the machine response", "pathtext.go",
     [("\tif e.Phase == PhasePathParse {", "\tif false {")]),
    ("go", "a path_offset is invented for another phase", "pathtext.go",
     [("\tif e.Phase == PhasePathParse {", "\tif true {")]),
    # P3b, step E3: edit_scalar (go/editscalar.go, rust/src/editscalar.rs). Each of the vectors, the properties or the edge tests must turn red on it.
    ("rust", "a no-op rewrites the token", "src/editscalar.rs",
     [("    if node.same(value) {", "    if false {")]),
    ("rust", "the single quote is not doubled in a single-quoted scalar", "src/editscalar.rs",
     [("format!(\"'{}'\", s.replace('\\'', \"''\"))", "format!(\"'{}'\", s)")]),
    ("rust", "the backslash and the quote are not escaped in a double-quoted scalar", "src/editscalar.rs",
     [("format!(\"\\\"{}\\\"\", s.replace('\\\\', \"\\\\\\\\\").replace('\"', \"\\\\\\\"\"))", "format!(\"\\\"{}\\\"\", s)")]),
    ("rust", "the kind of the new value is not checked", "src/editscalar.rs",
     [("    if !carries(found.shape, value) {", "    if false {")]),
    ("rust", "a control character is written", "src/editscalar.rs",
     [("    if !representable(value) {", "    if false {")]),
    ("rust", "a string that needs quotes is written plain", "src/editscalar.rs",
     [("            _ => s.filter(|s| plain_safe(s)).cloned(),", "            _ => s.cloned(),")]),
    ("rust", "the result is not validated", "src/editscalar.rs",
     [("        Ok(parsed) if refuse_invalid(&parsed, root, a).is_ok() => {", "        Ok(parsed) => {")]),
    ("rust", "the document is not validated before the edit", "src/editscalar.rs",
     [("    let doc = parse(text)?;\n    refuse_invalid(&doc, root, a)?;\n", "    let doc = parse(text)?;\n")]),
    ("rust", "null is written in another spelling", "src/editscalar.rs",
     [("Value::Null => Some(\"null\".to_string()),", "Value::Null => Some(\"~\".to_string()),")]),
    ("rust", "null is accepted by a position that is not nullable", "src/editscalar.rs",
     [("        (Value::Null, _) => shape.nullable,", "        (Value::Null, _) => true,")]),
    ("rust", "the booleans are written capitalised", "src/editscalar.rs",
     [("            Value::Bool(b) => Some(b.to_string()),", "            Value::Bool(b) => Some(if *b { \"True\" } else { \"False\" }.to_string()),")]),
    ("rust", "an edit of a parsed value is not refused for its missing text", "src/editscalar.rs",
     [("        Ok(_) => failure(\"EDIT_NO_SOURCE_TEXT\", None, path),", "        Ok(_) => failure(\"EDIT_NOT_A_SCALAR\", None, path),")]),
    ("go", "a no-op rewrites the token", "editscalar.go",
     [("\tif n.Same(value) {", "\tif false {")]),
    ("go", "the single quote is not doubled in a single-quoted scalar", "editscalar.go",
     [("return \"'\" + strings.ReplaceAll(v.Str, \"'\", \"''\") + \"'\", v.Kind == KindStr", "return \"'\" + v.Str + \"'\", v.Kind == KindStr")]),
    ("go", "the backslash and the quote are not escaped in a double-quoted scalar", "editscalar.go",
     [("return `\"` + strings.NewReplacer(`\\`, `\\\\`, `\"`, `\\\"`).Replace(v.Str) + `\"`, v.Kind == KindStr", "return `\"` + v.Str + `\"`, v.Kind == KindStr")]),
    ("go", "the kind of the new value is not checked", "editscalar.go",
     [("\tif !carries(f.shape, value) {", "\tif false {")]),
    ("go", "a control character is written", "editscalar.go",
     [("\tif value == nil || !representable(value) {", "\tif value == nil {")]),
    ("go", "a string that needs quotes is written plain", "editscalar.go",
     [("\treturn v.Str, editPlainSafe(v.Str)", "\treturn v.Str, true")]),
    ("go", "the result is not validated", "editscalar.go",
     [("\tif err != nil || refuseInvalid(parsed, root, a) != nil {", "\tif err != nil {")]),
    ("go", "the document is not validated before the edit", "editscalar.go",
     [("\tdoc, err := Parse(text)\n\tif err != nil {\n\t\treturn EditResult{}, err\n\t}\n\tif err := refuseInvalid(doc, root, a); err != nil {\n\t\treturn EditResult{}, err\n\t}\n", "\tdoc, err := Parse(text)\n\tif err != nil {\n\t\treturn EditResult{}, err\n\t}\n")]),
    ("go", "null is written in another spelling", "editscalar.go",
     [("\t\treturn \"null\", true", "\t\treturn \"~\", true")]),
    ("go", "null is accepted by a position that is not nullable", "editscalar.go",
     [("\tcase KindNull:\n\t\treturn shape.Nullable", "\tcase KindNull:\n\t\treturn true")]),
    ("go", "the booleans are written capitalised", "editscalar.go",
     [("\t\treturn strconv.FormatBool(v.Bool), true", "\t\treturn strings.ToUpper(strconv.FormatBool(v.Bool)[:1]) + strconv.FormatBool(v.Bool)[1:], true")]),
    ("go", "an edit of a parsed value is not refused for its missing text", "editscalar.go",
     [("\treturn editFailure(\"EDIT_NO_SOURCE_TEXT\", nil, path)", "\treturn editFailure(\"EDIT_NOT_A_SCALAR\", nil, path)")]),
    # P3b, step E4: the internal assertion of edit_scalar (EDIT_SPAN_MISMATCH and EDIT_INVALID_RESULT: verify_edit / verifyEdit and the expected tree). No input of the
    # public function can make it fail on purpose, so each condition of it is removed in turn and the direct tests of the check must turn red.
    ("rust", "the check of the bytes before the span is skipped", "src/editscalar.rs",
     [("    if !(out.starts_with(&text[..span.start]) && out.ends_with(&text[span.end..])) {", "    if !(out.ends_with(&text[span.end..])) {")]),
    ("rust", "the check of the bytes after the span is skipped", "src/editscalar.rs",
     [("    if !(out.starts_with(&text[..span.start]) && out.ends_with(&text[span.end..])) {", "    if !(out.starts_with(&text[..span.start])) {")]),
    ("rust", "a result that does not parse or validate is accepted", "src/editscalar.rs",
     [("        _ => Some(\"EDIT_INVALID_RESULT\"),", "        _ => None,")]),
    ("rust", "the tree of the result is not compared with the expected one", "src/editscalar.rs",
     [("            (!parsed.same(want)).then_some(\"EDIT_SPAN_MISMATCH\")", "            None")]),
    ("rust", "the expected tree replaces every scalar, not the one that was edited", "src/editscalar.rs",
     [("        _ if n.pos.is_some_and(|p| p.offset == offset) => value.value.clone(),", "        _ if n.pos.is_some() => value.value.clone(),")]),
    ("go", "the check of the bytes before the span is skipped", "editscalar.go",
     [("\tif !strings.HasPrefix(out, text[:sp.Start]) || !strings.HasSuffix(out, text[sp.End:]) {", "\tif !strings.HasSuffix(out, text[sp.End:]) {")]),
    ("go", "the check of the bytes after the span is skipped", "editscalar.go",
     [("\tif !strings.HasPrefix(out, text[:sp.Start]) || !strings.HasSuffix(out, text[sp.End:]) {", "\tif !strings.HasPrefix(out, text[:sp.Start]) {")]),
    ("go", "a result that does not validate is accepted", "editscalar.go",
     [("\tif err != nil || refuseInvalid(parsed, root, a) != nil {", "\tif err != nil {")]),
    ("go", "the tree of the result is not compared with the expected one", "editscalar.go",
     [("\tif !parsed.Same(want) {", "\tif false {")]),
    ("go", "the expected tree replaces every scalar, not the one that was edited", "editscalar.go",
     [("\t\tif n.Pos != nil && n.Pos.Offset == offset {", "\t\tif n.Pos != nil {")]),
    # P4, steps D1-D3 and D3b: diff (go/diff.go, go/diffcompare.go, go/diffcanonical.go, rust/src/diff.rs). The vectors and the unit tests must turn red on each, and
    # tools/parity/diff_proof.py must see each in the Rust/Go differential (every entry whose name starts with "diff:" is planted there too).
    ("rust", "diff: the events at one path are in the wrong order of kinds", "src/diff.rs",
     [(".then(x.kind.cmp(&y.kind))", ".then(y.kind.cmp(&x.kind))")]),
    ("rust", "diff: the elements of a set change are not sorted", "src/diff.rs",
     [("    let added = sorted_scalars(b)\n        .into_iter()", "    let added = match &b.value { Value::Seq(v) => v.clone(), _ => Vec::new() }\n        .into_iter()")]),
    ("rust", "diff: a representation change is never reported", "src/diff.rs",
     [("if was != is {", "if was != was {")]),
    ("rust", "diff: a change of the access declaration is not seen", "src/diff.rs",
     [(".filter(|k| *k != VALUE)\n", ".filter(|k| *k != VALUE && *k != \"access\")\n")]),
    ("rust", "diff: a value of another kind that looks the same is not a change", "src/diff.rs",
     [("if !va.same(vb) {\n                    let e = self.emit(EventKind::ValueChange", "if std::mem::discriminant(&va.value) == std::mem::discriminant(&vb.value) && !va.same(vb) {\n                    let e = self.emit(EventKind::ValueChange")]),
    ("rust", "diff: list elements are paired with any element that does not sort before them", "src/diff.rs",
     [("match eb.iter().find(|y| x.compare(y) == Ordering::Equal) {", "match eb.iter().find(|y| x.compare(y) != Ordering::Greater) {")]),
    ("rust", "diff: a place that only B has is not reported", "src/diff.rs",
     [("(None, Some(sb)) => self.gone(EventKind::Added, sb, path),", "(None, Some(_)) => Ok(()),")]),
    ("rust", "diff: the scope entries inside another entry are kept", "src/diff.rs",
     [("o.len() < e.len() && e.starts_with(o));", "o.len() < e.len() && false);")]),
    ("rust", "diff: the unsupported number of B is not looked for", "src/diff.rs",
     [("for (view, operand) in [(va, \"a\"), (vb, \"b\")] {", "for (view, operand) in [(va, \"a\")] {")]),
    ("rust", "diff: the elements of an appearing set are not sorted", "src/diff.rs",
     [("ShapeType::Set { .. } => Node::new(Value::Seq(sorted_scalars(v))),", "ShapeType::Set { .. } => v.clone(),")]),
    ("rust", "diff: the elements of an appearing list are not sorted by identity", "src/diff.rs",
     [("els.sort_by(Element::compare);", "")]),
    ("rust", "diff: an identity error is reported in the validate phase", "src/diff.rs",
     [("v.phase = Phase::PathOf;", "")]),
    ("rust", "diff: an error does not name its operand", "src/diff.rs",
     [("v.operand = Some(operand);", "")]),
    ("rust", "diff: the quote is not escaped in the canonical bytes", "src/diff.rs",
     [("'\"' => out.push_str(\"\\\\\\\"\"),", "'\"' => out.push('\"'),")]),
    ("rust", "diff: U+007F is escaped in the canonical bytes", "src/diff.rs",
     [("c if (c as u32) < 0x20 =>", "c if (c as u32) < 0x20 || c == '\\u{7f}' =>")]),
    ("rust", "diff: the keys of the canonical objects are not sorted", "src/diff.rs",
     [("sorted.sort_by(|x, y| x.key.as_bytes().cmp(y.key.as_bytes()));", "")]),
    ("go", "diff: the events at one path are in the wrong order of kinds", "diffcompare.go",
     [("strings.Compare(x.Kind, y.Kind))", "strings.Compare(y.Kind, x.Kind))")]),
    ("go", "diff: the elements of a set change are not sorted", "diffcompare.go",
     [("for _, x := range sortedScalars(b.Items) {", "for _, x := range b.Items {")]),
    ("go", "diff: a representation change is never reported", "diffcompare.go",
     [("; wa != wb {", "; wa != wa {")]),
    ("go", "diff: a change of the access declaration is not seen", "diffcompare.go",
     [("\t\tif k == valueKey {", "\t\tif k == valueKey || k == \"access\" {")]),
    ("go", "diff: a value of another kind that looks the same is not a change", "diffcompare.go",
     [("\t\tif !va.Same(vb) {\n\t\t\td.emit(DiffEvent{Kind: EventValueChange", "\t\tif va.Kind == vb.Kind && !va.Same(vb) {\n\t\t\td.emit(DiffEvent{Kind: EventValueChange")]),
    ("go", "diff: list elements are paired with any element that does not sort before them", "diffcompare.go",
     [("i := slices.IndexFunc(eb, func(y element) bool { return compareElements(x, y) == 0 })", "i := slices.IndexFunc(eb, func(y element) bool { return compareElements(x, y) <= 0 })")]),
    ("go", "diff: a place that only B has is not reported", "diffcompare.go",
     [("\t\treturn d.gone(EventAdded, sb, path)\n\t}", "\t\treturn nil\n\t}")]),
    ("go", "diff: the scope entries inside another entry are kept", "diff.go",
     [("return len(o) < len(e) && hasPrefix(e, o) })", "return len(o) < len(e) && false })")]),
    ("go", "diff: the unsupported number of B is not looked for", "diff.go",
     [("for i, v := range []DiffView{va, vb} {", "for i, v := range []DiffView{va} {")]),
    ("go", "diff: the elements of an appearing set are not sorted", "diffcompare.go",
     [("Items: sortedScalars(v.Items)}", "Items: v.Items}")]),
    ("go", "diff: the elements of an appearing list are not sorted by identity", "diffcompare.go",
     [("\t\tslices.SortStableFunc(els, compareElements)\n", "")]),
    ("go", "diff: an identity error is reported in the validate phase", "diff.go",
     [("\t\tv.Phase = PhasePathOf\n", "")]),
    ("go", "diff: an error does not name its operand", "diff.go",
     [("\tv.Operand = operand\n", "")]),
    ("go", "diff: the quote is not escaped in the canonical bytes", "diffcanonical.go",
     [("\t\t\tb.WriteString(`\\\"`)", "\t\t\tb.WriteByte('\"')")]),
    ("go", "diff: U+007F is escaped in the canonical bytes", "diffcanonical.go",
     [("case r < 0x20:", "case r < 0x20 || r == 0x7f:")]),
    ("go", "diff: the keys of the canonical objects are not sorted", "diffcanonical.go",
     [("\t\tslices.SortFunc(entries, func(x, y Entry) int { return strings.Compare(x.Key, y.Key) })\n", "")]),
    # P4, step P4-3: patch (go/patch.go, rust/src/patch.rs). The vectors and the unit tests must turn red on each, and tools/parity/patch_proof.py must see each in the
    # Rust/Go/model differential (every entry whose name starts with "patch:" is planted there too).
    ("rust", "patch: a value that is not previous is accepted", "src/patch.rs",
     [(".is_some_and(|previous| value.same(previous))", ".is_some_and(|_| true)")]),
    ("rust", "patch: a target that is not a scalar is applied", "src/patch.rs",
     [("if res.target != \"scalar\" || is_key_component(root, &segs) {", "if is_key_component(root, &segs) {")]),
    ("rust", "patch: an event of another kind that carries both values is applied", "src/patch.rs",
     [("(EventKind::ValueChange, Some(_), Some(next)) => nexts.push(next),", "(_, Some(_), Some(next)) => nexts.push(next),")]),
    ("rust", "patch: the events are applied in the reverse order", "src/patch.rs",
     [("for (e, next) in d.changes.iter().zip(&nexts) {", "for (e, next) in d.changes.iter().zip(&nexts).rev() {")]),
    ("rust", "patch: the span after is read from the original text", "src/patch.rs",
     [("after: source_span(&out, root, p, a)?,", "after: source_span(text, root, p, a)?,")]),
    ("rust", "patch: only the first event is applied", "src/patch.rs",
     [("        replaced.push(e.path.clone());\n    }\n    let mut spans", "        replaced.push(e.path.clone());\n        break;\n    }\n    let mut spans")]),
    ("rust", "patch: every event is applied to the original text", "src/patch.rs",
     [("out = edit_scalar(&out, root, &e.path, a, next)?.text;", "out = edit_scalar(text, root, &e.path, a, next)?.text;")]),
    ("go", "patch: a value that is not previous is accepted", "patch.go",
     [("\t\tif !f.value.Same(e.Previous) {", "\t\tif false {")]),
    ("go", "patch: a target that is not a scalar is applied", "patch.go",
     [("perr == nil && (res.Target != \"scalar\" || isKeyComponent(root, segs)) {", "perr == nil && (res.Target == \"\" || isKeyComponent(root, segs)) {")]),
    ("go", "patch: an event of another kind that carries both values is applied", "patch.go",
     [("if e.Kind != EventValueChange || e.Previous == nil || e.Next == nil {", "if e.Previous == nil || e.Next == nil {")]),
    ("go", "patch: the events are applied in the reverse order", "patch.go",
     [("\tfor _, e := range d.Changes {\n\t\tr, err := EditScalar(out.Text,", "\tfor k := len(d.Changes) - 1; k >= 0; k-- {\n\t\te := d.Changes[k]\n\t\tr, err := EditScalar(out.Text,")]),
    ("go", "patch: the span after is read from the original text", "patch.go",
     [("after, _ := SourceSpan(out.Text, root, p, a)", "after, _ := SourceSpan(text, root, p, a)")]),
    ("go", "patch: only the first event is applied", "patch.go",
     [("\t\tout.Replaced = append(out.Replaced, e.Path)\n\t}", "\t\tout.Replaced = append(out.Replaced, e.Path)\n\t\tbreak\n\t}")]),
    ("go", "patch: every event is applied to the original text", "patch.go",
     [("r, err := EditScalar(out.Text, root, e.Path, a, e.Next)", "r, err := EditScalar(text, root, e.Path, a, e.Next)")]),
    # P5-1: the canonical bytes (go/canonical.go, rust/src/canonical.rs). The table testdata/p5/canonical-vectors.tsv and the unit tests must turn red on each.
    ("rust", "canonical: the keys of an object are not sorted", "src/canonical.rs",
     [("            sorted.sort_by(|a, b| a.key.as_bytes().cmp(b.key.as_bytes()));\n", "")]),
    ("rust", "canonical: the keys are sorted by UTF-16 code unit, not by byte of the UTF-8", "src/canonical.rs",
     [("a.key.as_bytes().cmp(b.key.as_bytes())", "a.key.encode_utf16().cmp(b.key.encode_utf16())")]),
    ("rust", "canonical: a repeated key is not refused", "src/canonical.rs",
     [("if sorted.windows(2).any(|w| w[0].key == w[1].key) {", "if false {")]),
    ("rust", "canonical: a value the library does not hold is written as null", "src/canonical.rs",
     [("        Value::Unsupported(_) => {\n            return Err(CanonicalError {\n                reason: CANONICAL_UNSUPPORTED_VALUE,\n            })\n        }", "        Value::Unsupported(_) => out.extend_from_slice(b\"null\"),")]),
    ("rust", "canonical: the less-than sign is not escaped", "src/canonical.rs",
     [("| '<' | '>'", "| '>'")]),
    ("rust", "canonical: the line separator is not escaped", "src/canonical.rs",
     [(r"| '\u{2028}' |", "|")]),
    ("rust", "canonical: the last control character below U+0020 is written raw", "src/canonical.rs",
     [(r"'\u{00}'..='\u{1F}'", r"'\u{00}'..'\u{1F}'")]),
    ("rust", "canonical: the newline gets the six-character escape", "src/canonical.rs",
     [("            '\\u{0A}' => out.extend_from_slice(b\"\\\\n\"),\n", "")]),
    ("rust", "canonical: the digest has no sha256 prefix", "src/canonical.rs",
     [('format!("sha256:{}", sha256_hex(bytes))', "sha256_hex(bytes)")]),
    ("rust", "canonical: a sequence is written in reverse", "src/canonical.rs",
     [("for (i, item) in items.iter().enumerate() {", "for (i, item) in items.iter().rev().enumerate() {")]),
    ("go", "canonical: the keys of an object are not sorted", "canonical.go",
     [("\t\tslices.SortStableFunc(sorted, func(x, y Entry) int { return strings.Compare(x.Key, y.Key) })\n", "")]),
    ("go", "canonical: the keys are sorted by UTF-16 code unit, not by byte of the UTF-8", "canonical.go",
     [("return strings.Compare(x.Key, y.Key) })", "return slices.Compare(utf16.Encode([]rune(x.Key)), utf16.Encode([]rune(y.Key))) })"),
      ('import (\n\t"slices"', 'import (\n\t"unicode/utf16"\n\t"slices"')]),
    ("go", "canonical: a repeated key is not refused", "canonical.go",
     [("\t\t\tif sorted[i-1].Key == sorted[i].Key {", "\t\t\tif false {")]),
    ("go", "canonical: a value the library does not hold is written as null", "canonical.go",
     [("\tdefault:\n\t\treturn &CanonicalError{CanonicalUnsupportedValue}", "\tdefault:\n\t\tb.WriteString(\"null\")")]),
    ("go", "canonical: the less-than sign is not escaped", "canonical.go",
     [("case '<', '>', '&',", "case '>', '&',")]),
    ("go", "canonical: the line separator is not escaped", "canonical.go",
     [(r"'&', '\u2028', '\u2029':", r"'&', '\u2029':")]),
    ("go", "canonical: the last control character below U+0020 is written raw", "canonical.go",
     [("if r < 0x20 {", "if r < 0x1f {")]),
    ("go", "canonical: the newline gets the six-character escape", "canonical.go",
     [("\t\tcase '\\n':\n\t\t\tb.WriteString(`\\n`)\n", "")]),
    ("go", "canonical: the digest has no sha256 prefix", "canonical.go",
     [('return "sha256:" + SHA256Hex(b)', "return SHA256Hex(b)")]),
    ("go", "canonical: a nil node is not refused", "canonical.go",
     [("\tif n == nil {\n\t\treturn &CanonicalError{CanonicalNilNode}\n\t}\n", "")]),
    ("go", "canonical: a string that is not UTF-8 is written", "canonical.go",
     [("\tif !utf8.ValidString(s) {", "\tif !utf8.ValidString(s) && false {")]),
    # P5-2: the flat materialization (go/materialize.go, rust/src/materialize.rs). The shared cases testdata/p5/materialize.json and the hand-built shapes must turn red on each.
    ("rust", "materialize: a set does not stop the materialization", "src/materialize.rs",
     [("            ShapeType::Set { .. } => return Err(refuse(MATERIALIZE_SET, name)),\n", "            ShapeType::Set { .. } => {}\n")]),
    ("rust", "materialize: a nested composite is accepted", "src/materialize.rs",
     [("ShapeType::Composite(_) | ShapeType::List { .. } | ShapeType::DeclaredList { .. } => {", "ShapeType::Composite(_) => {}\n            ShapeType::List { .. } | ShapeType::DeclaredList { .. } => {")]),
    ("rust", "materialize: a field with no capability is given implemented", "src/materialize.rs",
     [("            None => return Err(refuse(MATERIALIZE_CAPABILITY_MISSING, name)),\n", "            None => {}\n")]),
    ("rust", "materialize: a capability of another spelling is not refused", "src/materialize.rs",
     [("Some(c) if !CAPABILITIES.contains(&c.as_str()) => {", "Some(c) if false && !CAPABILITIES.contains(&c.as_str()) => {")]),
    ("rust", "materialize: a capability for an undeclared key is not refused", "src/materialize.rs",
     [("        if !declared.iter().any(|(name, _)| name == key) {", "        if false && !declared.iter().any(|(name, _)| name == key) {")]),
    ("rust", "materialize: an omitted optional field is skipped, not refused", "src/materialize.rs",
     [("            return Err(refuse(MATERIALIZE_OMITTED_OPTIONAL, name));", "            continue;")]),
    ("rust", "materialize: a default is reported as authored", "src/materialize.rs",
     [("Some(VALUE_DEFAULT) => PROVENANCE_SCHEMA_DEFAULT,", "Some(VALUE_DEFAULT) => PROVENANCE_AUTHORED,")]),
    ("rust", "materialize: the provenance of a default is spelled default", "src/materialize.rs",
     [('const PROVENANCE_SCHEMA_DEFAULT: &str = "schema_default";', 'const PROVENANCE_SCHEMA_DEFAULT: &str = "default";')]),
    ("rust", "materialize: the fields are not sorted by key", "src/materialize.rs",
     [("    fields.sort_by(|x, y| x.key.cmp(&y.key));\n", "")]),
    ("rust", "materialize: the expected keys are taken in schema order", "src/materialize.rs",
     [("    expected_keys.sort();\n", "")]),
    ("rust", "materialize: the capability is dropped from the projection", "src/materialize.rs",
     [('                entry("capability", text(&f.capability)),\n', "")]),
    ("rust", "materialize: a document the validation refuses is accepted as empty", "src/materialize.rs",
     [("    let Some(document) = defaulted.document else {\n        return Err(refuse(MATERIALIZE_INVALID_DOCUMENT, \"\"));\n    };", "    let document = defaulted.document.unwrap_or_else(|| Node::new(Value::Map(vec![])));")]),
    ("go", "materialize: a set does not stop the materialization", "materialize.go",
     [("\t\tcase ShapeSet:\n\t\t\treturn nil, &MaterializeError{MaterializeSet, f.Name}\n", "")]),
    ("go", "materialize: a nested composite is accepted", "materialize.go",
     [("case ShapeComposite, ShapeList, ShapeDeclaredList:", "case ShapeList, ShapeDeclaredList:")]),
    ("go", "materialize: a field with no capability is given implemented", "materialize.go",
     [("\t\tif !ok {\n\t\t\treturn nil, &MaterializeError{MaterializeCapabilityMissing, f.Name}\n\t\t}\n", "\t\t_ = ok\n")]),
    ("go", "materialize: a capability of another spelling is not refused", "materialize.go",
     [("if !slices.Contains(materializeCapabilities, c) {", "if false && !slices.Contains(materializeCapabilities, c) {")]),
    ("go", "materialize: a capability for an undeclared key is not refused", "materialize.go",
     [("\t\tif !isDeclared(k) {", "\t\tif false && !isDeclared(k) {")]),
    ("go", "materialize: an omitted optional field is skipped, not refused", "materialize.go",
     [("\t\t\treturn nil, &MaterializeError{MaterializeOmittedOptional, f.Name}", "\t\t\tcontinue")]),
    ("go", "materialize: a default is reported as authored", "materialize.go",
     [("provenance = provenanceSchemaDefault", "provenance = provenanceAuthored")]),
    ("go", "materialize: the provenance of a default is spelled default", "materialize.go",
     [('provenanceSchemaDefault = "schema_default"', 'provenanceSchemaDefault = "default"')]),
    ("go", "materialize: the fields are not sorted by key", "materialize.go",
     [("return strings.Compare(x.Key, y.Key) })\n\texpected", "return strings.Compare(x.Key, y.Key) * 0 })\n\texpected")]),
    ("go", "materialize: the expected keys are taken in schema order", "materialize.go",
     [("\tslices.Sort(expected)\n", "")]),
    ("go", "materialize: the capability is dropped from the projection", "materialize.go",
     [('\t\t\t{Key: "capability", Value: textNode(f.Capability)},\n', "")]),
    ("go", "materialize: the undeclared capability keys are checked in map order", "materialize.go",
     [("\tslices.Sort(given)\n", "")]),
]


def in_diff(defects, changed):
    """The defects of the product files in `changed` (the keys of tools/diff_gate.py's diff-lines.json: `go/x.go`, `rust/src/x.rs`).
    A defect says what the test suites must catch in one file; a change of another file cannot unprotect it (a weakened test is `full`)."""
    return [d for d in defects if "%s/%s" % (d[0], d[2]) in changed]


def run(cmd, cwd, env):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def prepare(lang, tmp):
    """A copy of the module, laid out so that its tests find the corpus and testdata."""
    os.symlink(os.path.join(ROOT, "corpus"), os.path.join(tmp, "corpus"))
    os.symlink(os.path.join(ROOT, "testdata"), os.path.join(tmp, "testdata"))
    dest = os.path.join(tmp, lang)
    shutil.copytree(os.path.join(ROOT, lang), dest, ignore=shutil.ignore_patterns("target", "fuzz", "rapid"))
    return dest


def command(lang):
    if lang == "rust":
        return ["cargo", "+" + os.environ.get("RUST_TOOLCHAIN", "stable"), "test", "--locked", "--lib", "--test", "access", "--test", "corpus", "--test", "properties",
                "--test", "parity_dump", "--test", "behaviour", "--test", "schema", "--test", "real_schema", "--test", "expand", "--test", "policy", "--test", "defaults", "--test", "normalize", "--test", "resolution", "--test", "resolve_vectors", "--test", "resolve_properties", "--test", "composed", "--test", "editscalar", "--test", "diff", "--test", "canonical_corpus", "--test", "materialize_corpus"]
    return ["go", "test", "-count=1", "./..."]


def build_command(lang):
    """The same packages and test targets, built and not run: a defect the suites "catch" only because it does not build proves nothing
    (#94); `go test` exits 1 for a build failure as for a failing test, and so does cargo."""
    if lang == "rust":
        return command(lang) + ["--no-run"]
    return ["go", "test", "-vet=off", "-count=1", "-run", "^$", "./..."]


def stale_only(langs, match):
    """Every entry must match its file exactly once; no test runs."""
    bad = checked = 0
    for lang, name, rel, ops in DEFECTS:
        if lang not in langs or (match and match not in name and match not in rel):
            continue
        text = open(os.path.join(ROOT, lang, rel), encoding="utf-8").read()
        checked += 1
        for old, new in ops:
            if text.count(old) != 1:
                print("%-4s STALE %s: %r occurs %d times" % (lang, name, old, text.count(old)))
                bad += 1
                break
            text = text.replace(old, new)
    print("SEEDED DEFECTS (texts only, no test run): %d entries, %s" % (checked, "all match" if bad == 0 else "%d STALE" % bad))
    return 0 if bad == 0 else 1


def main():
    args = sys.argv[1:]
    changed = None
    if "--changed-from" in args:
        i = args.index("--changed-from")
        with open(args[i + 1], encoding="utf-8") as fh:
            data = json.load(fh)
        # tools/diff_gate.py's scope.json (the product files of both languages) or a plain list / object keyed by path
        changed = set(data.get("rust_files", [])) | set(data.get("go_files", [])) if "go_files" in data else set(data)
        del args[i:i + 2]
    match = None
    if "--match" in args:
        i = args.index("--match")
        match = args[i + 1]
        del args[i:i + 2]
    shard = (0, 1)
    if "--shard" in args:
        i = args.index("--shard")
        k, n = args[i + 1].split("/")
        shard = (int(k), int(n))
        if not 0 <= shard[0] < shard[1]:
            print("--shard K/N needs 0 <= K < N")
            return 2
        del args[i:i + 2]
    only_texts = "--stale-only" in args
    args = [a for a in args if a != "--stale-only"]
    which = args[0] if args else "all"
    langs = ("rust", "go") if which == "all" else (which,)
    if only_texts:
        return stale_only(langs, match)
    defects = DEFECTS if changed is None else in_diff(DEFECTS, changed)
    if changed is not None:
        print("diff mode: %d of %d defects are in changed files; the other %d are not run" % (len(defects), len(DEFECTS), len(DEFECTS) - len(defects)))
    bad = 0
    env = dict(os.environ, CIC_FORMS_ROOT=ROOT)
    for name in ("CIC_PRIMITIVES_ROOT", "CIC_REGISTRY_ROOT"):
        if name not in env:
            print("%s is not set: the runners need the pinned checkout" % name)
            return 2
    for lang in langs:
        if not [d for d in defects if d[0] == lang]:
            print("%-4s no defect of a changed file: not run" % lang)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            env["CARGO_TARGET_DIR"] = os.path.join(tmp, "target")
            base = prepare(lang, tmp)
            first = run(command(lang), base, env)
            print("%-4s baseline (unmodified)           : %s" % (lang, "pass" if first.returncode == 0 else "FAIL"))
            if first.returncode != 0:
                print(first.stdout[-2000:], first.stderr[-2000:])
                bad += 1
                continue
            mine = [d for d in defects if d[0] == lang and (not match or match in d[1] or match in d[2])]
            for _, name, rel, ops in mine[shard[0]::shard[1]]:
                path = os.path.join(base, rel)
                original = open(path, encoding="utf-8").read()
                text = original
                for old, new in ops:
                    if text.count(old) != 1:
                        print("%-4s STALE %s: %r occurs %d times" % (lang, name, old, text.count(old)))
                        bad += 1
                        break
                    text = text.replace(old, new)
                else:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    r = run(command(lang), base, env)
                    caught = r.returncode != 0
                    builds = not caught or run(build_command(lang), base, env).returncode == 0
                    print("%-4s %-46s: %s" % (lang, name, "NOT CAUGHT" if not caught else "CAUGHT" if builds else "DOES NOT BUILD (proves nothing)"))
                    bad += 0 if caught and builds else 1
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(original)
    print("SEEDED DEFECTS%s: %s" % ((" (partial: --match %s)" % match if match else "") + (" (shard %d/%d)" % shard if shard[1] > 1 else ""), "every defect is caught" if bad == 0 else "FAILED (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
