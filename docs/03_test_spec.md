# Test Specification

Template: `EDU-C2-095` — School GenAI Learning-Activity Evidence Completeness Validator.
Every result below traces to a test that exists in this repository (`tests/unit/*`,
`tests/proof_of_boundary/*`), a CI gate job, or a source-verified property. The template is a
deterministic validator with an optional, non-authoritative LLM reviewer summary that degrades to the
deterministic packet.

## Test Strategy
- Coverage target: 90% (deterministic core + every node `execute()` path + all degrade reasons + S-3
  screening + graph wiring). Coverage is exercised by the CI `run-tests` job (real framework wheel).
- Test types: Unit (`tests/unit/`) / Graph integration (`tests/unit/test_graph_config_wiring.py`,
  real-framework, runs in CI) / Proof-of-Boundary (`tests/proof_of_boundary/`).
- The optional LLM is exercised via fake-LLM dependency injection (`PostProcessNode(llm=…)` /
  `Graph(config={"llm": …})`); tests never mock the framework SecretProvider.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | PASS — `tests/proof_of_boundary/test_state_safety.py::test_state_file_safety` (AST scan of `src/schemas/state.py` rejects `BaseModel`/`InvocationContext` type annotations and credential-like field names). `State` is a flat `TypedDict` of `NotRequired` primitive fields (source) |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | An invalid **request** the caller can correct completes with a notice and no checklist, not an error (see BL-02); learner data in the record still fails closed with `status: error` by design (fail-closed, not raised — see BL-02a). The `SecurityViolationError` mechanism is exercised by the terminal S-3 gate: `tests/unit/test_post_process_node.py::test_s3_gate_raises_on_dropped_findings` raises it when a `correction_required` packet silently drops its findings |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations | PASS — CI `gate-credential-scan` (0 violations); no credential ever placed in State (secret resolved call-time only, never stored) |
