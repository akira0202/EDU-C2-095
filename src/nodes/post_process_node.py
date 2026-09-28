"""AgentCore Platform v1.0"""

from typing import Any, ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

from src.services.service import (
    assemble_completeness_packet,
    build_summary_messages,
    validate_summary,
)
from src.services.degradation import explain_degradation
from src.services.failure_message import GENERATION_DEGRADED, explain
from src.services.progress import emit_progress


class PostProcessNode(FunctionNode):
    """Assemble the deterministic, evidence-cited completeness packet (LLM-free), then an OPTIONAL,
    non-authoritative reviewer summary. Any LLM problem (missing secret / provider error / timeout /
    unsafe summary) drops only the summary and returns ``status:success`` with an allowlisted reason.
    The ``@final`` S-3 gate is a terminal whole-packet backstop only — summary safety is enforced in
    ``execute()`` before the summary enters the return dict."""

    # S-1: caller is a school/board governance system — VERIFIED_EXTERNAL, not ANONYMOUS.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: dict[str, Any] | None = None, llm: Any = None) -> None:
        # The LLM adapter is injected by the graph (tests inject a fake). A ``None`` client
        # (incl. the PB-6 no-arg construction) degrades gracefully to the deterministic packet.
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        emit_progress("Preparing the response...")
        # Upstream pre_process failed closed — no partial packet (docs/02 Data Flow).
        if state.get("status") == AgentStatus.ERROR.value:
            return {}

        # Already answered upstream: no checklist exists by design. The output is the reason
        # alone, not the packet shape with zero gaps — an empty checklist reads as "checked,
        # nothing missing", which is the opposite of what happened. Every value is a constant,
        # so nothing the refused request carried reaches the caller.
        degraded_code = state.get("diagnostic_code")
        if degraded_code:
            emit_trace_event("completeness_packet_degraded", {"reason": degraded_code}, state)
            return {
                "result": None,
                "formatted_output": explain_degradation(str(degraded_code)),
                "status": AgentStatus.SUCCESS.value,
            }

        missing_evidence = state.get("missing_evidence", [])
        packet = assemble_completeness_packet(missing_evidence)  # authoritative, LLM-free

        reviewer_summary: str | None = None
        reason: str | None = None
        if self._llm is None:
            emit_progress(GENERATION_DEGRADED)
            reason = "llm_unavailable"
        else:
            try:
                emit_progress("Writing the reviewer summary...")
                raw = self._llm.complete(build_summary_messages(packet), state)
                allowed = {
                    c for g in missing_evidence if isinstance(g, dict) if isinstance(c := g.get("citation"), str)
                }
                safe = validate_summary(raw, allowed)
                # validate_summary screens structured learner-PII, banned decision/quality language,
                # control chars, length, and fabricated citations. Credential leakage is screened here
                # with the shared substrate credential detector (reuse it; do not re-implement it): a hit
                # drops the summary and degrades to success, pre-empting the baseline S-3 BLOCK so the
                # deterministic packet is preserved rather than erroring the whole run.
                if safe is None or detect_credentials(safe):
                    reason = "llm_response_rejected"  # unsafe / PII / credential / malformed -> drop
                else:
                    reviewer_summary = safe
            except Exception as exc:  # best-effort: an LLM failure never errors the run
                # MissingSecret (unbound key) is distinct from a provider/timeout failure.
                emit_progress(GENERATION_DEGRADED)
                reason = "llm_unavailable" if type(exc).__name__ == "MissingSecret" else "llm_timeout"

        if reviewer_summary is not None:
            packet["reviewer_summary_available"] = True
        else:
            packet["reviewer_summary_available"] = False
            packet["reviewer_summary_unavailable_reason"] = reason

        # S-4: one domain event; degraded metadata only — never the prompt / summary / any credential.
        payload: dict[str, Any] = {
            "status": packet["status"],
            "missing_count": len(missing_evidence),
            "reviewer_summary_available": packet["reviewer_summary_available"],
        }
        if reason:
            payload["reviewer_summary_unavailable_reason"] = reason
        emit_trace_event("completeness_packet_generated", payload, state)

        delta: dict[str, Any] = {
            "completeness_packet": packet,
            "result": packet,
            "status": AgentStatus.SUCCESS.value,
        }
        if reviewer_summary is not None:
            delta["reviewer_summary"] = reviewer_summary
        return delta

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        # Terminal S-3 backstop over the whole returned packet: a ``correction_required`` verdict
        # must carry the missing-evidence findings it claims — silently dropping them is worse than
        # no packet. Reviewer-summary safety is handled in execute() (drop + degrade), so it never
        # reaches this gate. Raising here marks the node ERROR (a genuine terminal violation).
        packet = result.get("completeness_packet")
        if isinstance(packet, dict):
            if packet.get("status") == "correction_required" and not packet.get("missing_evidence"):
                emit_progress(explain("packet_findings_dropped"))
                raise SecurityViolationError(
                    "PostProcessNode: completeness packet dropped its missing-evidence findings"
                )
        return result
