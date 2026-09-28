"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# alternatives). LangGraph checkpoints use msgpack serialization, so only
# plain serializable fields are allowed. Do NOT add credentials or secrets.
#
# No learner-identifying data is ever stored in State: the activity record is
# de-identified in pre_process, which fails closed on learner-identifying data
# before any field below is written.

from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """EDU-C2-095 — School GenAI Learning-Activity Evidence Completeness Validator state.

    Domain fields only. All shared fields (user_input, validated_input, result,
    formatted_output, status, session_id, node_history, error_log, hitl_*, etc.)
    are inherited from AgentState and are NOT redeclared here.
    """

    # --- pre_process outputs ---
    # De-identified parsed activity record: learning_objective, use_context,
    # teacher_oversight, learner_facing_guidance, personal_information_handling,
    # parent_guardian_notification, governing_policy_reference.
    parsed_record: NotRequired[dict[str, Any]]
    # Effective required-evidence-item list, resolved from the caller
    # (school/board)-supplied, versioned approved evidence_schema.
    schema_required_items: NotRequired[list[str]]
    # item -> policy/schema section reference, the citation backing for each
    # finding, resolved from the supplied schema / policy_extracts.
    policy_citations: NotRequired[dict[str, Any]]

    # --- main outputs (deterministic) ---
    # Per-item presence findings: {item, present, issue}.
    item_findings: NotRequired[list[dict[str, Any]]]
    # Internal-consistency findings across the record's declared items.
    consistency_findings: NotRequired[list[dict[str, Any]]]
    # Cited gaps: {item, reason, citation} (citation = supplied schema/policy section).
    missing_evidence: NotRequired[list[dict[str, Any]]]

    # --- post_process outputs ---
    # Final evidence-cited packet: {status: "evidence_complete" | "correction_required",
    # missing_evidence, review_required: true, reviewer_summary_available: bool,
    # reviewer_summary_unavailable_reason?: "llm_unavailable" | "llm_timeout" |
    # "llm_response_rejected"}. review_required is the always-on human-review flag; the
    # reviewer_summary_* fields carry the deterministic-degrade signal; the reason is an
    # allowlisted code, never a provider exception body.
    completeness_packet: NotRequired[dict[str, Any]]
    # Optional, non-authoritative plain-text summary for the human reviewer (LLM-phrased
    # from determined findings only) — present only when the LLM is available; absent on degrade.
    reviewer_summary: NotRequired[str]

    # Allowlisted, non-sensitive code naming why no completeness checklist was produced, on a run
    # the caller can correct (src/services/degradation.py). Absent on a normal run and on a
    # fail-closed one; never carries a request value, record field, schema key or policy reference.
    diagnostic_code: NotRequired[str]