| TC-04 | InvocationContext via configurable only | Direct access raises error | Code property + State scan — no node constructs `InvocationContext(...)`; the LLM adapter resolves it via `InvocationContext.from_state(state)` (`src/services/llm.py`). `tests/proof_of_boundary/test_state_safety.py` rejects an `InvocationContext` type held in State; CI `gate-composition` covers the source pattern |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start`/`node_complete`/`node_error` absent from `execute()` body | 0 duplicates — nodes emit only domain events (`activity_record_validated`/`_rejected`, `evidence_completeness_checked`, `completeness_packet_generated`); no framework lifecycle event is re-emitted in `execute()` |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode`) | `TypeError` at class definition if overridden (`@final`) | PASS — `tests/unit/test_framework_compliance_tc06_tc07.py::test_tc06_security_gate_input_cannot_be_overridden` |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode`) | `TypeError` at class definition if overridden (`@final`) | PASS — `tests/unit/test_framework_compliance_tc06_tc07.py::test_tc07_security_gate_output_cannot_be_overridden` |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused before `execute()` | PASS — runtime refusal: for every privileged node, `node(state)` (via `__call__`) with `caller_trust_level = ANONYMOUS` returns `status: error` with an S-1 denial in `error_log` and `execute()` is never called (spied) — `tests/proof_of_boundary/test_pb_input_validation.py::TestTrustBoundary::test_insufficient_trust_refused_before_execute`, parameterized over all three `VERIFIED_EXTERNAL` nodes. The sufficient-trust invoke order through the same `node(state)` path is covered by `test_pb_invoke_order.py` |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | Domain input checks execute | Hook non-trivial — `PreProcessNode._extra_security_gate_input` (bound-size truncation + control-char strip); `tests/unit/test_pre_process_node.py::test_input_guard_truncates_and_strips_control_chars_without_raising` |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | Domain output checks execute | Hook non-trivial — `PostProcessNode._extra_security_gate_output` (terminal whole-packet backstop); `tests/unit/test_post_process_node.py::test_s3_gate_raises_on_dropped_findings`, `::test_s3_gate_passes_valid_packet` |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` inside each `execute()` | Domain event on every path | PASS — a spy on each node module's own `emit_trace_event` proves the domain event fires: `PreProcessNode` (`activity_record_validated` on success, `activity_record_degraded` on a notice, `activity_record_rejected` on fail-closed), `PostProcessNode` (`completeness_packet_degraded` on a notice), `MainNode` (`evidence_completeness_checked`), `PostProcessNode` (`completeness_packet_generated`) — `tests/proof_of_boundary/test_pb_output_validation.py::TestDomainEventEmission`. The upstream-error short-circuit in `main`/`post` is a no-op passthrough; framework lifecycle emits are order-verified by `test_pb_invoke_order.py` |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | PASS — a spy on each node module's `emit_trace_event` proves the domain event fires on every node's work path incl. the pre_process notice and fail-closed branches (`tests/proof_of_boundary/test_pb_output_validation.py::TestDomainEventEmission`); the framework lifecycle emits are order-verified by `test_pb_invoke_order.py` |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | PASS — `tests/proof_of_boundary/test_state_safety.py` |
| PB-3 | Level 2 → External service | Real external service connection | Data retrieved | N/A — no mandatory external service. The deterministic validation core has no external dependency; the **only** external touch is the **optional, best-effort** reviewer-summary LLM, which degrades to the deterministic packet when unavailable. Its adapter path (fake → summary; unavailable → degrade) is covered by `tests/unit/test_graph_config_wiring.py` |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | PASS — `tests/proof_of_boundary/test_import_isolation.py`; CI `gate-import-isolation` |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | PASS — `tests/proof_of_boundary/test_state_safety.py` (State definition holds primitives only; no credential-like field or prohibited type). By design nothing sensitive enters State: learner data is rejected fail-closed in `pre_process` (BL-02a) and secrets are resolved call-time, never stored |
| PB-6 | Invoke execution order | `__call__()`: S-1 → S-4 `node_start` → S-2 → `execute()` → S-3 → S-4 `node_complete` | Order verified | PASS — `tests/proof_of_boundary/test_pb_invoke_order.py::test_call_order_for_every_node` |
| PB-7 | HITL interrupt propagation *(conditional)* | Required only when `hitl.enabled: true` | — | **Auto-waived — non-HITL** — `config/agent.yaml` does not enable HITL; `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` auto-skips (`pytest.mark.skipif`) |

> **Pre-review gate checklist:** PB-1 / PB-2 / PB-4 / PB-5 / PB-6 are asserted and pass; TC-08 insufficient-trust refusal and TC-11 domain-event emission are asserted. PB-3 is N/A (no external service). PB-7 is **Auto-waived — non-HITL**.

## Business Logic Tests

These map the template's declared deterministic behavior and the optional-LLM degrade contract to
concrete tests in this repository (domain-correctness judgment is out of review scope).

