# EDU-C2-095 — Unit Tests: MainNode (deterministic completeness + consistency)

from framework.schemas.agent_status import AgentStatus

from src.nodes.main_node import MainNode

_REQUIRED = ["learning_objective", "teacher_oversight", "parent_guardian_notification"]
_CITATIONS = {
    "learning_objective": "schema-s1",
    "teacher_oversight": "policy-s2",
    "parent_guardian_notification": "policy-s3",
}


def _state(record, status=None):
    st = {
        "parsed_record": record,
        "schema_required_items": _REQUIRED,
        "policy_citations": _CITATIONS,
    }
    if status is not None:
        st["status"] = status
    return st


class TestMainNode:
    """Deterministic per-item presence + internal-consistency + cited missing-evidence."""

    def setup_method(self):
        self.node = MainNode()

    def test_all_items_present_no_gaps(self):
        out = self.node.execute(
            _state(
                {
                    "learning_objective": "draft essays with GenAI",
                    "teacher_oversight": "teacher reviews every output",
                    "parent_guardian_notification": "letter sent 2026-04",
                }
            )
        )
        assert out["status"] == AgentStatus.SUCCESS.value
        assert all(f["present"] for f in out["item_findings"])
        assert out["missing_evidence"] == []

    def test_missing_item_produces_cited_gap(self):
        out = self.node.execute(
            _state(
                {
                    "learning_objective": "x",
                    "teacher_oversight": "y",
                    # parent_guardian_notification missing
                }
            )
        )
        assert out["status"] == AgentStatus.SUCCESS.value
        gaps = {g["item"]: g for g in out["missing_evidence"]}
        assert "parent_guardian_notification" in gaps
        assert gaps["parent_guardian_notification"]["reason"] == "missing"
        assert gaps["parent_guardian_notification"]["citation"] == "policy-s3"

    def test_empty_item_is_a_gap(self):
        out = self.node.execute(
            _state({"learning_objective": "   ", "teacher_oversight": "y", "parent_guardian_notification": "z"})
        )
        lo = next(f for f in out["item_findings"] if f["item"] == "learning_objective")
        assert lo["present"] is False and lo["issue"] == "empty"
        assert any(g["item"] == "learning_objective" for g in out["missing_evidence"])

    def test_non_textual_value_is_inconsistent(self):
        out = self.node.execute(
            _state({"learning_objective": 42, "teacher_oversight": "y", "parent_guardian_notification": "z"})
        )
        lo = next(c for c in out["consistency_findings"] if c["item"] == "learning_objective")
        assert lo["consistent"] is False
        assert any(g["item"] == "learning_objective" for g in out["missing_evidence"])

    def test_gap_without_citation_falls_back_to_schema_reference(self):
        state = _state({"teacher_oversight": "y", "parent_guardian_notification": "z"})
        state["policy_citations"] = {}  # no citations supplied
        out = self.node.execute(state)
        gap = next(g for g in out["missing_evidence"] if g["item"] == "learning_objective")
        assert gap["citation"] == "evidence_schema.required_items"

    def test_short_circuits_on_upstream_error(self):
        # pre_process failed closed — main must no-op with an empty delta.
        out = self.node.execute(_state({"anything": "x"}, status=AgentStatus.ERROR.value))
        assert out == {}

    def test_no_decision_or_quality_fields_emitted(self):
        out = self.node.execute(
            _state({"learning_objective": "x", "teacher_oversight": "y", "parent_guardian_notification": "z"})
        )
        # deterministic completeness only — never an approval/rejection or score.
        for key in ("approved", "rejected", "decision", "quality_score", "grade"):
            assert key not in out

    def test_execute_method_signature(self):
        """Node contract: a node must implement execute(state), not _invoke_impl."""
        import inspect

        assert hasattr(MainNode, "execute"), "MainNode must implement execute()"
        params = list(inspect.signature(MainNode.execute).parameters.keys())
        assert len(params) >= 2 and params[1] == "state"
        assert "_invoke_impl" not in MainNode.__dict__
