# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: `LearningActivityEvidenceValidatorGraph`
- **L1 Base**: `AgentBaseGraph` (fixed 5-node backbone; no `AutonomousBaseGraph` loop — this is a
  deterministic, bounded validation pass over a single record, not a self-directed agent)
- **Level 2 reference type**: `DocGenerationAgent` (closest descriptive fit — the output is a
  structured, human-reviewable completeness packet document). Documentation label only; the class
  inherits `AgentBaseGraph` **directly** and does not inherit any Level 2 wrapper class.
- **Pattern × Industry**: `Data Validation` × `EDU`.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Why no `GraphNode` / inner subgraph

The workflow (input/PII/schema validation → deterministic completeness & consistency checks →
cited missing-evidence analysis → human-review packet + constrained reviewer summary) is a strictly
linear, single-pass pipeline: no branching topology, no HITL review loop (EDU is not in the
HCR/INS/FIN/GOV regulated set), and no external service call other than a
single constrained LLM phrasing call in `post_process`. The framework's category definition confirms a Cat 2 template
"can inherit the L1 framework directly and implement its own multi-step workflow." A `GraphNode`
inner subgraph exists for cases needing an independent topology (e.g. a draft↔review loop), which
this template does not need. The three domain steps map onto the fixed `pre_process` /
`main` / `post_process` slots plus a deterministic service module (`src/services/service.py`) — see
Node Configuration.

## Architecture Overview

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | Framework defaults (schema_version, session_id, trust_level) | — | — | `InitializeNode` (default) |
| pre_process | Parse the caller payload from `user_input` (`activity_record`, `evidence_schema`, `policy_extracts`); answer a request the caller can correct (a non-`dict` payload, a missing/unusable `activity_record`, a missing or malformed evidence schema) by **completing with a `diagnostic_code`** and no partial output; **fail closed** (`status=error`, no partial output) when **learner-identifying data** is present — detected by the **baseline PII mask marker `[MASKED]`** appearing in the parsed `activity_record` (the current wheel baseline replaces detected PII with `[MASKED]` before `execute()` and exposes no state finding, so the reject keys on that marker, not on re-detecting a raw identifier; a caller-supplied `[MASKED]` is treated the same — fail-closed, safe side); resolve the effective required-evidence-item list and the citable policy sections from the caller (school/board)-supplied approved schema | `user_input` | `parsed_record`, `schema_required_items`, `policy_citations` | `PreProcessNode(FunctionNode)` |
| main | Deterministic per-item presence + internal-consistency checks of the record against `schema_required_items`; assemble cited missing-evidence findings mapping each gap to its schema/policy section. **No LLM; no quality judgment; no approve/reject** | `parsed_record`, `schema_required_items`, `policy_citations` | `item_findings`, `consistency_findings`, `missing_evidence` | `MainNode(FunctionNode)` |
| post_process | Assemble the deterministic, evidence-cited **completeness packet** (`evidence_complete` / `correction_required`, `review_required: true`) **first, LLM-free**; then produce an **optional, non-authoritative** reviewer summary by a single call-time-resolved LLM call over the already-determined findings only — on LLM unavailable/failure/unsafe output, **drop the summary and keep `status: success`** with `reviewer_summary_available: false` + allowlisted reason; S-3 gate blocks any approval/rejection, quality judgment, fabricated citation, or learner-data leakage | `item_findings`, `consistency_findings`, `missing_evidence` | `completeness_packet`, `reviewer_summary`, `result` | `PostProcessNode(FunctionNode)` |
| finalize | Framework defaults (response_metadata, total_time_ms) | — | — | `FinalizeNode` (default) |

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry, max 3)
                                          pre_process
