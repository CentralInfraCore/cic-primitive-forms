"""The verification kernel: may an earlier result stand for the current state?

    from evidence import evaluate_reuse, Assurance

    decision = evaluate_reuse(previous, current, assurance)

Three things are kept apart:

* **Evidence** (`previous`): what an earlier execution produced and what it depended on: a verdict, the three digests of its
  validity (code closure, data read, environment), and a description of the graph the code digest came from. Its provenance
  (commit, id of the run) says where it came from. It is NOT part of the decision: the same keys under another commit are the
  same evidence.
* **Current state** (`current`): the same shape, for the state to be judged, without a verdict.
* **Assurance**: what the caller has established, independently of the evidence, about the tools that made the digests: which
  contract versions and rule versions are accepted, and for each extractor identity the set of language constructs whose
  dependencies it is known to model. The evidence cannot vouch for itself: an extractor that is not listed, or a construct the
  listed one is not verified for, makes the evidence unusable. This function uses assurance; it does not create it.

The function reads nothing (no file, git, environment, clock, process) and changes none of its arguments. The decision is:

    UNVERIFIABLE    the proof model cannot support a claim (unknown version or extractor, a construct outside the verified
                    scope, a subject with no node in the graph, an opaque dependency, malformed evidence). Technically a rerun.
    RERUN_REQUIRED  a known dependency or the environment differs, or the earlier result was not a pass. A real change.
    REUSE_ELIGIBLE  every condition holds and every digest is equal.

UNVERIFIABLE wins over RERUN_REQUIRED: when the model is in doubt a reported change is not trusted either. The reasons are
stable codes (REASONS below); `compared` lists the values actually compared, so a decision can be reproduced.
"""
from dataclasses import dataclass, field

CONTRACT_V2 = "cic-lab/evidence/v2"  # v2: graph.analysis is required (v1 had no statement of completeness)

REUSE_ELIGIBLE = "REUSE_ELIGIBLE"
RERUN_REQUIRED = "RERUN_REQUIRED"
UNVERIFIABLE = "UNVERIFIABLE"

ALL_CONDITIONS_HOLD = "ALL_CONDITIONS_HOLD"
UNVERIFIABLE_REASONS = (
    "MALFORMED_EVIDENCE", "CONTRACT_VERSION_UNKNOWN", "RULE_VERSION_NOT_ACCEPTED", "SUBJECT_MISMATCH", "EXTRACTOR_NOT_ALLOWED",
    "EXTRACTOR_MISMATCH", "ANALYSIS_INCOMPLETE", "CONSTRUCT_NOT_VERIFIED", "SUBJECT_KIND_UNSUPPORTED", "NONDETERMINISTIC_SUBJECT", "SUBJECT_NODE_MISSING", "OPAQUE_DEPENDENCY",
)
RERUN_REASONS = ("PREVIOUS_TEST_FAILED", "CODE_CHANGED", "DATA_CHANGED", "ENVIRONMENT_CHANGED")
REASONS = (ALL_CONDITIONS_HOLD,) + UNVERIFIABLE_REASONS + RERUN_REASONS


@dataclass(frozen=True)
class Assurance:
    """What the caller has established about the tools; supplied from outside, never read from the evidence."""
    contract_versions: frozenset
    rule_versions: frozenset  # the rule versions whose evidence is accepted; a stricter rule does not inherit an older one's
    extractors: dict = field(default_factory=dict)  # extractor identity -> frozenset of verified construct names

    @staticmethod
    def of(contract_versions, rule_versions, extractors):
        return Assurance(frozenset(contract_versions), frozenset(rule_versions),
                         {k: frozenset(v) for k, v in extractors.items()})


@dataclass(frozen=True)
class Decision:
    status: str
    reasons: tuple  # stable codes from REASONS, sorted
    compared: dict  # the values that were compared, for reproduction


def _is_str(x):
    return isinstance(x, str) and x != ""


def _is_str_list(x):
    return isinstance(x, list) and all(_is_str(i) for i in x)


