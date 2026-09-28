# EDU-C2-095 — Unit Tests: PostProcessNode (completeness packet + optional LLM summary + S-3)

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus

from src.nodes.post_process_node import PostProcessNode

_MISSING = [{"item": "parent_guardian_notification", "reason": "missing", "citation": "policy-s3"}]


class _SafeLLM:
    def complete(self, messages, state):
        return (
            "Some evidence items still need attention before this activity is used; "
            "teacher and school-leader review remains required."
        )


class _UnsafeLLM:
    def complete(self, messages, state):
        return "This activity is approved for use."  # 'approve' is banned


class _FabricatedCitationLLM:
    def complete(self, messages, state):
        return "Please refer to policy-s99 for the missing item; review remains required."


class _EmailPiiLLM:
    def complete(self, messages, state):
        return "Student alice@example.edu requires review before this activity is used."


class _PhonePiiLLM:
    def complete(self, messages, state):
        return "Guardian on 090-1234-5678 should be reached; review remains required."


class _StudentIdPiiLLM:
    def complete(self, messages, state):
        return "Record for student 1234567 is incomplete; review remains required."


class _CredentialLLM:
    # Emits a token the (stubbed/real) shared credential detector flags.
    def complete(self, messages, state):
        return "See key SECRET_TOKEN_abcdef1234567890 in the record; review remains required."


class MissingSecret(Exception):
    """Named to match the framework's unbound-secret exception (node keys on the class name)."""


class _MissingSecretLLM:
    def complete(self, messages, state):
        raise MissingSecret("AZURE_OPENAI_API_KEY")


class _TimeoutLLM:
    def complete(self, messages, state):
        raise TimeoutError("provider timeout")


class TestPostProcessNode:
    def test_correction_required_packet_with_gaps(self):
        out = PostProcessNode(llm=_SafeLLM()).execute({"missing_evidence": _MISSING})
        pkt = out["completeness_packet"]
        assert out["status"] == AgentStatus.SUCCESS.value
        assert pkt["status"] == "correction_required"
        assert pkt["missing_evidence"] == _MISSING
        assert pkt["review_required"] is True
        assert out["result"] is pkt

    def test_evidence_complete_when_no_gaps(self):
        out = PostProcessNode(llm=_SafeLLM()).execute({"missing_evidence": []})
        assert out["completeness_packet"]["status"] == "evidence_complete"

    def test_safe_summary_attached(self):
        out = PostProcessNode(llm=_SafeLLM()).execute({"missing_evidence": _MISSING})
        assert out["completeness_packet"]["reviewer_summary_available"] is True
        assert "reviewer_summary" in out
        assert "reviewer_summary_unavailable_reason" not in out["completeness_packet"]

    def test_unsafe_summary_dropped_degrade_rejected(self):
        out = PostProcessNode(llm=_UnsafeLLM()).execute({"missing_evidence": _MISSING})
        pkt = out["completeness_packet"]
        assert out["status"] == AgentStatus.SUCCESS.value
        assert "reviewer_summary" not in out
        assert pkt["reviewer_summary_available"] is False
        assert pkt["reviewer_summary_unavailable_reason"] == "llm_response_rejected"
        assert pkt["missing_evidence"] == _MISSING  # deterministic gaps preserved

    def test_fabricated_citation_dropped(self):
        out = PostProcessNode(llm=_FabricatedCitationLLM()).execute({"missing_evidence": _MISSING})
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_response_rejected"
        assert "reviewer_summary" not in out

    def test_email_pii_summary_dropped_packet_preserved(self):
        out = PostProcessNode(llm=_EmailPiiLLM()).execute({"missing_evidence": _MISSING})
        pkt = out["completeness_packet"]
        assert out["status"] == AgentStatus.SUCCESS.value
        assert "reviewer_summary" not in out  # learner PII never reaches State/output
        assert pkt["reviewer_summary_available"] is False
        assert pkt["reviewer_summary_unavailable_reason"] == "llm_response_rejected"
        assert pkt["missing_evidence"] == _MISSING  # deterministic packet preserved

    def test_phone_pii_summary_dropped(self):
        out = PostProcessNode(llm=_PhonePiiLLM()).execute({"missing_evidence": _MISSING})
        assert "reviewer_summary" not in out
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_response_rejected"

    def test_student_id_pii_summary_dropped(self):
        out = PostProcessNode(llm=_StudentIdPiiLLM()).execute({"missing_evidence": _MISSING})
        assert "reviewer_summary" not in out
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_response_rejected"

    def test_credential_summary_dropped_via_substrate(self):
        out = PostProcessNode(llm=_CredentialLLM()).execute({"missing_evidence": _MISSING})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert "reviewer_summary" not in out
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_response_rejected"

    def test_no_llm_degrades_unavailable(self):
        out = PostProcessNode().execute({"missing_evidence": _MISSING})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_unavailable"
        assert "reviewer_summary" not in out

    def test_missing_secret_degrades_unavailable(self):
        out = PostProcessNode(llm=_MissingSecretLLM()).execute({"missing_evidence": _MISSING})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_unavailable"

    def test_provider_error_degrades_timeout(self):
        out = PostProcessNode(llm=_TimeoutLLM()).execute({"missing_evidence": _MISSING})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["completeness_packet"]["reviewer_summary_unavailable_reason"] == "llm_timeout"

    def test_short_circuits_on_upstream_error(self):
        out = PostProcessNode(llm=_SafeLLM()).execute({"status": AgentStatus.ERROR.value})
        assert out == {}

    def test_s3_gate_raises_on_dropped_findings(self):
        node = PostProcessNode()
        bad = {"completeness_packet": {"status": "correction_required", "missing_evidence": []}}
        try:
            node._extra_security_gate_output(bad)
            raise AssertionError("expected SecurityViolationError")
        except SecurityViolationError:
            pass

    def test_s3_gate_passes_valid_packet(self):
        node = PostProcessNode()
        good = {"completeness_packet": {"status": "correction_required", "missing_evidence": _MISSING}}
        assert node._extra_security_gate_output(good) == good
