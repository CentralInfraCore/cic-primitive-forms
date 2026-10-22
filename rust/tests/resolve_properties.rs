//! Properties of `resolve_rule` over generated object graphs (`docs/P3-UNIT4-POLICY-RESOLUTION.md`). The graphs are chains of objects
//! with random carriers, object policies and field rules; the properties are the ones the design promises whatever the chain looks
//! like. The Go peer is `go/resolve_properties_test.go`.

use cic_primitive_forms::{
    bind_grammar, compile_schema, normalize, parse, resolve_policy, resolve_rule, Carrier, Context,
    Entry, Mode, Node, ObjectPolicy, PolicyGraph, PolicyObject, PolicyRule, PolicySurface,
    RuleKind, SchemaInput, Value, LAYER_FIELD, LAYER_NONE, LAYER_OBJECT_DEFAULT,
    LAYER_POLICY_INHERITED, LAYER_SYSTEM, REASON_CONTEXT_INCOMPLETE, REASON_RESET_NO_LOCAL_RULE,
};
use proptest::prelude::*;

fn mode() -> impl Strategy<Value = Mode> {
    prop_oneof![
        Just(Mode::Inherit),
        Just(Mode::NoInherit),
        Just(Mode::Reset)
    ]
}

fn rule() -> impl Strategy<Value = Option<PolicyRule>> {
    prop_oneof![
        Just(None),
        Just(Some(PolicyRule::Patterns(vec![]))), // an explicit "nobody"
        Just(Some(PolicyRule::Patterns(vec!["O=r".to_string()]))),
    ]
}

fn carrier() -> impl Strategy<Value = Carrier> {
    (mode(), rule(), rule()).prop_map(|(mode, access, modify)| Carrier {
        mode,
        access,
        modify,
    })
}

/// `$ <- $.o1 <- ... <- $.on`; returns the graph and the id of the deepest object.
fn chain(policy: &[ObjectPolicy], carrier: &[Carrier]) -> (PolicyGraph, String) {
    let mut g = PolicyGraph::new();
    let mut id = "$".to_string();
    for (i, p) in policy.iter().enumerate() {
        let prev = id.clone();
        if i > 0 {
            id = format!("{prev}.o{i}");
        }
        g.insert(
            id.clone(),
            PolicyObject {
                id: id.clone(),
                parent: (i > 0).then(|| prev.clone()),
                policy: p.clone(),
                carrier: (i > 0).then(|| carrier[i].clone()),
                carrier_path: id.clone(),
            },
        );
    }
    (g, id)
}

fn declared() -> impl Strategy<Value = ObjectPolicy> {
    (rule(), rule(), mode()).prop_map(|(a, m, inherit_policy)| {
        ObjectPolicy::Declared(PolicySurface {
            declared: true,
            default_access: a,
            default_modify: m,
            surface_rules: Vec::new(),
            inherit_policy,
            inherit_policy_declared: true,
        })
    })
}

fn any_policy() -> impl Strategy<Value = ObjectPolicy> {
    prop_oneof![
        Just(ObjectPolicy::Absent),
        Just(ObjectPolicy::Unknown),
        declared()
    ]
}

