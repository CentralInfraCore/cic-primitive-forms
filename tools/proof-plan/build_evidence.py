"""Evidence from an extractor's output: the link between the source and the kernel (evidence.py).

The builder reads what the extractor reported (graph JSON, schema cic-lab/typeddeps/v2) and what the caller measured (Merkle keys,
digests of the data read, of the environment). It states facts about the closure of ONE subject and nothing more:

* the constructs found in the closure, from the nodes (`constructs`), and whether the extraction of every node of it was complete
  in the extractor's declared scope (`analysis`);
* whether the subject has a node, and the opaque dependencies of the closure.

It never says that the extractor models those constructs correctly. That is the caller's Assurance, held outside the extractor.

Fail-closed: whatever is missing from the extractor's output (a node with no `constructs` or `analysis`, another schema, a subject
with no node) makes the analysis INCOMPLETE with the reason, so the kernel answers UNVERIFIABLE; a missing extractor binary, which
leaves no identity, is an error and no evidence at all.

The extractor identity is its binary's SHA-256: the digest covers the Go runtime and the libraries (x/tools) built into it and the
build flags, which a version string would not. `go version -m` is kept as provenance next to it.
"""
import hashlib
import subprocess

import evidence as ev

GRAPH_SCHEMA = "cic-lab/typeddeps/v2"
RULE_VERSION = "cic-lab/reuse-rules/v1"


class BuilderError(Exception):
    """No evidence can be made at all (not even an UNVERIFIABLE one)."""


def _digest(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        raise BuilderError("%s unreadable: %s" % (path, e))


def extractor_identity(binary, args=(), name="typeddeps", chain=()):
    """(identity, provenance) of an extractor: its binary, run with `args`, and the tools that make its input (`chain`, a list of
    (name, path), e.g. the rust-analyzer that writes the SCIP index scipdeps reads). BuilderError if one cannot be read.

    The identity holds the SHA-256 of every binary, which covers the runtime and the libraries built into it and the build flags,
    and the flags the extractor ran with: the same binary with -planted-no-const-group is a different model. The name is the
    language's: an identity of one language never equals one of another, so an Assurance for one cannot authorise the other."""
    parts = [(name, binary)] + list(chain)
    digests = [(n, _digest(p)) for n, p in parts]
    r = subprocess.run(["go", "version", "-m", binary], capture_output=True, text=True)
    build = [l.strip() for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else ["go version -m unavailable"]
    ident = "|".join("%s:sha256:%s" % (n, d) for n, d in digests) + "".join("|" + a for a in args)
    prov = {"extractor_binary_sha256": digests[0][1], "extractor_args": list(args), "extractor_build": build,
            "chain": {n: d for n, d in digests[1:]}}
    return ident, prov


def closure(nodes, subject):
    """The ids reachable from `subject` through deps that are nodes of the graph."""
    seen, stack = set(), [subject] if subject in nodes else []
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        stack.extend(d for d in nodes[i].get("deps", ()) if d in nodes)
    return seen


def build_evidence(graph, keys, subject_id, extractor, provenance, data_digest, environment_digest, opaque_reads=(), verdict=None,
                   kind="test", rule_version=RULE_VERSION, evidence_id=None):
    nodes = {n["id"]: n for n in graph.get("nodes", [])} if isinstance(graph, dict) else {}
    unresolved, constructs, opaque = [], set(), []
    if not isinstance(graph, dict) or graph.get("schema") != GRAPH_SCHEMA:
        unresolved.append("graph schema is not %s" % GRAPH_SCHEMA)
    in_graph = subject_id in nodes
    if not in_graph:
        unresolved.append("the subject has no node in the graph")
    for i in sorted(closure(nodes, subject_id)):
        n = nodes[i]
        c, a = n.get("constructs"), n.get("analysis")
        if not isinstance(c, list):
            unresolved.append("%s: no constructs reported" % i)
        else:
            constructs.update(c)
        if not (isinstance(a, dict) and a.get("status") == "COMPLETE" and a.get("unresolved") == []):
            unresolved.append("%s: analysis not complete%s" % (i, "" if not isinstance(a, dict) else ": " + "; ".join(a.get("unresolved") or ["no status"])))
        opaque.extend("%s: %s" % (i, o) for o in n.get("opaque") or ())
    e = {
        "contract_version": ev.CONTRACT_V2, "rule_version": rule_version,
        "subject": {"id": subject_id, "kind": kind},
        "code_closure_digest": keys.get(subject_id) or "unknown:no-key:" + subject_id,
        "data_dependencies_digest": data_digest, "environment_digest": environment_digest,
        "graph": {"extractor": extractor, "subject_in_graph": in_graph, "constructs": sorted(constructs), "opaque": sorted(opaque),
                  "analysis": {"status": "INCOMPLETE" if unresolved else "COMPLETE", "unresolved": unresolved}},
        "opaque_reads": list(opaque_reads),
        "provenance": dict(provenance, **({"evidence_id": evidence_id} if evidence_id else {})),
    }
    if verdict is not None:
        e["verdict"] = verdict
    return e
