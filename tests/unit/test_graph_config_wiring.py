"""Graph-level integration test — proves the Cat 2 flat composition + the deployed construction path
(requires the framework — runs in CI).

Constructs the real outer graph and exercises the node the graph built: a config-supplied `llm` (a
fake) reaches PostProcessNode; a config-less deploy still injects a real ReviewerSummaryLLM adapter;
and a real end-to-end `invoke` runs the flat backbone, degrades the optional summary safely when the
adapter has no secret, and surfaces the completeness packet + reviewer summary to the caller
(`get_output` override).
"""

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import SchoolGenAILearningActivityEvidenceCompletenessValidatorGraph as Graph
from src.services.degradation import (
    MALFORMED_JSON,
    NOT_OBJECT,
    SCHEMA_MISSING,
    explain_degradation,
)

_SCHEMA = {
    "required_items": ["learning_objective", "parent_guardian_notification"],
    "citations": {"learning_objective": "schema-s1"},
}


def _req(**over):
    base = {
        "activity_record": {
            "learning_objective": "use GenAI for drafting",
            "use_context": "grade 8 English",
        },
        "evidence_schema": _SCHEMA,
        "policy_extracts": {"parent_guardian_notification": "policy-s3"},
    }
    base.update(over)
    return json.dumps(base)


def _ctx():
    # A VERIFIED_EXTERNAL caller — the backbone nodes gate at that level; an ANONYMOUS caller is refused.
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


class _FakeLLM:
    """Best-effort summary — a safe fake so the composition surfaces a reviewer_summary."""

    def complete(self, messages, state):
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        return (
            "Some evidence items still need attention before this activity is used; "
            "teacher and school-leader review remains required."
        )


class TestGraphWiring:
    def test_flat_backbone_nodes_registered(self):
        from src.nodes.main_node import MainNode
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        g = Graph(config={})
        g.register_nodes()
        assert isinstance(g._nodes["pre_process"], PreProcessNode)
        assert isinstance(g._nodes["main"], MainNode)  # flat: main is a FunctionNode, not a GraphNode
        assert isinstance(g._nodes["post_process"], PostProcessNode)

    def test_config_injects_fake_llm(self):
        fake = _FakeLLM()
        g = Graph(config={"llm": fake})
        g.register_nodes()
        assert g._nodes["post_process"]._llm is fake

    def test_default_deploy_injects_real_adapter(self):
        # server.py constructs Graph() with NO config; the graph must still inject the default
        # keyless ReviewerSummaryLLM into post_process so the summary path is reachable in deployment.
        from src.services.llm import ReviewerSummaryLLM

        g = Graph()
        g.register_nodes()
        assert isinstance(g._nodes["post_process"]._llm, ReviewerSummaryLLM)

    def test_end_to_end_invoke_surfaces_packet_and_summary(self):
        g = Graph(config={"llm": _FakeLLM()})
        result = g.invoke(_req(), ctx=_ctx())
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        nh = result.get("node_history", [])
        assert "PreProcessNode" in nh and "MainNode" in nh and "PostProcessNode" in nh, f"composition: {nh}"
        # the completeness packet reaches the caller as `output`
        out = result.get("output")
        assert isinstance(out, dict), f"output must be the packet dict: {out!r}"
        assert out.get("status") in ("evidence_complete", "correction_required")
        assert out.get("review_required") is True
        assert out.get("reviewer_summary_available") is True
        # the reviewer summary reaches the caller only because of the get_output override
        assert result.get("reviewer_summary"), "reviewer_summary must be surfaced to the caller"

    def test_end_to_end_degrades_without_secret(self):
        # No injected client + no AZURE_OPENAI_* in the test env → the default adapter's per-request
        # secret resolution fails → the optional summary is dropped, the run still succeeds.
        g = Graph()  # default ReviewerSummaryLLM, no secret bound
        result = g.invoke(_req(), ctx=_ctx())
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        out = result.get("output")
        assert isinstance(out, dict) and out.get("reviewer_summary_available") is False
        assert out.get("review_required") is True  # deterministic core still complete
        assert result.get("reviewer_summary", "") == ""  # degrade → no summary text surfaced


class TestCallerCorrectableCompletesWithANotice:
    """A request the caller can correct is answered at the deployed boundary: the run completes,
    the notice is the whole output, and no checklist or reviewer summary is produced. An empty
    checklist would read as "checked, nothing missing" — the opposite of what happened."""

    def _assert_notice_only(self, result, code):
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        assert result.get("diagnostic_code") == code
        assert result.get("output") == explain_degradation(code)
        assert result.get("reviewer_summary", "") == ""

    def test_malformed_request_completes_with_a_notice(self):
        g = Graph(config={"llm": _FakeLLM()})
        self._assert_notice_only(g.invoke("{ not valid json", ctx=_ctx()), MALFORMED_JSON)

    def test_request_that_is_not_an_object_completes_with_a_notice(self):
        g = Graph(config={"llm": _FakeLLM()})
        self._assert_notice_only(g.invoke('["a"]', ctx=_ctx()), NOT_OBJECT)

    def test_missing_school_schema_completes_with_a_notice(self):
        g = Graph(config={"llm": _FakeLLM()})
        result = g.invoke(json.dumps({"activity_record": {"learning_objective": "x"}}), ctx=_ctx())
        self._assert_notice_only(result, SCHEMA_MISSING)

    def test_the_checklist_steps_do_not_run_on_a_notice(self):
        # main must not assemble a partial checklist from a record that was never parsed.
        g = Graph(config={"llm": _FakeLLM()})
        result = g.invoke("{ not valid json", ctx=_ctx())
        assert not result.get("missing_evidence") and not result.get("completeness_packet")

    def test_the_notice_never_echoes_the_rejected_request(self):
        marker = "grade-8-pilot-unpublished"
        g = Graph(config={"llm": _FakeLLM()})
        result = g.invoke(json.dumps({"activity_record": {"note": marker}}), ctx=_ctx())
        assert result.get("status") != "error"
        assert marker not in json.dumps(result, ensure_ascii=False, default=str)

    def test_learner_data_in_the_record_is_still_an_error_not_a_notice(self):
        # The S-2 fail-closed is a refusal this agent makes, so it must not be reported as a
        # completed request carrying a notice.
        record = {"learning_objective": "[MASKED] wrote this"}
        g = Graph(config={"llm": _FakeLLM()})
        result = g.invoke(_req(activity_record=record), ctx=_ctx())
        assert result.get("status") == "error"
        assert not result.get("output") and not result.get("diagnostic_code")
