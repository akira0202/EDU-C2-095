"""AgentCore Platform v1.0 — caller-facing wording for runs that end without a checklist.

Each outcome here is reported by completing the run with an allow-listed ``diagnostic_code`` and a
fixed sentence derived from that code alone. No request value, record field, schema key or policy
reference is ever interpolated, so a notice can never echo caller content back. On such a run
``status=SUCCESS`` means the agent finished handling the request safely; it does not mean a
completeness checklist was produced.

Every code here names something the caller can correct: the request is not the documented
envelope, or the school's evidence schema it carried is not usable as a checklist. Each sentence
says what to send instead. No checklist is assembled and no reviewer summary is written.

Not reported here, and ending as ``status=ERROR`` instead: learner-identifying data in the
activity record (the S-2 fail-closed — a record that reached this agent carrying learner data is
a governance failure, not a shape to fix), and a completeness packet that dropped the findings its
own verdict claims (the terminal S-3 backstop). Those are refusals this agent makes, and neither
is ever represented as a successful completion.

The reviewer summary is not in this module's scope either: it is optional and non-authoritative,
so an unavailable or unsafe summary already degrades inside ``post_process`` to the deterministic
checklist with ``reviewer_summary_available: false`` — the checklist is still produced.
"""

from __future__ import annotations

# The outer request envelope.
EMPTY_OR_MISSING = "empty_or_missing"
MALFORMED_JSON = "malformed_json"
TOO_DEEP = "too_deep"
NOT_OBJECT = "not_object"
ACTIVITY_RECORD_SHAPE = "activity_record_shape"

# The school-supplied evidence schema and policy extracts carried inside it.
SCHEMA_MISSING = "schema_missing"
SCHEMA_NOT_OBJECT = "schema_not_object"
SCHEMA_ITEMS_INVALID = "schema_items_invalid"
SCHEMA_CITATIONS_INVALID = "schema_citations_invalid"
POLICY_EXTRACTS_INVALID = "policy_extracts_invalid"

_ENVELOPE = 'Send a JSON object holding "activity_record" and "evidence_schema"; "policy_extracts" is ' "optional."

_MESSAGES: dict[str, str] = {
    EMPTY_OR_MISSING: f"No request content was received. {_ENVELOPE}",
    MALFORMED_JSON: ("The request is not valid JSON. Check for a missing quote, comma, or brace, then send it again."),
    TOO_DEEP: "The request is nested too deeply. Flatten the structure and send it again.",
    NOT_OBJECT: f"The request must be a JSON object, not a list or a plain value. {_ENVELOPE}",
    ACTIVITY_RECORD_SHAPE: (
        'The request carries no "activity_record" object. Send the proposed learning activity as a '
        "JSON object under that key."
    ),
    SCHEMA_MISSING: (
        'The request carries no "evidence_schema". Send your school\'s approved schema — this agent '
        "checks against the schema you supply and never substitutes one of its own."
    ),
    SCHEMA_NOT_OBJECT: 'The "evidence_schema" must be a JSON object.',
    SCHEMA_ITEMS_INVALID: (
        'The "evidence_schema" needs "required_items": a non-empty list of evidence-item names, each '
        "a non-empty string."
    ),
    SCHEMA_CITATIONS_INVALID: (
        'The "evidence_schema" has a "citations" entry that is not usable. It must be an object '
        "mapping an evidence-item name to a non-empty policy or schema section reference."
    ),
    POLICY_EXTRACTS_INVALID: (
        'The "policy_extracts" are not usable. Send an object mapping an evidence-item name to a '
        "non-empty policy section reference, or omit the field."
    ),
}

# Every code this module answers. A node degrades only with one of these; anything else is a defect.
DEGRADATION_CODES = frozenset(_MESSAGES)

_FALLBACK = "The request could not be completed and no completeness checklist was produced."


def explain_degradation(code: str) -> str:
    """Return one caller-facing sentence for a degradation code."""
    return _MESSAGES.get(code, f"{_FALLBACK} [{code}]" if code else _FALLBACK)
