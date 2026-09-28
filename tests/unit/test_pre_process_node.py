# EDU-C2-095 — Unit Tests: PreProcessNode (input normalization + S-2 de-identification)

import json

import pytest
from framework.schemas.agent_status import AgentStatus

from src.nodes.pre_process_node import PreProcessNode
from src.services import degradation
from src.services.degradation import (
    ACTIVITY_RECORD_SHAPE,
    DEGRADATION_CODES,
    EMPTY_OR_MISSING,
    MALFORMED_JSON,
    NOT_OBJECT,
    POLICY_EXTRACTS_INVALID,
    SCHEMA_CITATIONS_INVALID,
    SCHEMA_ITEMS_INVALID,
    SCHEMA_MISSING,
    TOO_DEEP,
    explain_degradation,
)

_SCHEMA = {
    "required_items": ["learning_objective", "parent_guardian_notification"],
    "citations": {"learning_objective": "schema-s1"},
}


def _payload(**over):
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


class TestPreProcessNode:
    """Input parse, caller-supplied-schema resolution, and fail-closed behaviour."""

    def setup_method(self):
        self.node = PreProcessNode()

    @staticmethod
    def _assert_notice(out, code):
        """A request the caller can correct completes with the code and nothing else — no
        checklist is produced and no error is recorded, because nothing failed."""
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["diagnostic_code"] == code
        assert "parsed_record" not in out and "error_log" not in out

    def test_valid_payload_resolves_schema_and_citations(self):
        out = self.node.execute({"user_input": _payload()})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["parsed_record"] == {
            "learning_objective": "use GenAI for drafting",
            "use_context": "grade 8 English",
        }
        assert out["schema_required_items"] == [
            "learning_objective",
            "parent_guardian_notification",
        ]
        # citations merge the schema's own map and the supplied policy_extracts.
        assert out["policy_citations"] == {
            "learning_objective": "schema-s1",
            "parent_guardian_notification": "policy-s3",
        }

    def test_baseline_masked_pii_fails_closed(self):
        # The baseline S-2 scan masks PII to [MASKED] before execute(); its presence
        # in the untrusted record means learner-identifying data was supplied.
        out = self.node.execute({"user_input": _payload(activity_record={"learning_objective": "[MASKED] wrote this"})})
        assert out["status"] == AgentStatus.ERROR.value
        assert "parsed_record" not in out

    def test_caller_supplied_mask_marker_is_rejected(self):
        out = self.node.execute({"user_input": _payload(activity_record={"note": "the token [MASKED] appears"})})
        assert out["status"] == AgentStatus.ERROR.value
        assert "parsed_record" not in out

    def test_missing_schema_degrades(self):
        out = self.node.execute({"user_input": json.dumps({"activity_record": {"learning_objective": "x"}})})
        self._assert_notice(out, SCHEMA_MISSING)

    def test_non_dict_payload_degrades(self):
        self._assert_notice(self.node.execute({"user_input": "[1, 2, 3]"}), NOT_OBJECT)

    def test_invalid_json_degrades(self):
        self._assert_notice(self.node.execute({"user_input": "{not json"}), MALFORMED_JSON)

    def test_deeply_nested_json_degrades_without_raising(self):
        # Well under 64 KiB but nested past CPython's decoder recursion limit:
        # json.loads raises RecursionError, which must be converted to a structural
        # outcome rather than escaping as an unhandled exception.
        deep = "[" * 12000 + "]" * 12000
        assert len(deep) < 65536
        self._assert_notice(self.node.execute({"user_input": deep}), TOO_DEEP)  # must not raise

    def test_activity_record_not_object_degrades(self):
        self._assert_notice(self.node.execute({"user_input": _payload(activity_record="a string")}), ACTIVITY_RECORD_SHAPE)

    def test_empty_required_items_degrades(self):
        out = self.node.execute(
            {"user_input": json.dumps({"activity_record": {"a": "x"}, "evidence_schema": {"required_items": []}})}
        )
        self._assert_notice(out, SCHEMA_ITEMS_INVALID)

    def test_empty_citation_value_degrades(self):
        # A whitespace-only citation is a malformed schema entry — it must stop the run in
        # resolve_evidence_schema, never reach output as a citation-less gap.
        schema = {"required_items": ["learning_objective"], "citations": {"learning_objective": "   "}}
        out = self.node.execute(
            {"user_input": json.dumps({"activity_record": {"learning_objective": "x"}, "evidence_schema": schema})}
        )
        self._assert_notice(out, SCHEMA_CITATIONS_INVALID)

    def test_empty_policy_extract_value_degrades(self):
        schema = {"required_items": ["learning_objective"]}
        out = self.node.execute(
            {
                "user_input": json.dumps(
                    {
                        "activity_record": {"learning_objective": "x"},
                        "evidence_schema": schema,
                        "policy_extracts": {"learning_objective": ""},
                    }
                )
            }
        )
        self._assert_notice(out, POLICY_EXTRACTS_INVALID)

    def test_a_notice_carries_only_the_code(self):
        # No payload content and no error_log: nothing failed, and nothing the caller sent
        # is repeated back to them.
        marker = "grade-8-pilot-unpublished"
        out = self.node.execute({"user_input": json.dumps({"activity_record": {"a": marker}})})
        assert set(out.keys()) == {"diagnostic_code", "status"}
        assert marker not in json.dumps(out)

    def test_input_guard_truncates_and_strips_control_chars_without_raising(self):
        raw = _payload() + "\x00\x01" + ("P" * 70000)
        st = self.node._extra_security_gate_input({"user_input": raw})
        cleaned = st["user_input"]
        assert len(cleaned) <= 65536
        assert "\x00" not in cleaned