| BL-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Deterministic cited missing-evidence checklist (presence + internal consistency + citation backing) | Record vs caller-supplied approved schema/policy | Each absent/empty required item and each inconsistent present item → a `{item, reason, citation}` gap; citation falls back to the schema requirement when none supplied | PASS — `tests/unit/test_main_node.py` (`test_missing_item_produces_cited_gap`, `test_empty_item_is_a_gap`, `test_non_textual_value_is_inconsistent`, `test_gap_without_citation_falls_back_to_schema_reference`, `test_all_items_present_no_gaps`) |
| BL-02 | A request the caller can correct completes with a notice (no partial output) | Empty / malformed / over-deep / non-`dict` payload; no `activity_record` object; `evidence_schema` missing, not an object, or with unusable `required_items` / `citations`; unusable `policy_extracts` | `status: success` + `diagnostic_code`, no `parsed_record`, no `error_log`; the checklist steps are skipped and the output is the code's fixed sentence | PASS — `tests/unit/test_pre_process_node.py` (`test_non_dict_payload_degrades`, `test_missing_schema_degrades`, `test_deeply_nested_json_degrades_without_raising`, `test_invalid_json_degrades`, `test_activity_record_not_object_degrades`, `test_empty_required_items_degrades`, `test_empty_citation_value_degrades`, `test_empty_policy_extract_value_degrades`, `test_a_notice_carries_only_the_code`); end-to-end at the deployed boundary `tests/unit/test_graph_config_wiring.py::TestCallerCorrectableCompletesWithANotice` (`test_malformed_request_completes_with_a_notice`, `test_request_that_is_not_an_object_completes_with_a_notice`, `test_missing_school_schema_completes_with_a_notice`, `test_the_checklist_steps_do_not_run_on_a_notice`) |
| BL-02a | Fail-closed input (no partial output, never a notice) | Baseline-masked learner PII (`[MASKED]`) in the activity record, incl. a caller-supplied literal marker | `status: error`, no `parsed_record`, no `diagnostic_code`, straight to finalize | PASS — `tests/unit/test_pre_process_node.py` (`test_baseline_masked_pii_fails_closed`, `test_caller_supplied_mask_marker_is_rejected`); `tests/proof_of_boundary/test_pb_input_validation.py::test_masked_pii_is_not_answered_as_a_correctable_request`; end-to-end `tests/unit/test_graph_config_wiring.py::test_learner_data_in_the_record_is_still_an_error_not_a_notice` |
| BL-02b | A notice carries nothing the request sent (security) | A distinctive value in a request that is refused | The sentence is derived from the allow-listed code alone — no record field, schema key, policy reference or request value in the whole response | PASS — `tests/unit/test_pre_process_node.py::test_a_notice_carries_only_the_code`, `::test_no_notice_can_interpolate_anything`, `::test_every_code_has_a_sentence_of_its_own`, `::test_an_unclassified_code_still_fails_closed`, `::test_an_unknown_code_falls_back_with_its_handle`; end-to-end `tests/unit/test_graph_config_wiring.py::test_the_notice_never_echoes_the_rejected_request` |
| BL-03 | Deterministic completeness packet (LLM-free authority) | Missing-evidence findings | `{status: evidence_complete\|correction_required, missing_evidence, review_required: true}` assembled with no LLM | PASS — `tests/unit/test_post_process_node.py` (`test_correction_required_packet_with_gaps`, `test_evidence_complete_when_no_gaps`); short-circuit on upstream error (`test_short_circuits_on_upstream_error`) |
| BL-04 | Optional reviewer-summary DI + controlled degrade | Fake LLM (safe) / no client / missing secret / provider error / malformed-or-unsafe summary | Safe → summary attached; else summary dropped, `status: success`, `reviewer_summary_available: false` + allowlisted reason (`llm_unavailable` / `llm_timeout` / `llm_response_rejected`) | PASS — `tests/unit/test_post_process_node.py` (`test_safe_summary_attached`, `test_no_llm_degrades_unavailable`, `test_missing_secret_degrades_unavailable`, `test_provider_error_degrades_timeout`, `test_unsafe_summary_dropped_degrade_rejected`) |
| BL-05 | S-3 summary safety — drop-and-degrade, packet preserved | Fake LLM returning learner PII (email/phone/ID) / credential / approval-decision / fabricated citation | Summary dropped (`llm_response_rejected`), deterministic packet preserved; never surfaced to State/output | PASS — `tests/unit/test_post_process_node.py` (`test_email_pii_summary_dropped_packet_preserved`, `test_phone_pii_summary_dropped`, `test_student_id_pii_summary_dropped`, `test_credential_summary_dropped_via_substrate`, `test_fabricated_citation_dropped`); output-boundary PB coverage in `tests/proof_of_boundary/test_pb_output_validation.py::TestOutputBoundary` |
| BL-06 | Graph composition + DI seam + terminal output | `Graph(config={"llm": fake})` / `Graph()` / `Graph.invoke` with a `VERIFIED_EXTERNAL` context | Flat backbone registered; fake DI reaches PostProcessNode; config-less deploy injects the default adapter; end-to-end invoke surfaces the packet as `output` **and** the reviewer summary via the `get_output` override | PASS (CI `run-tests`) — `tests/unit/test_graph_config_wiring.py` (`test_flat_backbone_nodes_registered`, `test_config_injects_fake_llm`, `test_default_deploy_injects_real_adapter`, `test_end_to_end_invoke_surfaces_packet_and_summary`) |
| BL-07 | LLM-free provisional operation (`status: success` without a live LLM) | `Graph()` deploy path, no `AZURE_OPENAI_*` values bound | Deterministic core completes `status: success`; only the optional summary degrades (`reviewer_summary_available: false`) | PASS (CI `run-tests`) — `tests/unit/test_graph_config_wiring.py::test_end_to_end_degrades_without_secret`. Production-only SecretProvider provisioning and the STG sign-off case are recorded in the operation guide |

