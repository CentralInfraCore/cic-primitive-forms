//! Properties of `resolve_rule` over generated object graphs (`docs/P3-UNIT4-POLICY-RESOLUTION.md`). The graphs are chains of objects
//! with random carriers, object policies and field rules; the properties are the ones the design promises whatever the chain looks
//! like. The Go peer is `go/resolve_properties_test.go`.

use cic_primitive_forms::{
    resolve_rule, Carrier, Mode, ObjectPolicy, PolicyGraph, PolicyObject, PolicyRule,
    PolicySurface, RuleKind, LAYER_FIELD, LAYER_NONE, LAYER_SYSTEM,
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
