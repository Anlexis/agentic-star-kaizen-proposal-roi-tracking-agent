# Test Specification — MFG-C2-054 Kaizen Proposal Digitization & ROI Tracking Agent

## 1. Test Strategy

- **Agent:** MFG-C2-054 — Kaizen Proposal Digitization & ROI Tracking Agent (Cat 2,
  DocGeneration pattern, two-layer nested graph: outer `AgentBaseGraph` backbone +
  inner `DomainWorkflowGraph` `BaseGraph`).
- **Coverage target:** ≥ 90% of `src/nodes/` + `src/graph/` branches.
- **Test types:** Unit (per node + graph wiring) · Proof-of-Boundary (framework
  security/serialization contracts) · Backbone invoke (full `Graph().invoke()`).
- **Framework provisioning:** `framework` (agenticstar-agentcore) is supplied by
  CI — the CI stub package on `PYTHONPATH` for the CI stub package arm, or the wheel from the
  package registry for the wheel arm. Tests import the REAL Wave-1 modules on
  `develop`; there are no stub nodes.
- **S-4 audit:** `emit_trace_event` is patched at the node module level in unit
  tests to avoid audit-backend calls, never via a `sys.modules` stub (which would
  break the real `shared` package the framework loads at import time).

### Test file map

| File | Scope |
|------|-------|
| `tests/unit/test_nodes.py` | All 8 domain/backbone nodes + outer & inner graph wiring |
| `tests/unit/test_main_node.py` | Deprecated `MainNode` stub — execute() contract kept green |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 per-node + backbone invoke order (VERIFIED_EXTERNAL) + S-1 gate + payload alignment |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 Level-0 import isolation (AST scan) |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2/PB-5 State msgpack/credential safety (AST scan) |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 HITL interrupt-propagation (skip stub — no cross-boundary HITL) |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | TC-06/TC-07 — the framework's S-2/S-3 gates cannot be overridden by a domain node |
| `tests/unit/test_input_contract.py` | The caller-data contract at the node boundary: non-finite/out-of-range/boolean numerics, structural caps, identifier validation, injection and credential screening, and the legitimate-prose negative controls |
| `tests/unit/test_output_invariant.py` | The output boundary: the union credential gate, node-level clearing, `get_output()` withholding on every non-success status, and identifier fidelity |
| `tests/integration/test_invoke_contract.py` | End-to-end through the real ASGI `/invoke`: auth boundary, the committed deploy payload, ROI reachability, rejection paths, and the error envelope |
| `tests/integration/test_outer_invoke_returns_domain_result.py` | The compiled outer graph surfaces the domain result on success and withholds it on a blocked output |

### Canonical valid payload (PB-6 `_VALID_PAYLOAD`)

The SUCCESS-yielding, positive-ROI Kaizen proposal used by the backbone invoke test
and by `deploy/invoke_payload.json` (the two MUST stay identical — asserted by
`test_invoke_payload_matches_pb6`):

```json
{
  "proposal_id": "MFG-KAIZEN-20260712-001",
  "title": "Reduce die-changeover time on the Line 3 stamping press",
  "submitter": "T. Sato",
  "department": "Press Shop",
  "category": "efficiency",
  "current_state": "Die changeover on the Line 3 stamping press takes 48 minutes on average ...",
  "proposed_improvement": "Adopt an SMED (single-minute exchange of die) kit ...",
  "affected_processes": ["die changeover", "press setup", "first-article inspection"],
  "estimated_cost_jpy": 1200000,
  "estimated_annual_savings_jpy": 4800000,
  "implementation_period_months": 3
}
```

ROI: net annual benefit = 4,800,000 − 1,200,000 = **3,600,000 JPY/yr**; payback =
1,200,000 ÷ (4,800,000 ÷ 12) = **3.0 months** (≤ 24) ⇒ `roi_positive = True`,
`roi_tier = excellent`, `impact_tier = significant`.

`tests/integration/test_invoke_contract.py` reads this payload from
`deploy/invoke_payload.json` rather than restating it, so the request the deployment
evidence posts and the request the suite asserts cannot drift apart.