proptest! {
    #[test]
    fn an_unknown_ancestor_is_never_permissive(n in 1usize..7, k in 0usize..7, modes in proptest::collection::vec(mode(), 7)) {
        let k = k.min(n);
        let policy: Vec<ObjectPolicy> =
            (0..=n).map(|i| if i == k { ObjectPolicy::Unknown } else { ObjectPolicy::Absent }).collect();
        let carriers: Vec<Carrier> =
            (0..=n).map(|i| Carrier { mode: modes[i.min(6)], access: None, modify: None }).collect();
        let (g, target) = chain(&policy, &carriers);
        for op in ["access", "modify"] {
            let r = resolve_rule(&g, &target, "config_surface", None, &format!("{target}.f"), op);
            prop_assert!(matches!(r.kind, RuleKind::Unknown | RuleKind::None), "{op}: {r:?}");
            prop_assert!(r.layer == LAYER_NONE && !r.reason.is_empty(), "{op}: {r:?}");
        }
    }

    #[test]
    fn the_result_is_always_well_formed(
        policy in proptest::collection::vec(any_policy(), 1..8),
        carriers in proptest::collection::vec(carrier(), 8),
        field in rule(),
    ) {
        let (g, target) = chain(&policy, &carriers);
        for op in ["access", "modify"] {
            let r = resolve_rule(&g, &target, "config_surface", field.as_ref(), &format!("{target}.f"), op);
            match r.kind {
                RuleKind::None | RuleKind::Unknown => {
                    prop_assert!(r.layer == LAYER_NONE && !r.reason.is_empty() && r.source_path.is_empty(), "{op}: {r:?}");
                }
                RuleKind::Open | RuleKind::Owner => {
                    prop_assert!(r.layer == LAYER_SYSTEM && r.source_path.is_empty() && r.reason.is_empty(), "{op}: {r:?}");
                }
                RuleKind::List => {
                    prop_assert!(
                        r.layer != LAYER_NONE && r.layer != LAYER_SYSTEM && !r.source_path.is_empty() && r.reason.is_empty(),
                        "{op}: {r:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn a_field_rule_on_an_object_with_no_policy_wins(
        n in 0usize..6,
        carriers in proptest::collection::vec(carrier(), 6),
        nobody in any::<bool>(),
    ) {
        let policy = vec![ObjectPolicy::Absent; n + 1];
        let (g, target) = chain(&policy, &carriers);
        let field = PolicyRule::Patterns(if nobody { vec![] } else { vec!["O=r".to_string()] });
        let path = format!("{target}.f");
        for op in ["access", "modify"] {
            let r = resolve_rule(&g, &target, "config_surface", Some(&field), &path, op);
            prop_assert!(r.kind == RuleKind::List && r.layer == LAYER_FIELD && r.source_path == path, "{op}: {r:?}");
        }
    }
}

fn object(
    id: &str,
    parent: Option<&str>,
    policy: ObjectPolicy,
    carrier: Option<Carrier>,
) -> (String, PolicyObject) {
    (
        id.to_string(),
        PolicyObject {
            id: id.to_string(),
            parent: parent.map(str::to_string),
            policy,
            carrier,
            carrier_path: id.to_string(),
        },
    )
}

fn assert_incomplete(g: &PolicyGraph, target: &str) {
    let r = resolve_rule(
        g,
        target,
        "config_surface",
        None,
        &format!("{target}.f"),
        "access",
    );
    assert!(
        r.kind == RuleKind::Unknown
            && r.reason == REASON_CONTEXT_INCOMPLETE
            && r.layer == LAYER_NONE,
        "want unknown / CONTEXT_INCOMPLETE, got {r:?}"
    );
}

#[test]
fn an_incomplete_or_cyclic_graph_is_unknown_never_permissive() {
    let inherit = || {
        Some(Carrier {
            mode: Mode::Inherit,
            access: None,
            modify: None,
        })
    };
    let graph = |objects: Vec<(String, PolicyObject)>| objects.into_iter().collect::<PolicyGraph>();
    // a cycle through the carriers
    assert_incomplete(
        &graph(vec![
            object("a", Some("b"), ObjectPolicy::Absent, inherit()),
            object("b", Some("a"), ObjectPolicy::Absent, inherit()),
        ]),
        "a",
    );
    // a carrier whose parent is not in the graph, and a carrier with no parent at all
    assert_incomplete(
        &graph(vec![object(
            "a",
            Some("zz"),
            ObjectPolicy::Absent,
            inherit(),
        )]),
        "a",
    );
    assert_incomplete(
        &graph(vec![object("a", None, ObjectPolicy::Absent, inherit())]),
        "a",
    );
    // the object itself is not in the graph
    assert_incomplete(&graph(vec![]), "a");
    // the walk over the ancestors' policy: a missing parent and a cycle (no carrier, so the first walk ends at once)
    assert_incomplete(
        &graph(vec![object("a", Some("zz"), ObjectPolicy::Absent, None)]),
        "a",
    );
    assert_incomplete(
        &graph(vec![
            object("a", Some("b"), ObjectPolicy::Absent, None),
            object("b", Some("a"), ObjectPolicy::Absent, None),
        ]),
        "a",
    );
}

#[test]
fn an_undeclared_inherit_policy_is_ignored() {
    let surface = |default_access, inherit_policy| {
        ObjectPolicy::Declared(PolicySurface {
            declared: true,
            default_access,
            default_modify: None,
            surface_rules: Vec::new(),
            inherit_policy,
            inherit_policy_declared: false,
        })
    };
    let list = Some(PolicyRule::Patterns(vec!["O=r".to_string()]));
    let g: PolicyGraph = vec![
        object("$", None, surface(list, Mode::Inherit), None),
        object(
            "$.a",
            Some("$"),
            surface(None, Mode::Reset),
            Some(Carrier {
                mode: Mode::Inherit,
                access: None,
                modify: None,
            }),
        ),
    ]
    .into_iter()
    .collect();
    let r = resolve_rule(&g, "$.a", "config_surface", None, "$.a.f", "access");
    assert!(
        r.layer == LAYER_POLICY_INHERITED && r.source_path == "$",
        "an inherit_policy the schema did not declare changed the walk: {r:?}"
    );
}

/// The scenario was run through the reference (`access_policy.py`, `resolve`): an object whose own policy stops the inheritance, under
/// a carrier that resets, is still behind the reset barrier of the carrier walk: deny, `RESET_NO_LOCAL_RULE`.
#[test]
fn a_reset_carrier_above_an_object_that_does_not_inherit_still_sets_the_barrier() {
    let no_inherit = ObjectPolicy::Declared(PolicySurface {
        declared: true,
        default_access: None,
        default_modify: None,
        surface_rules: Vec::new(),
        inherit_policy: Mode::NoInherit,
        inherit_policy_declared: true,
    });
    let g: PolicyGraph = vec![
        object("$", None, ObjectPolicy::Absent, None),
        object(
            "a",
            Some("$"),
            ObjectPolicy::Absent,
            Some(Carrier {
                mode: Mode::Reset,
                access: None,
                modify: None,
            }),
        ),
        object(
            "o1",
            Some("a"),
            no_inherit,
            Some(Carrier {
                mode: Mode::Inherit,
                access: None,
                modify: None,
            }),
        ),
    ]
    .into_iter()
    .collect();
    let r = resolve_rule(&g, "o1", "config_surface", None, "o1.f", "access");
    assert!(
        r.kind == RuleKind::None && r.reason == REASON_RESET_NO_LOCAL_RULE,
        "{r:?}"
    );
}

// ---- G2 and G5 of the P3 exit-criteria check ---------------------------------------------------------------------------------

struct Xs(u64);
impl Xs {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
}

/// A copy of `n` whose mapping entries, at every depth, are in an order drawn from `rng`. The values are untouched.
fn shuffled(n: &Node, rng: &mut Xs) -> Node {
    let value = match &n.value {
        Value::Map(es) => {
            let mut es: Vec<Entry> = es
                .iter()
                .map(|e| Entry {
                    key: e.key.clone(),
                    key_pos: e.key_pos,
                    value: shuffled(&e.value, rng),
                })
                .collect();
            for i in (1..es.len()).rev() {
                let j = (rng.next() % (i as u64 + 1)) as usize;
                es.swap(i, j);
            }
            Value::Map(es)
        }
        Value::Seq(items) => Value::Seq(items.iter().map(|it| shuffled(it, rng)).collect()),
        other => other.clone(),
    };
    Node { value, pos: n.pos }
}

/// Reordering the entries of a mapping, at any depth, changes neither the normalization (as a value, with its origins) nor the
/// resolution, whole (rule, layer, source path and reason of every position). The Go peer is `TestPropertyMappingOrderChangesNoResult`.
#[test]
fn mapping_order_changes_no_result() {
    let root = std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    );
    let f: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(root.join("testdata/p1/resolution.json")).unwrap(),
    )
    .unwrap();
    let a: Vec<String> = f["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    for c in f["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let schema = c["schema"].as_str().unwrap();
        let cs = compile_schema(&SchemaInput {
            bytes: schema.as_bytes(),
            sha256: &cic_primitive_forms::access::sha256_hex(schema.as_bytes()),
            grammar: f["grammar_pin"].as_str().unwrap(),
            surface: "config_surface",
        })
        .unwrap();
        let one = Value::Int(c["grammar"].as_i64().unwrap());
        let g = bind_grammar(Some(&one), Some(&one)).unwrap();
        let unknown: Vec<String> = c["unknown"]
            .as_array()
            .map(|u| u.iter().map(|s| s.as_str().unwrap().to_string()).collect())
            .unwrap_or_default();
        let run = |d: &Node| {
            let n = normalize(d, &cs.root, &a, g, &Context::default()).unwrap();
            let r = resolve_policy(
                &n.object,
                &cs.root,
                &cs.policy,
                &cs.surface,
                &a,
                g,
                &unknown,
            )
            .unwrap();
            (n, r)
        };
        let doc = parse(c["yaml"].as_str().unwrap()).unwrap();
        let (base_n, base_r) = run(&doc);
        for seed in 1..=60u64 {
            let (n, r) = run(&shuffled(
                &doc,
                &mut Xs(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1),
            ));
            assert!(
                n.object.same(&base_n.object) && n.value_origins == base_n.value_origins,
                "{id}: seed {seed}: the normalization changed"
            );
            assert_eq!(r, base_r, "{id}: seed {seed}: the resolution changed");
        }
    }
}

proptest! {
    /// The local layers are never cut. A position's own list wins over everything above it, whatever the chain looks like; and the
    /// containing object's own rule applies even when its carrier or its own policy resets or stops the inheritance.
    #[test]
    fn local_rules_are_never_cut(
        policy in proptest::collection::vec(any_policy(), 1..8),
        carriers in proptest::collection::vec(carrier(), 8),
        field in rule(),
        own_mode in mode(),
    ) {
        let mut policy = policy;
        let own = PolicyRule::Patterns(vec!["O=own".to_string()]);
        let last = policy.len() - 1;
        policy[last] = ObjectPolicy::Declared(PolicySurface {
            declared: true,
            default_access: Some(own.clone()),
            default_modify: Some(own),
            surface_rules: Vec::new(),
            inherit_policy: own_mode,
            inherit_policy_declared: true,
        });
        let (g, target) = chain(&policy, &carriers);
        let path = format!("{target}.f");
        for op in ["access", "modify"] {
            let r = resolve_rule(&g, &target, "config_surface", field.as_ref(), &path, op);
            if field.is_some() {
                prop_assert!(r.layer == LAYER_FIELD && r.source_path == path, "{op}: the position's own rule was cut: {r:?}");
            } else {
                prop_assert!(
                    r.kind == RuleKind::List && r.layer == LAYER_OBJECT_DEFAULT && r.source_path == target,
                    "{op}: the object's own default was cut: {r:?}"
                );
            }
        }
    }
}