def _shape_errors(e, with_verdict):
    """Why `e` is not evidence of the v1 shape; an empty list when it is."""
    if not isinstance(e, dict):
        return ["not an object"]
    err = []
    for k in ("code_closure_digest", "data_dependencies_digest", "environment_digest"):
        if not _is_str(e.get(k)):
            err.append(k)
    s = e.get("subject")
    if not (isinstance(s, dict) and _is_str(s.get("id")) and _is_str(s.get("kind"))):
        err.append("subject")
    g = e.get("graph")
    if not (isinstance(g, dict) and _is_str(g.get("extractor")) and isinstance(g.get("subject_in_graph"), bool)
            and _is_str_list(g.get("constructs")) and _is_str_list(g.get("opaque"))
            and isinstance(g.get("analysis"), dict) and g["analysis"].get("status") in ("COMPLETE", "INCOMPLETE")
            and _is_str_list(g["analysis"].get("unresolved"))):
        err.append("graph")
    if not _is_str_list(e.get("opaque_reads")):
        err.append("opaque_reads")
    if with_verdict and e.get("verdict") not in ("pass", "fail"):
        err.append("verdict")
    if "stability" in e and e["stability"] not in ("stable", "unstable"):
        err.append("stability")
    return err


def _unverifiable(previous, current, assurance):
    codes, shape = set(), {}
    for name, e, with_verdict in (("previous", previous, True), ("current", current, False)):
        errs = _shape_errors(e, with_verdict)
        if errs:
            codes.add("MALFORMED_EVIDENCE")
            shape[name] = errs
    if codes:
        return codes, shape  # nothing else can be read from a malformed argument
    for e in (previous, current):
        if e.get("contract_version") not in assurance.contract_versions:
            codes.add("CONTRACT_VERSION_UNKNOWN")
    if previous.get("rule_version") not in assurance.rule_versions:
        codes.add("RULE_VERSION_NOT_ACCEPTED")
    if previous["subject"] != current["subject"]:
        codes.add("SUBJECT_MISMATCH")
    if previous["subject"]["kind"] != "test":
        codes.add("SUBJECT_KIND_UNSUPPORTED")  # a mutant verdict has its own rule (RESULTS.md section 5); not modelled here
    px, cx = previous["graph"]["extractor"], current["graph"]["extractor"]
    if px != cx:
        codes.add("EXTRACTOR_MISMATCH")
    for e in (previous, current):
        if e.get("stability") == "unstable":
            # a test whose repeated runs have disagreed (a timing check, a draw of cases) is not a function of its code: the earlier result
            # does not stand for another state however equal the keys are. `stability` is optional; absent means nobody has recorded it.
            codes.add("NONDETERMINISTIC_SUBJECT")
        g = e["graph"]
        verified = assurance.extractors.get(g["extractor"])
        # two separate statements, both needed: the extraction was complete in the extractor's declared scope (its own report),
        # and the constructs it found are ones the extractor is VERIFIED for (the caller's assurance). Neither implies the other.
        if g["analysis"]["status"] != "COMPLETE" or g["analysis"]["unresolved"]:
            codes.add("ANALYSIS_INCOMPLETE")
        if verified is None:
            codes.add("EXTRACTOR_NOT_ALLOWED")
        elif not set(g["constructs"]) <= verified:
            codes.add("CONSTRUCT_NOT_VERIFIED")
        if not g["subject_in_graph"]:
            codes.add("SUBJECT_NODE_MISSING")
        if g["opaque"] or e["opaque_reads"]:
            codes.add("OPAQUE_DEPENDENCY")
    return codes, shape


def evaluate_reuse(previous, current, assurance):
    compared = {}
    codes, shape = _unverifiable(previous, current, assurance)
    if shape:
        compared["shape_errors"] = shape
    if codes:
        return Decision(UNVERIFIABLE, tuple(sorted(codes)), compared)

    for name, key in (("CODE_CHANGED", "code_closure_digest"), ("DATA_CHANGED", "data_dependencies_digest"),
                      ("ENVIRONMENT_CHANGED", "environment_digest")):
        compared[key] = {"previous": previous[key], "current": current[key]}
        if previous[key] != current[key]:
            codes.add(name)
    compared["extractor"] = previous["graph"]["extractor"]
    compared["subject"] = dict(previous["subject"])
    compared["previous_verdict"] = previous["verdict"]
    compared["previous_evidence"] = (previous.get("provenance") or {}).get("evidence_id")
    if previous["verdict"] != "pass":
        codes.add("PREVIOUS_TEST_FAILED")
    if codes:
        return Decision(RERUN_REQUIRED, tuple(sorted(codes)), compared)
    return Decision(REUSE_ELIGIBLE, (ALL_CONDITIONS_HOLD,), compared)