Note that the platform privacy filter masks `"department": "Press Shop"` in this very
payload — its heuristic reads any two consecutive Title Case words as a person name. The
document therefore reports that field as redacted rather than typesetting the sentinel;
`test_platform_redacted_field_is_named_not_certified` pins that.

## 2. Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Where |
|-------|------|----------------|-------|
| TC-01 | State contract: flat `TypedDict`, domain fields `NotRequired`, no Pydantic/dataclass | AST scan: 0 violations | `test_state_safety.py` |
| TC-02 | Invalid/empty/non-JSON input rejected at PreProcessNode | `status=error`, error_log populated | `TestPreProcessNode` |
| TC-03 | No JWT/credential in State | CI `gate-credential-scan`: 0 violations | CI + `test_state_safety.py` |
| TC-04 | `execute(self, state)` contract — no `_invoke_impl` | Signature `(self, state)`, `_invoke_impl` absent | `test_execute_signature_is_state_first`, `test_main_node.py` |
| TC-05 | S-4: `emit_trace_event()` called inside each node `execute()` | ≥1 domain event per node (positional form) | verified by CoE preflight S-4/#3 |
| TC-08 | S-1: `required_trust_level` enforced in `__call__` before `execute()` | ANONYMOUS caller → refused; VERIFIED_EXTERNAL → admitted | `TestS1TrustGate` |
| TC-08a | Outer `PreProcessNode` = VERIFIED_EXTERNAL; inner nodes + post_process = ANONYMOUS | trust levels asserted per node | `test_trust_level_*` |
| TC-11 | S-3 output gate on post_process | credential pattern → redacted + `status=error`; clean → pass | `TestPostProcessNode` |

## 3. Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Where |
|-------|----------|------|----------------|-------|
| PB-2 | State serialization | AST scan of `src/schemas/state.py` | primitives only; no Pydantic/dataclass | `test_state_safety.py` |
| PB-4 | Import isolation | AST scan of `src/` | 0 Level-0 (`agenticstar` / platform) imports | `test_import_isolation.py` |
| PB-5 | Checkpoint safety | no credential-named fields / prohibited types in State | inspection pass | `test_state_safety.py` |
| PB-6 | Invoke execution order (per node) | `__call__`: S-4 node_start → S-1 gate → S-2 input gate → `execute()` → S-3 output gate → S-4 node_complete | order verified for every `src/nodes/` class | `TestInvokeOrder` |
| PB-6b | Backbone invoke order | full `Graph().invoke(_VALID_PAYLOAD, ctx=VERIFIED_EXTERNAL)` | `status=success`; node_history = `[Initialize, PreProcess, KaizenDocumentGraphNode, PostProcess, Finalize]` | `TestBackboneInvokeOrder` |
| PB-6c | Real external caller | `InvocationContext(caller_trust_level=VERIFIED_EXTERNAL)` — **never** `for_internal()` | inner ANONYMOUS nodes accept the passthrough trust; SUCCESS end-to-end | `TestBackboneInvokeOrder` |
| PB-6d | Payload alignment | `deploy/invoke_payload.json["input"] == _VALID_PAYLOAD` | Stage-5 deploy-stg invoke exercises the PB-6 payload | `test_invoke_payload_matches_pb6` |
| PB-7 | HITL interrupt propagation | skip stub — `propagate_hitl=False`, no cross-boundary interrupt() checkpoint | skipped with reason (real assertion when HITL wired) | `test_pb7_hitl_interrupt_propagation.py` |

## 4. Business Logic Tests

