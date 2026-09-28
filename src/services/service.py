"""AgentCore Platform v1.0"""

# Service layer: deterministic, domain-specific validation logic.
# Must NOT contain framework imports, routing, or credentials — nodes call this.
# No external I/O — a pure, local evaluation of the caller-supplied payload.

from __future__ import annotations

import json
import re
from typing import Any

from src.services.degradation import (
    POLICY_EXTRACTS_INVALID,
    SCHEMA_CITATIONS_INVALID,
    SCHEMA_ITEMS_INVALID,
    SCHEMA_MISSING,
    SCHEMA_NOT_OBJECT,
)

# The baseline FunctionNode S-2 PII scan masks detected PII to this marker BEFORE
# execute() runs (the current wheel replaces the identifier in place and exposes no
# state finding). PreProcessNode fails closed when this marker appears in the
# untrusted, parsed activity record — the only signal that survives the baseline.
# A caller that supplies the literal marker itself is rejected the same way (safe
# side): V1 does not accept a record already carrying the marker until the framework
# exposes a structured PII finding. See docs/02_design.md S-2.
_PII_MASK_MARKER = "[MASKED]"

# Depth cap for the recursive marker scan — a structure deeper than this is treated
# as a hit (fail closed, safe side). In practice the parse below already rejects
# over-deep input; this is defense in depth against unbounded recursion.
_MAX_SCAN_DEPTH = 100


def parse_activity_payload(raw_input: str) -> dict[str, Any]:
    """Parse and shallow-validate the caller-supplied payload.

    Expects a JSON object carrying ``activity_record``, ``evidence_schema`` and
    (optional) ``policy_extracts``. Raises ValueError on malformed JSON, deeply
    nested JSON, or a non-object top level — PreProcessNode turns this into a
    structural AgentStatus.ERROR (fail closed, no partial output).
    """
    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        raise ValueError(f"activity payload is not valid JSON: {exc}") from exc
    except RecursionError:
        # Deeply nested JSON (within the size limit) overflows the decoder's
        # recursion. Convert to a bounded structural error — never leak the
        # exception body — so the node fails closed like any malformed payload.
        raise ValueError("activity payload nesting exceeds the allowed depth") from None
    if not isinstance(payload, dict):
        raise ValueError("activity payload must be a JSON object")
    return payload


def contains_mask_marker(value: Any, _depth: int = 0) -> bool:
    """True if the baseline PII mask marker appears anywhere in ``value``.

    Recurses dict/list/tuple and checks every string leaf (and dict keys). Used to
    fail closed when learner-identifying data was masked by the baseline S-2 scan
    (or a caller supplied the literal marker). Never inspects a raw identifier — the
    baseline has already replaced it with the marker before execute() runs. A
    structure deeper than ``_MAX_SCAN_DEPTH`` is treated as a hit (fail closed).
    """
    if _depth > _MAX_SCAN_DEPTH:
        return True
    if isinstance(value, str):
        return _PII_MASK_MARKER in value
    if isinstance(value, dict):
        return any(contains_mask_marker(k, _depth + 1) or contains_mask_marker(v, _depth + 1) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_mask_marker(v, _depth + 1) for v in value)
    return False


def resolve_evidence_schema(evidence_schema: Any, policy_extracts: Any) -> tuple[list[str], dict[str, str], str | None]:
    """Structurally validate the caller (school/board)-supplied evidence schema and
    resolve the effective required-evidence-item list and the citable policy sections.

    Returns ``(schema_required_items, policy_citations, code)``. ``code`` is one of the
    allow-listed degradation codes when the schema/policy is missing or not usable as a
    checklist, and None on success. It is a code rather than a sentence so that the reason a
    caller reads is decided in one place (``src/services/degradation.py``) and can carry no
    schema key or policy reference. The schema is TRUSTED (supplied by the VERIFIED_EXTERNAL
    school governance caller) but must be well-formed — a hostile non-dict value becomes a
    bounded structural code, never a silent coercion. The required items come only from the
    schema, never from the untrusted record.
    """
    if evidence_schema is None:
        return [], {}, SCHEMA_MISSING
    if not isinstance(evidence_schema, dict):
        return [], {}, SCHEMA_NOT_OBJECT

    raw_items = evidence_schema.get("required_items")
    if not isinstance(raw_items, list) or not raw_items:
        return [], {}, SCHEMA_ITEMS_INVALID
    required_items: list[str] = []
    for item in raw_items:
        if not isinstance(item, str) or not item.strip():
            return [], {}, SCHEMA_ITEMS_INVALID
        required_items.append(item)

    # policy_citations: item -> policy/schema section reference, validated as a
    # str->str map. Sourced from the schema's own ``citations`` and/or the supplied
    # ``policy_extracts`` (both caller-supplied and trusted, still validated).
    citations: dict[str, str] = {}
    raw_citations = evidence_schema.get("citations", {})
    if raw_citations and not isinstance(raw_citations, dict):
        return [], {}, SCHEMA_CITATIONS_INVALID
    for item, ref in (raw_citations or {}).items():
        # A citation must be a non-empty/non-whitespace reference — an empty string
        # is not a valid schema/policy section, so it fails closed as a structural
        # error rather than reaching missing_evidence as a citation-less gap.
        if not isinstance(item, str) or not isinstance(ref, str) or not item.strip() or not ref.strip():
            return [], {}, SCHEMA_CITATIONS_INVALID
        citations[item] = ref
    if policy_extracts is not None:
        if not isinstance(policy_extracts, dict):
            return [], {}, POLICY_EXTRACTS_INVALID
        for item, ref in policy_extracts.items():
            if not isinstance(item, str) or not isinstance(ref, str) or not item.strip() or not ref.strip():
                return [], {}, POLICY_EXTRACTS_INVALID
            citations.setdefault(item, ref)

    return required_items, citations, None


