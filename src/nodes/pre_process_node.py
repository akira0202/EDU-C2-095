"""AgentCore Platform v1.0"""

# Node contract (agents_layer_design.md §1):
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return AgentStatus.<X>.value strings for status assignments
#  - Never import from mediator/, api/, or other agents

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import (
    contains_mask_marker,
    parse_activity_payload,
    resolve_evidence_schema,
)
from src.services.degradation import (
    ACTIVITY_RECORD_SHAPE,
    DEGRADATION_CODES,
    EMPTY_OR_MISSING,
    MALFORMED_JSON,
    NOT_OBJECT,
    TOO_DEEP,
)
from src.services.failure_message import explain
from src.services.progress import emit_progress

# S-2 business-rule guard: truncate a raw payload larger than this before it reaches
# execute() (execute()'s JSON parse then fails cleanly on the truncated payload).
# 64 KiB comfortably covers a learning-activity record + schema + policy extracts.
_MAX_PAYLOAD_CHARS = 65536

# C0 control characters except tab / newline / carriage-return, plus DEL. Stripped
# by the non-raising input guard before execute() parses the payload.
_CONTROL_ORDINALS = frozenset({c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)} | {0x7F})


class PreProcessNode(FunctionNode):
    """Parse and shallow-validate the caller-supplied activity payload; answer a request the
    caller can correct (the envelope, the activity record, the school's evidence schema) by
    completing the run with a ``diagnostic_code`` instead of erroring; fail closed on
    learner-identifying data (baseline ``[MASKED]`` marker); resolve the caller-supplied evidence
    schema and policy citations."""

    # S-1: the caller is a school/board governance system supplying its
    # own approved schema and governance content — VERIFIED_EXTERNAL, not ANONYMOUS.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        # S-2 domain guard: input hooks must NOT raise. Defensively truncate an
        # oversized payload and strip control characters before execute() (which then
        # parses the cleaned payload and fails cleanly on any malformed result). The
        # learner-PII fail-closed is performed in execute() on the baseline ``[MASKED]``
        # marker, NOT here.
        raw = state.get("user_input", "")
        if isinstance(raw, str):
            cleaned = raw[:_MAX_PAYLOAD_CHARS]
            if any(ord(ch) in _CONTROL_ORDINALS for ch in cleaned):
                cleaned = "".join(ch for ch in cleaned if ord(ch) not in _CONTROL_ORDINALS)
            if cleaned != raw:
                state = {**state, "user_input": cleaned}
        return state

    def _degrade(self, code: str, state: dict[str, Any]) -> dict[str, Any]:
        """Complete the run carrying the reason: the caller can correct this request.

        Only the allow-listed code is recorded — never payload content — and no error is logged,
        because nothing failed. ``post_process`` turns the code into the caller-facing sentence,
        so no progress line is sent here: the notice is the output, not a message alongside it.
        A code this agent has no notice for is a defect rather than a caller correction, so it
        stays fail-closed.
        """
        if code not in DEGRADATION_CODES:
            emit_progress(explain("contract_violation"))
            emit_trace_event("activity_record_rejected", {"reason": code}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: unclassified rejection {code}"],
            }
        emit_trace_event("activity_record_degraded", {"reason": code}, state)
        return {"diagnostic_code": code, "status": AgentStatus.SUCCESS.value}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_progress("Checking the request...")
        user_input = state.get("user_input", "")
        if not isinstance(user_input, str) or not user_input.strip():
            return self._degrade(EMPTY_OR_MISSING, state)

        try:
            payload = parse_activity_payload(user_input)
        except ValueError as exc:
            # The parse failure is one of three shapes (not JSON / too deep / not an object).
            # Map it to the caller-facing sentence the other rejection paths already use, so a
            # caller is told what to change instead of seeing only the runner's generic failure.
            text = str(exc)
            if "not valid JSON" in text:
                code = MALFORMED_JSON
            elif "nesting" in text:
                code = TOO_DEEP
            else:
                code = NOT_OBJECT
            return self._degrade(code, state)

        activity_record = payload.get("activity_record")
        if not isinstance(activity_record, dict):
            return self._degrade(ACTIVITY_RECORD_SHAPE, state)

        # Learner-PII fail-closed: the baseline S-2 scan masks detected PII to
        # ``[MASKED]`` before execute(). Its presence in the untrusted activity record
        # means learner-identifying data was supplied — reject with no output. A
        # caller-supplied literal marker is rejected the same way (safe side). The
        # trusted evidence_schema / policy_extracts are NOT screened here.
        if contains_mask_marker(activity_record):
            emit_trace_event(
                "activity_record_rejected",
                {"reason": "learner_pii_present"},
                state,
            )
            emit_progress(explain("learner_pii_present"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: learner-identifying data present; record rejected (fail closed)"],
            }

        required_items, citations, schema_code = resolve_evidence_schema(
            payload.get("evidence_schema"), payload.get("policy_extracts")
        )
        if schema_code:
            return self._degrade(schema_code, state)

        emit_trace_event(
            "activity_record_validated",
            {"required_item_count": len(required_items)},
            state,
        )

        return {
            "parsed_record": activity_record,
            "schema_required_items": required_items,
            "policy_citations": citations,
            "status": AgentStatus.SUCCESS.value,
        }