| BL-ID | Test | Input | Expected Result | Where |
|-------|------|-------|----------------|-------|
| BL-01 | Happy-path digitization | `_VALID_PAYLOAD` | 6-section Kaizen document + ROI analysis; `KAIZEN PROPOSAL` / `DIGITIZED RECORD` + proposal_id present | `test_backbone_invoke_succeeds_and_returns_output`, `TestInnerDomainGraph` |
| BL-02 | Category normalisation | `category="productivity"` / `"cost"` | `efficiency` / `cost_reduction` | `test_category_aliases_are_normalised` |
| BL-03 | Numeric parsing | `estimated_cost_jpy="1200000"` | parsed to `int` 1,200,000 | `test_numeric_fields_are_coerced` |
| BL-03a | Numeric fail-closed | `"NaN"` / `"Infinity"` / `True` / `1e308` / `-1` on any numeric field | `status=error` naming the field; the value never echoed | `test_non_finite_numbers_are_refused`, `test_out_of_range_numbers_are_refused`, `test_booleans_are_refused_as_numbers` |
| BL-04 | Net-benefit derivation | cost 1.2M, savings 4.8M | `net_annual_benefit_jpy = 3,600,000` | `test_derives_net_annual_benefit` |
| BL-05 | Impact-tier classification | net-benefit matrix | major / significant / moderate / minor per thresholds | `TestParseProposalNode` |
| BL-06 | Positive ROI within horizon | cost 1.2M, savings 4.8M | `roi_positive=True`, payback 3.0, tier `excellent` | `test_positive_roi_within_horizon` |
| BL-07 | Negative net benefit | cost 5M, savings 1M | `roi_positive=False`, tier `not_viable` | `test_negative_net_benefit_not_viable` |
| BL-08 | Payback over horizon | cost 10M, savings 1M | payback 120 months > 24 ⇒ `roi_positive=False` | `test_payback_over_horizon_not_positive` |
| BL-08a | The horizon decides an outcome | cost 4.6M vs 5.0M, savings 2.4M | payback 23 ⇒ positive; payback 25 ⇒ not positive | `test_payback_horizon_actually_decides_an_outcome` |
| BL-08b | `acceptable` tier is reachable | cost 5M, savings 3M | payback 20 months ⇒ `roi_positive=True`, tier `acceptable`, `first_year_net_positive=False` | `test_roi_verdict_moves_with_the_figures`, `test_first_year_net_is_reported_separately_from_the_verdict` |
| BL-09 | Document assembly | 6 sections + ROI dict | all 6 headers + `7. ROI Analysis`; ROI YES/NO + tier reflects flag | `TestOutputFormatNode` |
| BL-10 | Graph key coupling | inner `get_output` ↔ outer `merge_output` | 5 coupled keys mapped; `merge_output` returns changed keys only | `TestOuterGraphComposition`, `TestInnerDomainGraph` |

### Negative / boundary cases

| Case | Node | Expected |
|------|------|----------|
| empty `user_input` | PreProcessNode | `status=error`, "empty" |
| invalid JSON | PreProcessNode | `status=error`, "invalid JSON" |
| JSON root not an object | PreProcessNode | `status=error`, "object" |
| missing `title` | PreProcessNode | `status=error`, "title" |
| empty `proposal_id` | InputValidateNode | `status=error`, "proposal_id" |
| empty narrative (`title`) | InputValidateNode | `status=error`, "non-empty" |
| missing `proposal_data` | Parse / Generate / Calculate | `status=error` |
| missing `proposal_sections` | OutputFormatNode | `status=error` |
| empty `kaizen_document` | PostProcessNode | fallback message, `status=success` |
| credential leak in output | PostProcessNode | redacted + output-bearing fields cleared, `status=error` (S-3) |
| credential-shaped caller text | PreProcessNode | refused with the violation class named, value never echoed |
| chat-template control token / sentence-initial directive | PreProcessNode | refused by the template's own screen, asserted on a direct `execute()` |
| payload > 64,000 chars, list > 50 entries, field over its cap | PreProcess / InputValidate | refused before rendering |
| malformed `proposal_id` | PreProcessNode | refused (never rewritten) |
| unauthenticated or wrong-token caller | `src/api/server.py` | HTTP 401, generic body |

## 5. Test Execution Summary

- Execution: `pytest tests/` against the published framework wheel.
- PB-7 ships as a skip stub by design (no cross-boundary HITL) — 1 skipped.
- Gates: `gate-dep-pinning`, `gate-stub-check`, `gate-cat-consistency`,
  import-isolation, composition, invoke-chain, credential-scan, trust-level,
  scaffold-integrity — all PASS.
- Coverage: node + graph modules exercised on both success and error paths.
