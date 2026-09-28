"""AgentCore Platform v1.0"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import (
    assemble_missing_evidence,
    check_internal_consistency,
    check_item_presence,
)
from src.services.progress import emit_progress


class MainNode(FunctionNode):
    """Deterministic per-item presence + internal-consistency checks of the activity
    record against the caller-supplied schema, and assembly of the cited
    missing-evidence gaps. No LLM, no quality judgement, no approve/reject."""

    # S-1: the caller is a school/board governance system supplying
    # its own approved schema and governance content — VERIFIED_EXTERNAL, not ANONYMOUS.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Upstream pre_process already answered this request — it failed closed on learner PII,
        # or it completed with a diagnostic_code because the caller can correct the request. Either
        # way there is no parsed record to check, so short-circuit: no partial completeness output
        # is produced and finalize still runs (docs/02_design.md Data Flow).
        emit_progress("Checking the activity record against the checklist...")
        if state.get("status") == AgentStatus.ERROR.value or state.get("diagnostic_code"):
            return {}

        record = state.get("parsed_record", {})
        required_items = state.get("schema_required_items", [])
        citations = state.get("policy_citations", {})

        item_findings = check_item_presence(record, required_items)
        consistency_findings = check_internal_consistency(record, required_items)
        missing_evidence = assemble_missing_evidence(item_findings, consistency_findings, citations)

        emit_trace_event(
            "evidence_completeness_checked",
            {
                "required_item_count": len(required_items),
                "missing_count": sum(1 for f in item_findings if not f["present"]),
                "inconsistent_count": sum(1 for c in consistency_findings if not c.get("consistent", True)),
            },
            state,
        )

        return {
            "item_findings": item_findings,
            "consistency_findings": consistency_findings,
            "missing_evidence": missing_evidence,
            "status": AgentStatus.SUCCESS.value,
        }
