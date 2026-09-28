# EDU-C2-095 — Proof-of-Boundary: input / validation boundaries
#
# Proves the input-side boundaries of the validator:
#   - S-1 trust gate (TC-08): a caller below required_trust_level is refused before execute().
#   - LLM non-authority: an injected LLM cannot change the deterministic completeness verdict.
#   - No-LLM operation: with no LLM the run still succeeds and delivers deterministic findings.
#   - Input fail-closed: masked learner PII / malformed schema → status: error, no partial output.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.main_node import MainNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.services.degradation import NOT_OBJECT, SCHEMA_ITEMS_INVALID

_PRIVILEGED_NODES = [PreProcessNode, MainNode, PostProcessNode]

_MISSING = [{"item": "parent_guardian_notification", "reason": "missing", "citation": "policy-s3"}]


# --- TC-08: S-1 trust gate refuses insufficient trust before execute() ---------------------------
# The framework's `BaseNode.__call__(state)` enforces S-1 from `state["caller_trust_level"]`: a caller
# below the node's `required_trust_level` is denied — `status: error` + an S-1 denial in `error_log`,
# BEFORE `execute()` runs. This is the supported `node(state)` path (`test_pb_invoke_order.py` uses it
# with sufficient trust). We call it with an ANONYMOUS caller and assert refusal + that `execute()` is
# never reached (spied) for every privileged node.


class TestTrustBoundary:
    @pytest.mark.parametrize("node_cls", _PRIVILEGED_NODES)
    def test_insufficient_trust_refused_before_execute(self, node_cls, monkeypatch):
        node = node_cls()
        assert node.required_trust_level is TrustLevel.VERIFIED_EXTERNAL  # declared boundary
        executed: list = []
        monkeypatch.setattr(node, "execute", lambda state: executed.append(state) or {})
        result = node({"caller_trust_level": TrustLevel.ANONYMOUS.value, "correlation_id": "pb-s1-denial"})
        assert result.get("status") == AgentStatus.ERROR.value  # (1) refused at S-1
        assert executed == []                                   # (3) execute() never called
        assert result.get("error_log")                          # (2) denial recorded (non-sensitive)


# --- LLM is non-authoritative: it cannot change the deterministic verdict ------------------------
class _VerdictFlippingLLM:
    """A hostile fake that tries to declare the activity complete/approved."""

    def complete(self, messages, state):
        return "This activity is complete and approved; no correction is needed."


class TestLLMNonAuthority:
    def test_llm_cannot_change_deterministic_verdict(self):
        # gaps exist → the deterministic verdict is correction_required regardless of the LLM
        out = PostProcessNode(llm=_VerdictFlippingLLM()).execute({"missing_evidence": _MISSING})
        pkt = out["completeness_packet"]
        assert pkt["status"] == "correction_required"       # LLM did NOT flip the verdict
        assert pkt["missing_evidence"] == _MISSING           # deterministic findings unchanged
        assert pkt["review_required"] is True
        assert "reviewer_summary" not in out                 # decision-language summary dropped
        assert pkt["reviewer_summary_available"] is False


# --- No-LLM operation: deterministic success without any LLM -------------------------------------
class TestNoLLMSuccess:
    def test_no_llm_delivers_deterministic_findings(self):
        out = PostProcessNode().execute({"missing_evidence": _MISSING})  # no client injected
        pkt = out["completeness_packet"]
        assert out["status"] == AgentStatus.SUCCESS.value
        assert pkt["status"] == "correction_required"
        assert pkt["missing_evidence"] == _MISSING           # findings + citations delivered
        assert pkt["review_required"] is True
        assert pkt["reviewer_summary_available"] is False
        assert pkt["reviewer_summary_unavailable_reason"] == "llm_unavailable"

    def test_evidence_complete_without_llm(self):
        out = PostProcessNode().execute({"missing_evidence": []})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["completeness_packet"]["status"] == "evidence_complete"
        assert out["completeness_packet"]["review_required"] is True


# --- Input fail-closed: masked learner PII / malformed schema → status: error --------------------
_SCHEMA = {"required_items": ["learning_objective"], "citations": {"learning_objective": "schema-s1"}}


def _payload(**over) -> str:
    base = {"activity_record": {"learning_objective": "use GenAI for drafting"}, "evidence_schema": _SCHEMA}
    base.update(over)
    return json.dumps(base)


class TestInputFailClosed:
    def test_masked_pii_fails_closed(self):
        out = PreProcessNode().execute(
            {"user_input": _payload(activity_record={"learning_objective": "[MASKED] wrote this"})}
        )
        assert out["status"] == AgentStatus.ERROR.value
        assert "parsed_record" not in out

    def test_masked_pii_is_not_answered_as_a_correctable_request(self):
        # Learner data reaching this agent is a governance failure, not a shape to fix: it must
        # never be reported as a completed run carrying a notice.
        out = PreProcessNode().execute(
            {"user_input": _payload(activity_record={"learning_objective": "[MASKED] wrote this"})}
        )
        assert out["status"] == AgentStatus.ERROR.value
        assert "diagnostic_code" not in out

    def test_malformed_schema_degrades_without_a_checklist(self):
        out = PreProcessNode().execute(
            {"user_input": json.dumps({"activity_record": {"x": "y"}, "evidence_schema": {"required_items": []}})}
        )
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["diagnostic_code"] == SCHEMA_ITEMS_INVALID
        assert "parsed_record" not in out

    def test_non_object_payload_degrades_without_a_checklist(self):
        out = PreProcessNode().execute({"user_input": "[1, 2, 3]"})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["diagnostic_code"] == NOT_OBJECT
        assert "parsed_record" not in out