# Citation used when the caller supplied no specific policy/schema section for a
# required item — the requirement itself still traces to the schema.
_SCHEMA_REQUIREMENT_REF = "evidence_schema.required_items"


def _is_empty(value: Any) -> bool:
    """True for a value that carries no evidence: None, a blank/whitespace string,
    or an empty list/dict."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return False


def check_item_presence(record: dict[str, Any], required_items: list[str]) -> list[dict[str, Any]]:
    """Deterministic per-item presence check of the record against the schema's
    required items. Returns ``[{item, present, issue}]`` — ``issue`` is ``"missing"``
    (key absent) or ``"empty"`` (present but no evidence), else ``None``.
    Presence only — no judgement of educational quality or approval."""
    findings: list[dict[str, Any]] = []
    for item in required_items:
        if item not in record:
            findings.append({"item": item, "present": False, "issue": "missing"})
        elif _is_empty(record.get(item)):
            findings.append({"item": item, "present": False, "issue": "empty"})
        else:
            findings.append({"item": item, "present": True, "issue": None})
    return findings


def check_internal_consistency(record: dict[str, Any], required_items: list[str]) -> list[dict[str, Any]]:
    """Deterministic internal-consistency check across the record's declared items.
    V1 scope: each present required item must carry a textual (str) value — a
    present-but-non-textual value is a structural inconsistency. Returns
    ``[{item, consistent, issue}]`` for present items only. No semantic/quality
    judgement (that stays out of scope)."""
    findings: list[dict[str, Any]] = []
    for item in required_items:
        if item in record and not _is_empty(record.get(item)):
            if isinstance(record.get(item), str):
                findings.append({"item": item, "consistent": True, "issue": None})
            else:
                findings.append({"item": item, "consistent": False, "issue": "non-textual value"})
    return findings


def assemble_missing_evidence(
    item_findings: list[dict[str, Any]],
    consistency_findings: list[dict[str, Any]],
    citations: dict[str, str],
) -> list[dict[str, Any]]:
    """Assemble the cited missing-evidence gaps from the deterministic findings.
    Each gap is ``{item, reason, citation}`` where ``citation`` is the supplied
    schema/policy section for the item (or a reference to the schema requirement
    when the caller supplied none). Absent items and inconsistent present items
    both produce a gap."""
    gaps: list[dict[str, Any]] = []
    for f in item_findings:
        if not f["present"]:
            gaps.append(
                {
                    "item": f["item"],
                    "reason": f["issue"] or "missing",
                    # `or` (not a default arg) so an empty citation also falls back —
                    # defense in depth; resolve_evidence_schema already rejects empties.
                    "citation": citations.get(f["item"]) or _SCHEMA_REQUIREMENT_REF,
                }
            )
    for c in consistency_findings:
        if not c.get("consistent", True):
            gaps.append(
                {
                    "item": c["item"],
                    "reason": c["issue"] or "inconsistent",
                    "citation": citations.get(c["item"]) or _SCHEMA_REQUIREMENT_REF,
                }
            )
    return gaps


# --- post_process: deterministic packet + optional non-authoritative reviewer summary ---

_REVIEWER_SUMMARY_MAX_CHARS = 4000

# Phrases that would make the non-authoritative summary assert an approval/rejection, an
# educational-quality judgement, a learner decision, or a contact action — all out of scope.
# A summary containing any of these is dropped (degrade), never returned.
_BANNED_SUMMARY_PHRASES = (
    "approve",
    "approved",
    "reject",
    "rejected",
    "deny",
    "denied",
    "permit",
    "prohibit",
    "not approved",
    "authorize",
    "grade",
    "graded",
    "score",
    "excellent",
    "poor quality",
    "high quality",
    "low quality",
    "well designed",
    "well-designed",
    "passes",
    "fails the",
    "recommend approval",
    "contact the student",
    "email the guardian",
    "notify the parent",
)

# A citation-like token in the free-text summary (section markers / schema-policy refs). Every such
# token must be one of the citations backing a determined finding, else the summary is dropped.
_CITATION_TOKEN = re.compile(r"§\s*[\w.\-]+|\b(?:schema|policy)[-_][\w.\-]+", re.IGNORECASE)

_SUMMARY_CONTROL_ORDINALS = frozenset({c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)} | {0x7F})

# Bounded structured-identifier detector for the LLM-generated reviewer summary. This is defense in
# depth that COMPLEMENTS — does not re-implement — the baseline S-2 substrate: the framework S-2 gate
# de-identifies INPUT fields before execute(), but the summary is produced INSIDE execute() and is not
# covered by any input gate. It flags the structured learner-PII classes that can surface in free text:
# email, phone number, and long numeric identifiers (student ID / My Number). Free-form learner NAMES
# are mitigated structurally rather than by regex — the raw activity record is never sent to the LLM
# (only de-identified findings + citations are), and an input record carrying baseline-masked PII is
# already rejected upstream by PreProcessNode. Credential leakage is handled separately in the node via
# the shared credential-detector substrate.
# Bounds are RFC-derived (local-part 64, label 63, at most 4 labels) and format-derived
# (identifier / phone digit runs), so every quantifier below has a finite upper bound: the
# summary these run over is attacker-influenced LLM output, where an unbounded quantifier
# degrades to quadratic backtracking on a long non-matching string. The label group is
# repeated rather than folded into one dot-bearing character class, so no two adjacent
# elements can match the same character.
_SUMMARY_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,3}\.[A-Za-z]{2,24}"
)
_SUMMARY_LONG_DIGITS_RE = re.compile(r"\d{7,64}")
_SUMMARY_PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d\-.\s()]{7,32}\d(?!\d)")


def summary_contains_pii(text: str) -> bool:
    """True if the summary carries structured learner-PII (email / phone / long numeric ID). Kept
    conservative — a hit drops the optional summary (degrade to success), so a rare false positive is
    safe, while citation refs (`policy-s3`, `§4.2`) never trip it (too few digits)."""
    if _SUMMARY_EMAIL_RE.search(text):
        return True
    if _SUMMARY_LONG_DIGITS_RE.search(text):  # 7+ consecutive digits: student ID / My Number
        return True
    # Separator-formatted phone numbers (e.g. 090-1234-5678): count as a hit only when the digit
    # count is phone-plausible, so short dotted citation refs are not mistaken for a number.
    for m in _SUMMARY_PHONE_RE.finditer(text):
        if sum(ch.isdigit() for ch in m.group()) >= 9:
            return True
    return False


def assemble_completeness_packet(missing_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the deterministic, evidence-cited completeness packet — LLM-free. This packet and its
    ``status`` are the authoritative output; the optional reviewer summary is added by the node."""
    return {
        "status": "correction_required" if missing_evidence else "evidence_complete",
        "missing_evidence": missing_evidence,
        "review_required": True,
        "reviewer_summary_available": False,
    }


