"""Adds the surface-level validation cases (issue #167) to testdata/p1/schema.json. Idempotent: refuses to add twice."""
import json, sys
P = "testdata/p1/schema.json"
raw = open(P, encoding="utf-8").read()
d = json.loads(raw)
assert json.dumps(d, indent=1, ensure_ascii=False) == raw.rstrip("\n"), "the file is not in the canonical formatting"
if any(c["id"].startswith("surface-validation.") for c in d["cases"]):
    sys.exit("already added")

HEAD = ('spec:\n  identity:\n    atomic_ref: "cic:core:Identity@v0.2.0"\n    kind: Demo\n    namespace: "cic:demo"\n    version: v1.0.0\n'
        '  config_surface:\n    aggregate_ref: "cic:core:ConfigSurface@v0.2.0"\n')
NODES = ('    nodes:\n      - name: kind\n        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n'
         '      - name: mtu\n        shape_type: scalar\n        scalar_type: integer\n        optional: true\n        contract:\n          - type: range\n            expression: "68..9216"\n')

def case(cid, validation, surface_paths, docs=None):
    """One shared schema vector: the schema text with the given surface `validation` block, the expected compile result (the node-level contract and the listed surface-level paths), and optionally the documents to validate."""
    c = {"id": cid, "schema": HEAD + NODES + validation, "surface": "config_surface",
         "compile": {"ok": True, "uses_item_choice": False, "unevaluated_contracts": ["$.mtu"],
                     "unevaluated_surface_contracts": surface_paths,
                     "kind": "Demo", "namespace": "cic:demo", "version": "v1.0.0"}}
    if docs is not None:
        c["documents"] = docs
    return c

MUST = "      - type: must\n        expression: \"kind = 'Demo'\"\n"
MUST2 = "      - type: must\n        expression: \"count(items) = 1\"\n"
S = "config_surface.validation"
new = [
    case("surface-validation.absent", "", []),
    case("surface-validation.empty-list", "    validation: []\n", []),
    case("surface-validation.one-entry", "    validation:\n" + MUST, [S + "[0]"]),
    case("surface-validation.two-entries", "    validation:\n" + MUST + MUST2, [S + "[0]", S + "[1]"],
         # a document that breaks the declared entry is still accepted: the entry is listed, not evaluated
         [{"id": "breaks-the-declared-must", "yaml": "kind: Other\n", "expect": {"violations": [], "candidates": []}}]),
    case("surface-validation.scalar-member", "    validation: must\n", [S]),
    case("surface-validation.null-member", "    validation:\n", [S]),
    case("surface-validation.mapping-member", "    validation:\n      type: must\n      expression: \"kind = 'Demo'\"\n", [S]),
    case("surface-validation.entry-not-inspected", "    validation:\n      - not-a-contract\n      - 5\n", [S + "[0]", S + "[1]"]),
]
d["cases"].extend(new)
open(P, "w", encoding="utf-8").write(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
print("added", len(new))