class TestTheNoticeTellsTheCallerWhatToChange:
    """A request the caller can correct is answered, not failed — and the answer is the notice
    itself, released as the output by post_process. So the sentence, not a progress line, is what
    reaches the caller: each code must have one of its own, and it must name what to change.

    A refusal this agent makes (learner data in the record) is a different outcome and keeps the
    fail-closed path, reason line included — `TestPreProcessNode` covers that side.
    """

    @staticmethod
    def _notice(user_input):
        out = PreProcessNode().execute({"user_input": user_input, "correlation_id": "c"})
        assert out["status"] == AgentStatus.SUCCESS.value
        code = out["diagnostic_code"]
        return code, explain_degradation(code)

    def test_empty_input_states_the_reason(self):
        code, notice = self._notice("   ")
        assert code == EMPTY_OR_MISSING and "No request content" in notice

    def test_non_json_input_states_the_reason(self):
        code, notice = self._notice("test")
        assert code == MALFORMED_JSON and "not valid JSON" in notice

    def test_json_that_is_not_an_object_states_the_reason(self):
        code, notice = self._notice('["a"]')
        assert code == NOT_OBJECT and "must be a JSON object" in notice

    def test_excessive_nesting_states_its_own_reason(self):
        code, notice = self._notice("[" * 5000 + "]" * 5000)
        assert code == TOO_DEEP and "nested too deeply" in notice

    def test_a_missing_schema_says_this_agent_never_supplies_one(self):
        # The school's own approved schema is the checklist; substituting a default would be a
        # governance decision this agent does not make, so the notice has to say so.
        code, notice = self._notice(json.dumps({"activity_record": {"a": "x"}}))
        assert code == SCHEMA_MISSING and "never substitutes" in notice

    @pytest.mark.parametrize("code", sorted(DEGRADATION_CODES))
    def test_every_code_has_a_sentence_of_its_own(self, code):
        notice = explain_degradation(code)
        assert notice and not notice.startswith(degradation._FALLBACK)
        assert code not in notice  # the internal handle is not shown when a real sentence exists

    @pytest.mark.parametrize("code", sorted(DEGRADATION_CODES))
    def test_no_notice_can_interpolate_anything(self, code):
        # A constant sentence is what makes "no request value is ever echoed" structural.
        notice = explain_degradation(code)
        assert "{" not in notice and "}" not in notice and "%s" not in notice

    def test_an_unknown_code_falls_back_with_its_handle(self):
        assert explain_degradation("no_such_code") == f"{degradation._FALLBACK} [no_such_code]"
        assert explain_degradation("") == degradation._FALLBACK

    def test_an_unclassified_code_still_fails_closed(self):
        # _degrade is the only way into the notice path; a code with no sentence is a defect in
        # this agent, not a caller correction, so it must not be answered as a completed request.
        out = PreProcessNode()._degrade("a_code_nobody_registered", {"correlation_id": "c"})
        assert out["status"] == AgentStatus.ERROR.value
        assert "diagnostic_code" not in out