def build_summary_messages(completeness_packet: dict[str, Any]) -> list[dict[str, str]]:
    """Build the system/user messages for the non-authoritative reviewer summary. Only the
    already-determined, de-identified findings are sent — never the raw activity record."""
    system = (
        "You write a short, plain-language summary of a completeness check for a human reviewer. "
        "You do NOT approve or reject the activity, do NOT judge educational quality, do NOT grade "
        "or decide anything about learners, and do NOT contact anyone. Reference only the citations "
        "provided; invent nothing. State that teacher and school-leader review remains required."
    )
    user = (
        "Completeness result: "
        + str(completeness_packet.get("status"))
        + ". Missing or insufficient evidence items with their cited sections: "
        + json.dumps(completeness_packet.get("missing_evidence", []), ensure_ascii=True)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def validate_summary(text: Any, allowed_citations: set[str]) -> str | None:
    """Return the safe summary text, or ``None`` if it is empty, malformed, or unsafe. Called INSIDE
    ``execute()`` before the summary enters the return dict so an unsafe summary is dropped (degrade
    to success) rather than raised. Rejects: non-str / empty / over-length, control characters, a
    banned approval/decision/quality phrase, a structured learner-PII identifier, or a citation token
    not backing a determined finding. Credential leakage is caught in the node (shared substrate)."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > _REVIEWER_SUMMARY_MAX_CHARS:
        return None
    if any(ord(ch) in _SUMMARY_CONTROL_ORDINALS for ch in stripped):
        return None
    if summary_contains_pii(stripped):
        return None
    lowered = stripped.lower()
    if any(phrase in lowered for phrase in _BANNED_SUMMARY_PHRASES):
        return None
    for token in _CITATION_TOKEN.findall(stripped):
        if token not in allowed_citations:
            return None
    return stripped
