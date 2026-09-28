# EDU-C2-095 — Proof-of-Boundary: output boundary + domain-event emission
#
#   - Output boundary: no unsafe summary (approval/rejection, quality judgment, learner decision,
#     fabricated citation) and no learner data cross the node's return delta; terminal S-3 blocks a
#     silent finding-drop; the deterministic checklist is preserved on optional-summary degrade.
#   - PB-1 / TC-11: each node's execute() emits its domain event (spied on the NODE module's own
#     emit_trace_event symbol — the framework lifecycle emits are separate and covered by PB-6).

import json

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus

import src.nodes.main_node as main_mod
import src.nodes.post_process_node as post_mod
import src.nodes.pre_process_node as pre_mod
from src.nodes.post_process_node import PostProcessNode

_MISSING = [{"item": "parent_guardian_notification", "reason": "missing", "citation": "policy-s3"}]


def _spy_domain_emit(module):
    """Record domain event_types emitted by a node module's `execute()`, and a restore fn.
    Patches the node module's own imported `emit_trace_event` symbol (nodes do
    `from shared.utils.audit_logger import emit_trace_event`), NOT the framework base_node emit."""
    events: list = []
    original = module.emit_trace_event
    module.emit_trace_event = lambda event_type, payload, state: events.append(event_type)

    def restore() -> None:
        module.emit_trace_event = original

    return events, restore


# --- PB-1 / TC-11: each node emits its domain event on its work path ------------------------------
class TestDomainEventEmission:
    def test_pre_process_emits_on_success(self):
        payload = json.dumps(
            {"activity_record": {"learning_objective": "use GenAI for drafting"},
             "evidence_schema": {"required_items": ["learning_objective"]}}
        )
        events, restore = _spy_domain_emit(pre_mod)
        try:
            pre_mod.PreProcessNode().execute({"user_input": payload})
        finally:
            restore()
        assert "activity_record_validated" in events

    def test_pre_process_emits_on_degrade(self):
        events, restore = _spy_domain_emit(pre_mod)
        try:
            pre_mod.PreProcessNode().execute({"user_input": "{not json"})  # notice path
        finally:
            restore()
        assert "activity_record_degraded" in events

    def test_pre_process_emits_on_fail_closed(self):
        payload = json.dumps(
            {"activity_record": {"learning_objective": "[MASKED] wrote this"},
             "evidence_schema": {"required_items": ["learning_objective"]}}
        )
        events, restore = _spy_domain_emit(pre_mod)
        try:
            pre_mod.PreProcessNode().execute({"user_input": payload})  # fail-closed path
        finally:
            restore()
        assert "activity_record_rejected" in events

    def test_post_process_emits_on_degrade(self):
        events, restore = _spy_domain_emit(post_mod)
        try:
            post_mod.PostProcessNode().execute({"diagnostic_code": "malformed_json"})
        finally:
            restore()
        assert "completeness_packet_degraded" in events

    def test_main_emits_on_completeness_check(self):
        state = {
            "parsed_record": {"learning_objective": "x"},
            "schema_required_items": ["learning_objective", "parent_guardian_notification"],
            "policy_citations": {"parent_guardian_notification": "policy-s3"},
        }
        events, restore = _spy_domain_emit(main_mod)
        try:
            main_mod.MainNode().execute(state)
        finally:
            restore()
        assert "evidence_completeness_checked" in events

    def test_post_process_emits_on_packet(self):
        events, restore = _spy_domain_emit(post_mod)
        try:
            post_mod.PostProcessNode().execute({"missing_evidence": _MISSING})
        finally:
            restore()
        assert "completeness_packet_generated" in events


# --- Output boundary: nothing unsafe crosses the node's return delta ------------------------------
class _ApprovalLLM:
    def complete(self, messages, state):
        return "This activity is approved and of high quality; no correction needed."


class _LearnerDataLLM:
    def complete(self, messages, state):
        return "Contact guardian at alice@example.edu about student 1234567."


class _FabricatedCitationLLM:
    def complete(self, messages, state):
        return "See policy-s99 for the missing item; review remains required."


class TestOutputBoundary:
    def test_decision_and_quality_summary_does_not_cross(self):
        out = PostProcessNode(llm=_ApprovalLLM()).execute({"missing_evidence": _MISSING})
        pkt = out["completeness_packet"]
        assert out["status"] == AgentStatus.SUCCESS.value
        assert "reviewer_summary" not in out                       # decision/quality text dropped
        assert pkt["status"] == "correction_required"              # deterministic verdict preserved
        assert pkt["missing_evidence"] == _MISSING
        assert pkt["reviewer_summary_available"] is False
        assert pkt["reviewer_summary_unavailable_reason"] == "llm_response_rejected"

    def test_learner_data_summary_does_not_cross(self):
        out = PostProcessNode(llm=_LearnerDataLLM()).execute({"missing_evidence": _MISSING})
        assert "reviewer_summary" not in out                       # learner PII never surfaced
        assert out["completeness_packet"]["reviewer_summary_available"] is False

    def test_fabricated_citation_does_not_cross(self):
        out = PostProcessNode(llm=_FabricatedCitationLLM()).execute({"missing_evidence": _MISSING})
        assert "reviewer_summary" not in out
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_response_rejected"

    def test_degrade_preserves_checklist(self):
        out = PostProcessNode().execute({"missing_evidence": _MISSING})  # no LLM
        pkt = out["completeness_packet"]
        assert out["status"] == AgentStatus.SUCCESS.value           # degrade keeps success
        assert pkt["missing_evidence"] == _MISSING                  # checklist preserved
        assert pkt["review_required"] is True
        assert pkt["reviewer_summary_available"] is False
        assert pkt["reviewer_summary_unavailable_reason"] == "llm_unavailable"

    def test_terminal_s3_blocks_silent_finding_drop(self):
        node = PostProcessNode()
        bad = {"completeness_packet": {"status": "correction_required", "missing_evidence": []}}
        try:
            node._extra_security_gate_output(bad)
            raise AssertionError("expected SecurityViolationError")
        except SecurityViolationError:
            pass

    def test_terminal_s3_passes_valid_packet(self):
        node = PostProcessNode()
        good = {"completeness_packet": {"status": "correction_required", "missing_evidence": _MISSING}}
        assert node._extra_security_gate_output(good) == good