## Additional Regression Coverage

Guards added alongside the changes in this revision. Each entry names the test and the
condition it accepts on; they supplement the mandatory TC / PB items above and replace none
of them.

| Concern | Test | Acceptance condition |
|---|---|---|
| Deployment startup contract | `tests/unit/test_entry_point_import.py` | `src/api/server.py` — the module `uvicorn src.api.server:app` loads — imports successfully, exposes `app` and a compiled `agent`, and **every key of the shipped `config/config.yaml` reaches `agent.config`** — so no runtime parameter is inert on the standalone path while still applying on the AgentRegistry path. |


## Test Execution Summary
- Execution: CI `run-tests` job (real framework wheel) on every push/MR; local unit runs for the
  deterministic node/service logic.
- Test files: `tests/unit/test_pre_process_node.py`, `test_main_node.py`, `test_post_process_node.py`,
  `test_graph_config_wiring.py`, `test_framework_compliance_tc06_tc07.py`;
  `tests/proof_of_boundary/{test_pb_invoke_order,test_pb_input_validation,test_pb_output_validation,test_import_isolation,test_state_safety,test_pb7_hitl_interrupt_propagation}.py`.
- Asserted/pass: PB-1 / PB-2 / PB-4 / PB-5 / PB-6; TC-01 / 03 / 06 / 07 / 08 / 09 / 10 / 11. TC-02 / 04 / 05
  satisfied via test / CI gate / source as mapped. PB-3: N/A (no external service). PB-7: auto-waived (non-HITL).
- TC-08 refusal (`test_pb_input_validation.py::TestTrustBoundary`) and PB-1 / TC-11 domain-event
  emission (`test_pb_output_validation.py::TestDomainEventEmission`) are asserted; all mandatory TC/PB items are now covered.
- Coverage: target 90%. The deterministic core, every node `execute()` path, all four degrade reasons,
  S-3 screening, and graph wiring are exercised by the tests above (run in CI `run-tests`).

## LLM Availability Matrix

Expected `status` and returned artifact for each state of the Azure OpenAI connection. The three
connection values (`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT`) are
resolved at call time from the invocation-bound `SecretProvider`. Declaring them in
`config/agent.yaml` `requires.secrets` states the deployment contract; it does not change any row
below. The reviewer summary is non-authoritative; the deterministic completeness packet is unaffected.

| Condition | Expected `status` | Artifact returned | Result |
|-----------|-------------------|-------------------|--------|
| Connection values resolve, provider responds | `success` | Completeness packet + `reviewer_summary`, `reviewer_summary_available: true` | Pass (`test_graph_config_wiring::test_end_to_end_invoke_surfaces_packet_and_summary`) |
| A connection value cannot be resolved | `success` | Completeness packet only; `reviewer_summary_unavailable_reason: llm_unavailable` | Pass (`test_post_process_node::test_missing_secret_degrades_unavailable`, `test_graph_config_wiring::test_end_to_end_degrades_without_secret`) |
| Provider fails (error / timeout / malformed response) | `success` | Completeness packet only; `reviewer_summary_unavailable_reason: llm_timeout` | Pass (`test_post_process_node::test_provider_error_degrades_timeout`) |
