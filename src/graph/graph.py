"""AgentCore Platform v1.0"""

# EDU-C2-095 — School GenAI Learning-Activity Evidence Completeness Validator
#
# Cat 2 template. Standalone composition (3 FunctionNodes in the fixed backbone) —
# no GraphNode / inner subgraph. See docs/02_design.md "Why no GraphNode / inner subgraph".
#
#   START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                              ↓ (retry, max 3)
#                                            pre_process

from typing import Any

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.main_node import MainNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State
from src.services.llm import ReviewerSummaryLLM


class SchoolGenAILearningActivityEvidenceCompletenessValidatorGraph(AgentBaseGraph):
    """Validates a teacher-authored proposed GenAI learning-activity record against a
    caller-supplied school-approved versioned evidence schema + policy extracts, and returns a
    cited missing-evidence checklist for human review. It does not judge educational quality,
    approve/reject the activity, process learner data, grade learners, or contact anyone —
    teacher and school-leader review remains mandatory."""

    @property
    def name(self) -> str:
        return "SchoolGenAILearningActivityEvidenceCompletenessValidator"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = MainNode()
        # DI seam: a caller-supplied `llm` (tests inject a fake) wins; otherwise the default
        # keyless ReviewerSummaryLLM, which resolves AZURE_OPENAI_* per-request at call time. The
        # reviewer summary is best-effort — an unavailable/failing/unsafe adapter degrades to the
        # deterministic completeness packet (reviewer_summary_available: false), never an error.
        llm = self.config.get("llm")
        if llm is None:
            llm = ReviewerSummaryLLM()
        self._nodes["post_process"] = PostProcessNode(llm=llm)

    def _extra_initial_state(self) -> dict[str, Any]:
        # Seed the NotRequired domain fields so downstream reads and get_output have stable defaults.
        return {
            "parsed_record": {},
            "schema_required_items": [],
            "policy_citations": {},
            "item_findings": [],
            "consistency_findings": [],
            "missing_evidence": [],
            "completeness_packet": {},
            "reviewer_summary": "",
        }

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        # AgentBaseGraph.get_output surfaces only `output` (= formatted_output or result = the
        # completeness packet). The optional reviewer summary is a separate State field, so without
        # this override it would never reach the caller — the human reviewer it is written for.
        out: dict[str, Any] = super().get_output(state)
        out.update({"reviewer_summary": state.get("reviewer_summary", "")})
        # Completed with a notice instead of a checklist: the code names which notice, so a
        # caller can branch on it rather than parse the sentence.
        code = state.get("diagnostic_code")
        if code:
            out["diagnostic_code"] = str(code)
        return out