```

`main` and `post_process` set `status=success` on a normal pass — a `correction_required` verdict
(the record is missing required evidence) is a valid, **successful** finding, not an error. `status`
becomes `error` only on a **security failure**: learner-identifying data detected in the activity
record, or the terminal S-3 backstop finding a packet that dropped its own findings. That routes
straight to `finalize`, skipping `main` and `post_process`.

A request the caller can **correct** — the envelope, the activity record, or the school's evidence
schema — is not an error: `pre_process` completes carrying a `diagnostic_code`, `main` skips the
checklist steps, and `post_process` releases the code's fixed sentence as the whole output. Both
`main` and `post_process` check the error status **and** the marker at the top of `execute()`,
rather than assuming the fixed edge alone blocks them — so neither a fail-closed input nor an
answered one ever yields a partial completeness packet.

## Outcome Classification

A run ends in one of four ways. The split is by **whose problem it is**: a request the caller can
correct is answered, a refusal this agent makes is not.

| Outcome | Condition | Result |
|---------|-----------|--------|
| **Checklist** (the normal outcome) | the request meets the input contract | `status = success`, the evidence-cited completeness packet. `correction_required` — the activity is missing required evidence — is the expected finding, not a failure |
| **Notice** (caller-correctable, no checklist) | empty / malformed / over-nested / non-object request; no `activity_record` object; an `evidence_schema` that is missing, not an object, or without a usable `required_items` / `citations`; unusable `policy_extracts` | `status = success`, `diagnostic_code` = the code, output = the fixed sentence naming what to send instead; the checklist steps are skipped and no reviewer summary is written |
| **Degraded summary** (checklist produced) | the reviewer-summary LLM is unavailable, times out, or returns content the output screen rejects | `status = success`, the full checklist with `reviewer_summary_available: false` and an allowlisted reason. The summary is optional and non-authoritative, so losing it never withholds the checklist |
| **Error** (fail-closed, no partial output) | learner-identifying data in the activity record (the baseline `[MASKED]` marker); a completeness packet that dropped the findings its own verdict claims (terminal S-3); credential-shaped content anywhere in a node's return (framework S-3 scan) | `status = error`, no output |

`status = success` with a `diagnostic_code` means the request was handled safely, **not** that a
checklist was produced. An empty checklist would read as "checked, nothing missing", which is the
opposite of what happened, so a degraded run returns the sentence alone. Every sentence is derived
from the allow-listed code alone (`src/services/degradation.py`) — no record field, schema key or
policy reference is ever interpolated.

Learner data in the record stays an **error**, not a notice, even though a caller could in principle
remove it: a record that reached this agent carrying learner data is a governance failure at the
school's own boundary, and reporting it as a completed request would understate it.

### Terminal Output (`get_output`)

The graph overrides `AgentBaseGraph.get_output()` so the caller (the human reviewer) receives both the
authoritative packet and the optional summary:

- `output` — `formatted_output or result`; `post_process` sets `result = completeness_packet`, so
  `output` is the evidence-cited completeness packet (`status`, `missing_evidence`, `review_required`,
  `reviewer_summary_available`, and `reviewer_summary_unavailable_reason` on degrade).
- `reviewer_summary` — added by the override via `state.get("reviewer_summary", "")`. The inherited
  `get_output` surfaces only `output`, so **without this override the optional summary would never
  reach the caller**. Non-authoritative, human-facing; empty string on degrade.
- `diagnostic_code` — added by the override, and present **only** on a run that completed with a
  notice instead of a checklist. `output` is then the code's fixed sentence, not a packet, so the
  code is what lets a caller branch without parsing the sentence.

Plus the framework-standard `status`, `trace_id`, `correlation_id`, `node_history`. No other State
field is surfaced — no learner-identifying data, no raw record, no prompt, no credential.

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `parsed_record` | `dict` | De-identified parsed activity record (`learning_objective`, `use_context`, `teacher_oversight`, `learner_facing_guidance`, `personal_information_handling`, `parent_guardian_notification`, `governing_policy_reference`) | No (`NotRequired`) |
| `schema_required_items` | `list[str]` | Effective required-evidence-item list, resolved from the caller (school/board)-supplied, versioned approved `evidence_schema` | No |
| `policy_citations` | `dict` | `item -> policy/schema section reference` used as the citation backing for each finding, resolved from the supplied schema/`policy_extracts` | No |
| `item_findings` | `list[dict]` | Per-item presence findings: `{item, present, issue}` | No |
| `consistency_findings` | `list[dict]` | Internal-consistency findings across the record's declared items | No |
| `missing_evidence` | `list[dict]` | Cited gaps: `{item, reason, citation}` (citation = supplied schema/policy section) | No |
| `completeness_packet` | `dict` | Final evidence-cited packet: `{status: "evidence_complete"\|"correction_required", missing_evidence, review_required: true, reviewer_summary_available: bool, reviewer_summary_unavailable_reason?: "llm_unavailable"\|"llm_timeout"\|"llm_response_rejected"}`. `review_required: true` is the always-on human-review flag; the `reviewer_summary_*` fields carry the deterministic-degrade signal; the reason is an **allowlisted code**, never a provider exception body | No |
| `reviewer_summary` | `str` | Optional, non-authoritative plain-text summary for the human reviewer (LLM-phrased from determined findings only) — **present only when the LLM is available; absent on degrade** | No |
| `diagnostic_code` | `str` | Allowlisted, non-sensitive code naming why no checklist was produced, on a run the caller can correct (`src/services/degradation.py`). Absent on a normal run and on a fail-closed one; never carries a request value, record field, schema key or policy reference | No |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- **No learner-identifying data in State** — de-identified inputs only; detection fails closed in `pre_process`
- InvocationContext resolved via `InvocationContext.from_state(state)` inside `execute` (secrets via `.secrets.require()`); never stored in State or on the node instance
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

### Trust boundary — schema provenance vs record content

Two inputs sit at **different trust levels**, and conflating them would let a caller weaken
validation:

- The **`evidence_schema` + `policy_extracts`** are the *trusted governance rule set*, supplied by
  the **school/board governance caller** (`VERIFIED_EXTERNAL`) and versioned. They define *what must
  be present* and provide the citable sections. They are validated **structurally** (well-formed
  map/list; fail closed on malformed/missing) but are trusted as authoritative because they are
  provided by the deploying school authority, not by the record author. Per-school variation is why
  the schema is caller-supplied rather than a single bundled registry.
- The **`activity_record`** is the *untrusted content under validation* — teacher-authored, possibly
  containing learner-identifying data. It is de-identification-screened (fail closed on
  learner-identifying data) and is never treated as a source of validation rules.

`main` checks the untrusted record against the trusted schema; it never reads required-item or
citation content out of the `activity_record` itself.

**MEXT scope:** the MEXT *Guidelines for the Use of Generative AI in Primary and Secondary Education
Ver. 2.0* are the research rationale for the template, **not a runtime
dependency**. The agent does not ingest or parse the MEXT PDF; citations resolve to the supplied
`evidence_schema` / `policy_extracts` sections, which the school authored from MEXT.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext — not held in state. If a client is injected via `config["llm"]` (a fake in
      tests, or a keyless/local client), `PostProcessNode` uses it **directly, with NO secret resolution**.
      Only when it must build the **default real** client does it resolve the LLM **secret** **inside
      `execute(self, state)`** via `InvocationContext.from_state(state).secrets.require("AZURE_OPENAI_API_KEY")` (the
      `execute(state)` contract passes no `ctx` argument; never construct `InvocationContext(...)` directly),
      and only when about to generate the optional summary — never stored on the node instance or in State
      (no cross-invoke leakage). So `MissingSecret` can arise only on the default-client path; a fake/keyless
      injected client never triggers secret resolution
- [x] ConnectionPolicy — governs the single LLM call's retry/timeout (`max_retry`, `timeout_s`, `budget_usd`);
      a timeout/error degrades (drop summary, `status:success`) rather than failing the run
- [x] SecurityViolationError — raised by `PostProcessNode._extra_security_gate_output()` only on a
      **terminal** S-3 violation of the whole returned packet (a determined finding silently dropped, or
      learner data / a decision / a credential reaching the packet). It is **not** used to reject an
      unsafe reviewer summary — that is detected and dropped inside `execute()` (drop + degrade to
      `status:success`), so the summary path degrades rather than erroring
- [x] S-2: `_extra_security_gate_input()` on `PreProcessNode` — a **non-raising** guard that truncates
      an oversized raw payload and normalizes control characters before `execute()` (business-rule
      guard; input hooks must not raise, per the framework contract). The baseline `FunctionNode` PII
      scan runs automatically **ahead of `execute()` and masks any detected PII in place**, so `execute()`
      never sees the raw identifier. The learner-identifying-data **fail-closed** therefore keys on a
      signal that **survives the baseline** — the presence of the baseline PII **mask marker `[MASKED]`**
      in the parsed `activity_record` (the current wheel baseline masks PII to `[MASKED]` and exposes no
      state finding) — and `PreProcessNode.execute()` returns `status=error` with **no `parsed_record` and
      no packet**. A caller-supplied `[MASKED]` in the record is rejected the same way (safe side; V1 does
      not accept a record already carrying the marker until the framework exposes a structured PII finding).
      Malformed/missing schema fails closed the same way. The fail-closed must NOT depend on `execute()`
      detecting a raw identifier the baseline has already masked
- [x] S-3: `_extra_security_gate_output()` on `PostProcessNode` — a **terminal** domain output gate over
      the whole returned packet: raises `SecurityViolationError` only if `completeness_packet` silently
      drops a finding `main` computed, or if learner data / a decision / a credential reached the packet.
      **Reviewer-summary safety is NOT enforced here** — an unsafe/malformed summary (approval/rejection,
      quality judgment, learner decision, fabricated citation, learner-data leak) is detected **inside
      `execute()` before the summary is placed in the return dict** and is **dropped** (degrade to
      `status:success` + `reviewer_summary_available:false` + reason `llm_response_rejected`), so it never
      reaches this hook. Runs after the baseline credential scan on output strings
- [x] S-4: `emit_trace_event()` — one domain event per node's `execute()`
      (`activity_record_validated` / `activity_record_rejected` in `pre_process`;
      `evidence_completeness_checked` in `main`; `completeness_packet_generated` in `post_process`)

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` / `RemoteAgentNode` → deliberate no-op (upstream or remote node's gate already applied)
> - Custom `BaseNode` subclass → must implement `_security_gate_input()` and
>   `_security_gate_output()` directly (`@abstractmethod` — omission raises `TypeError` at instantiation)

### Composition Pattern

- **Pattern**: Standalone (3 `FunctionNode`s in the fixed backbone; no `GraphNode`/`RemoteAgentNode`)
- **Composition target**: N/A — no subgraph or remote agent composed
- **Error propagation strategy**: N/A (no `GraphNode`/`RemoteAgentNode` in use)

## LLM usage (optional, non-authoritative — deterministic degrade)

The single LLM call lives only in `PostProcessNode.execute()` and phrases the **already-determined**
findings into a human-readable `reviewer_summary`. It is **optional and non-authoritative** — the
deterministic validator core never depends on it, so the template runs, tests, and STG-deploys with **no
LLM provisioning** (design-fix confirmed against the approved proposal and the development-credential provisioning policy).

- **Authoritative outputs are LLM-free.** `completeness_packet.status`, `missing_evidence`, every
  citation, and `review_required` are produced **deterministically** in `main` / `post_process`; the
  LLM never decides whether an evidence item is present, never scores the activity, and never
  approves/rejects it.
- **Only the default client resolves the secrets, and only at call time.** The three `AZURE_OPENAI_*`
  values are declared in `config/agent.yaml` `requires.secrets` — the manifest states what the deployment
  must provision; it does not make the summary a precondition of the run. An **injected `config['llm']` client
  (fake/keyless) is used directly — no secret resolved**. Only when the node must build the **default real**
  client does it resolve `InvocationContext.from_state(state).secrets.require("AZURE_OPENAI_API_KEY")` inside
  `execute(self, state)`, **only when about to generate the summary** — never `os.environ` / `.env` / CI
  variable names / provider internals, never `ctx` as an argument, never a directly-constructed
  `InvocationContext(...)`, never at import/startup, never stored on the node instance or in
  config/State/log. A raised `MissingSecret` (default-client path only) maps **only** to the allowlisted
  `llm_unavailable` degrade (distinct from provider-error/timeout → `llm_timeout`, and unsafe/malformed
  summary → `llm_response_rejected`), with no exception body emitted. Enabling the production summary uses
  only the official SecretProvider provisioning; nodes/graph/State/tests/STG success criteria are
  unchanged when it lands.
- **Deterministic degrade.** On missing secret / provider error / timeout / unsafe or malformed summary,
  the summary is **dropped** and the run returns **`status: success`** with the deterministic packet plus
  `reviewer_summary_available: false`, `review_required: true`, and an **allowlisted** reason code
  (`llm_unavailable` / `llm_timeout` / `llm_response_rejected`). The provider exception body, credentials,
  the prompt, and the activity-record content are **never** emitted to the return, trace, or `error_log`.
- **Injected for tests — three distinct paths.** A fake `BaseLLM` injected via `config["llm"]`
  (`Graph(config={"llm": fake_llm})`) is used directly and needs no secret, so tests do **not** mock the
  SecretProvider. `docs/03` and the PB suites distinguish: **(a)** fake client → normal summary attached;
  **(b)** fake client returns an unsafe/malformed summary → dropped in `execute()` → `status:success` +
  `reviewer_summary_available:false` + `llm_response_rejected`; **(c)** **no** injected client + no
  `AZURE_OPENAI_*` → the default-client build raises `MissingSecret` → `status:success` + `llm_unavailable`.
  The secret value and any exception body stay out of State/log/output on every path. Provisional STG runs
  the LLM-free validator core to `status: success` with the summary absent.
- **Summary safety is checked inside `PostProcessNode.execute()`, before the summary is placed in the
  return dict.** If the summary asserts an approval/rejection, an educational-quality judgment, a learner
  decision, a fabricated citation (one not backing a determined finding), or leaks learner data, the
  summary is **dropped** and the run returns the deterministic packet with `status: success`,
  `reviewer_summary_available: false`, and reason `llm_response_rejected`. The `_extra_security_gate_output()`
  hook is a terminal, packet-level backstop only — it never rejects the summary (raising there would error
  the run instead of degrading). `budget_usd` caps the call cost.

## EU AI Act Art.13 Design-Time Evidence (Advisory until 2026-09-01; required from 2026-09-01 when the Annex III determination is `In scope`)

> This template is declared **Not in scope** (EDU is not in the required-industry
> set; the agent produces a read-only, human-reviewed evidence-completeness checklist and does not
> evaluate learners or determine access to education/training). The table below is recorded for
> advisory completeness.

| Evidence item | Design reference / description |
|---------------|--------------------------------|
| Intended purpose and operating context | Pre-use completeness check of a teacher-authored proposed GenAI learning-activity record against a school-approved evidence schema; output feeds a human reviewer's decision, which the agent does not make |
| System capabilities and limitations | Deterministic presence/consistency validation + cited missing-evidence checklist + non-authoritative LLM-phrased summary. It does not judge educational quality, approve/reject, grade, process learner data, or contact students/guardians |
| User-facing transparency information | `completeness_packet` carries `review_required: true`; `reviewer_summary` states it is not an approval and that teacher/school-leader review remains mandatory |
| Human oversight mechanism | Output is a pre-decision evidence packet only; the school-leadership/board reviewer makes the approval decision (out of the agent's scope) |

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/` required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Bounded, deterministic single-pass validation of one record — no self-directed think/act loop or iteration budget needed |
| Composition pattern | GraphNode (inner subgraph) | Standalone (3 FunctionNodes) | **Standalone** | Linear pipeline, no branching topology, no HITL loop, no external service beyond one LLM phrasing call — a subgraph adds complexity with no need for this linear validation pass |
| Completeness decision | LLM-assisted | Deterministic, rule-based | **Deterministic** | Compliance-grade completeness must be reproducible, auditable, and citeable; delegating present/absent to an LLM risks hallucination; the LLM is structurally barred from deciding completeness |
| Reviewer summary | Constrained LLM phrasing | Fully LLM-free (template-only) | **Constrained LLM phrasing (optional)** | The approved scope specifies a human-readable reviewer summary phrased by a non-authoritative LLM over the determined findings. Kept **optional**: on LLM unavailable/failure it **degrades to no summary** (`reviewer_summary_available:false`, `review_required:true`, allowlisted reason) with `status:success` — the LLM is never a hard dependency (per the approved proposal and the credential-provisioning policy) — and is S-3-gated |
| Evidence schema source | Bundled/hardcoded registry | Caller (school)-supplied, versioned | **Caller-supplied, versioned** | The approved evidence schema differs per school/board; it is trusted because supplied by the `VERIFIED_EXTERNAL` school governance caller, validated structurally, and kept separate from the untrusted `activity_record` |
| Reviewer-summary safety enforcement | `_extra_security_gate_output()` hook | Inside `execute()` before return | **Inside `execute()`** | The `@final` S-3 hook runs after `execute()` and raising there marks the node `error` — incompatible with the drop-to-`success` degrade. Checking summary safety in `execute()` lets an unsafe summary be **dropped** while the deterministic packet still returns `success`; the hook stays a terminal whole-packet backstop |
| Learner-PII fail-closed signal | Re-detect the raw identifier in `execute()` | Baseline `[MASKED]` marker in the record | **Baseline `[MASKED]` marker** | The current wheel baseline `FunctionNode` PII scan **masks** PII to `[MASKED]` before `execute()` and exposes no state finding, so the raw identifier is gone. V1 fails closed on the `[MASKED]` marker appearing in the parsed `activity_record` (a caller-supplied `[MASKED]` is rejected safe-side too); otherwise PII-containing input is silently processed to `success` |
| Answering a request the caller can correct | fail closed (`status=error`) | complete with an allow-listed `diagnostic_code` + a fixed sentence | **complete with a code + sentence** | An error is how this agent reports a refusal of its own; a request that simply needs correcting is answered, so the school sees what to send instead of a terminal failure. The classification is by whose problem it is, so learner data in the record and a dropped-findings packet stay `status=error` |
| What a notice returns | the packet shape with an empty checklist | the code's sentence alone, no packet | **the sentence alone** | An empty checklist reads as "checked, nothing missing" — the opposite of what happened. Withholding the packet shape makes the two outcomes impossible to confuse |
| Where the reason for a notice is decided | a sentence returned by the service that detected it | an allow-listed code, rendered in one place | **a code** | A sentence assembled next to the detection can pick up the schema key or policy reference that caused it; a code cannot, so "no caller content is ever echoed" holds structurally rather than by review |

## Security Model Mapping

| Layer | Implementation |
|-------|---------------|
| S-1 | All three domain nodes declare `required_trust_level = TrustLevel.VERIFIED_EXTERNAL` — the caller is a school/board governance system supplying its own approved schema and governance content; not `ANONYMOUS` |
| S-2 | Baseline `FunctionNode` PII scan on `user_input` applies automatically and **masks** detected PII to `[MASKED]` before `execute()` (exposes no state finding); `PreProcessNode._extra_security_gate_input()` adds a non-raising oversized-payload/control-char guard; `PreProcessNode.execute()` fails closed on the **`[MASKED]` marker in the parsed `activity_record`** (learner-identifying data present; a caller-supplied `[MASKED]` is rejected the same, safe side) . A malformed/missing schema is a different outcome — the caller can correct it, so the run completes with a `diagnostic_code` and no partial output, never an error. PB coverage: (a) a PII-containing record → **no `parsed_record`, no packet, `status=error`, no `diagnostic_code`**; (b) a record carrying a literal `[MASKED]` marker → same fail-closed reject; (c) a malformed schema → **no `parsed_record`, no packet, `status=success` + the schema code** |
| S-3 | Baseline `FunctionNode` credential scan on output strings applies automatically. **Reviewer-summary safety** (fabricated citation, approval/decision/quality-judgment language, learner-data leak) is checked in `PostProcessNode.execute()` **before** the summary enters the return dict — unsafe → **drop summary, degrade to `status:success`** (`llm_response_rejected`). `_extra_security_gate_output()` is the **terminal** gate that raises `SecurityViolationError` only on a whole-packet violation (silent finding-drop, or learner data / decision / credential reaching the packet) |
| S-4 | `emit_trace_event()` called at least once per node's `execute()` (events listed under Shared Components) |
| S-5 | The LLM is **optional and injected**; the `AZURE_OPENAI_*` values are declared in `agent.yaml` `requires.secrets` as the deployment contract, not as a precondition of the run — the node resolves them **inside `execute(state)`** via `InvocationContext.from_state(state).secrets.require("AZURE_OPENAI_API_KEY")` only when generating the summary (no `ctx` arg, no direct `InvocationContext(...)`, nothing stored on the node/State), and **degrades** (drop summary, `status:success`, `llm_unavailable`) when it is missing/unavailable, so no live-LLM provisioning is required to run/test/STG-deploy (per the approved proposal and the credential-provisioning policy). A keyless/local/fake client needs no secret. Dependencies are exact-pinned in `pyproject.toml` |

## Explicit Out-of-Scope (per the approved proposal)

Educational-quality judgment, activity approval/rejection, learner grading, learner-data processing,
and any contact with students/guardians are explicitly excluded — this template only validates a
proposed GenAI learning-activity record against the school-approved evidence schema and hands a
cited, human-reviewable completeness packet to a teacher/school-leader. These exclusions hold in the
output as well: the S-3 gate rejects any output that asserts such a decision.
