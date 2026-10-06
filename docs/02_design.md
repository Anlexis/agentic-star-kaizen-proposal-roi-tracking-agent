# Template Design Specification — MFG-C2-054 Kaizen Proposal Digitization & ROI Tracking Agent

## Position in AgentCore Architecture

- **Agent Class**: KaizenProposalDigitizationROITrackingAgent
- **L1 Base** (framework base class): AgentBaseGraph — direct framework inheritance
- **Pattern**: Cat 2 — DocGenerationAgent (two-layer nested workflow)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); ADR-005 JSON-serialised strings for all dict/list fields
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; Cat 2 nested via `GraphNode`)

## Domain Context

Kaizen (改善) proposal digitization and ROI tracking for a Japanese manufacturer. Turns a
free-form factory-floor improvement proposal submitted by a line worker or supervisor into a
standardized, digitized Kaizen record with an auditable ROI verdict, ready for review-board
evaluation, portfolio roll-up, and horizontal deployment (yokoten).

**Business value**: standardizes ad-hoc paper/Excel Kaizen proposals, computes ROI consistently
(so weak proposals are filtered and strong ones prioritized), and produces a reusable record for
cross-line/plant reuse.

## Architecture Overview

### Backbone (outer AgentBaseGraph — fixed 5-node pipeline)

```
START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
                                         ↓ (retry, max 3)
                                       pre_process
```

### Inner Domain Workflow (DomainWorkflowGraph — linear 5-node pipeline)

```
START → input_validate → parse_proposal → generate_proposal_sections
          → calculate_roi → output_format → END
```

### Node Configuration

| Node | Class | File | Trust | Responsibility | Input Keys | Output Keys |
|------|-------|------|-------|---------------|------------|-------------|
| initialize | InitializeNode | framework | — | session init | — | session_id, schema_version |
| pre_process | PreProcessNode | src/nodes/pre_process_node.py | VERIFIED_EXTERNAL | S-1 trust, size cap, JSON validation, template-owned injection + credential screen, identifier check | user_input, input_context | validated_input, enriched_context |
| main | KaizenDocumentGraphNode | src/graph/graph.py | — | delegates to DomainWorkflowGraph | validated_input | kaizen_document, proposal_sections, roi_analysis, roi_positive |
| post_process | PostProcessNode | src/nodes/post_process_node.py | ANONYMOUS | S-3 output gate (union scan), clears pre-gate fields on violation, sets formatted_output | kaizen_document | formatted_output, result (+ cleared domain fields on violation) |
| finalize | FinalizeNode | framework | — | response metadata | — | response_metadata, total_time_ms |
| input_validate (inner) | InputValidateNode | src/nodes/input_validate_node.py | ANONYMOUS | domain field validation, finite+bounded numeric parsing, structural caps, category normalisation, redaction record | validated_input | proposal_data |
| parse_proposal (inner) | ParseProposalNode | src/nodes/parse_proposal_node.py | ANONYMOUS | net-benefit + impact-tier derivation | proposal_data | proposal_data (enriched) |
| generate_proposal_sections (inner) | GenerateProposalSectionsNode | src/nodes/generate_proposal_sections_node.py | ANONYMOUS | generate 6 Kaizen document sections (deterministic templates; `generation_mode: deterministic`) | proposal_data | proposal_sections |
| calculate_roi (inner) | CalculateRoiNode | src/nodes/calculate_roi_node.py | ANONYMOUS | ROI %, payback, positive-return verdict | proposal_data | roi_analysis, roi_positive |
| output_format (inner) | OutputFormatNode | src/nodes/output_format_node.py | ANONYMOUS | assemble final digitized document | proposal_sections, roi_analysis | kaizen_document, result |

### Data Flow

```
user_input (JSON Kaizen proposal payload)
    │
    ▼ PreProcessNode (VERIFIED_EXTERNAL, S-1)
validated_input (normalised JSON string)
enriched_context (JSON string — ADR-005)
    │
    ▼ KaizenDocumentGraphNode → DomainWorkflowGraph
    │   InputValidateNode              → proposal_data (JSON string — ADR-005)
    │   ParseProposalNode              → proposal_data (enriched, ADR-005)
    │   GenerateProposalSectionsNode   → proposal_sections (JSON string — ADR-005)
    │   CalculateRoiNode               → roi_analysis (JSON string), roi_positive (bool)
    │   OutputFormatNode               → kaizen_document (str), result (str)
    ▼ merge_output
kaizen_document, proposal_sections, roi_analysis, roi_positive → outer state
    │
    ▼ PostProcessNode (ANONYMOUS, S-3)
formatted_output (S-3-gated kaizen_document), result
```

### State Definition

| Field | Type | Purpose | Producer |
|-------|------|---------|----------|
| validated_input | NotRequired[Optional[str]] | Normalised proposal JSON string | PreProcessNode |
| enriched_context | NotRequired[Optional[str]] | JSON: {source, channel, proposal_id} | PreProcessNode |
| proposal_data | NotRequired[Optional[str]] | JSON: parsed+enriched proposal payload | InputValidateNode / ParseProposalNode |
| proposal_sections | NotRequired[Optional[str]] | JSON: {section_name: text, ...} × 6 sections | GenerateProposalSectionsNode |
| roi_analysis | NotRequired[Optional[str]] | JSON: ROI computation result | CalculateRoiNode |
| roi_positive | NotRequired[Optional[bool]] | True if the proposal clears the ROI threshold | CalculateRoiNode |
| kaizen_document | NotRequired[Optional[str]] | Final formatted Kaizen document text | OutputFormatNode |
| result | NotRequired[Optional[str]] | Same as kaizen_document (backbone convention) | OutputFormatNode / PostProcessNode |

**ADR-005 constraint**: all dict/list-valued fields use JSON-serialised `Optional[str]`. `to_json()` / `from_json()` helpers are defined in `src/schemas/state.py` and used at every producer/consumer boundary — one contract end-to-end.

**Prohibited**: re-declaring `formatted_output` (inherited from AgentState), credentials in State, Pydantic models.

### Input Payload Schema (user_input JSON)

```json
{
  "proposal_id": "KAIZEN-2026-0042",
  "title": "Reduce changeover time on Assembly Line 3",
  "submitter": "Yamada Taro",
  "department": "Assembly",
  "category": "efficiency",
  "current_state": "Model changeover on Line 3 takes 45 minutes, causing 6% idle time.",
  "proposed_improvement": "Introduce SMED quick-die-change carts and pre-staged tooling.",
  "affected_processes": ["changeover", "line3"],
  "estimated_cost_jpy": 800000,
  "estimated_annual_savings_jpy": 3600000,
  "implementation_period_months": 2
}
```

### Caller-Data Contract (enforced, not documented-only)

Every field below is validated in `src/nodes/input_validate_node.py` via
`src/security/screening.py`. Limits are enforced; a value that violates one is refused with an
error naming the field and never echoing its value.

| Field | Rule | On violation |
|---|---|---|
| `proposal_id` | required; 1–64 chars of `[A-Za-z0-9._-]`, preserved byte-for-byte | refuse |
| `title` | required; ≤200 chars, collapsed to a single line | refuse |
| `current_state`, `proposed_improvement` | required; ≤8,000 chars each | refuse |
| `submitter`, `department` | ≤200 chars, collapsed to a single line | refuse |
| `category` | reduced to `[a-z0-9_]{1,32}`, then alias-mapped | fall back to `uncategorized` |
| `affected_processes` | ≤50 entries, ≤120 chars each | refuse |
| `estimated_cost_jpy`, `estimated_annual_savings_jpy` | finite, 0 … 1,000,000,000,000 | refuse |
| `implementation_period_months` | finite, 0 … 600 | refuse |
| whole payload | ≤64,000 chars, capped before parsing | refuse |
| `input_context.channel` | reduced to `[a-z0-9_]{1,32}` | fall back to `unknown` |

**Numbers fail closed.** `NaN` and `±Infinity` parse cleanly through `float()` and then compare
False against every threshold, so an unguarded value turns the positive-return verdict into a
silent success. There is no default-to-zero path: a proposal whose cost cannot be read is a
proposal that cannot be scored, and printing a fabricated `0 JPY` as the submitter's own figure on
a document that reads as authoritative is the worse failure. Booleans are rejected explicitly —
`True` is an `int` in Python and would otherwise be read as `1`.

**Free text stays free, structured positions do not.** The narrative fields are the product, so
they are length-bounded but not alphabet-restricted. The values that render into structured header
rows — the proposal id, the category, the channel — are restricted, so no caller string can
introduce a line break, a delimiter or a directive into a row a reader would take as the agent's
own output.

### Output Invariant

The enforced output invariant is the credential/secret gate described in *Security Configuration*.

**The monetary precision grid does not apply to this template**, and that is a decision rather than
an omission. The grid exists to stop a report leaking raw line items from a corpus the caller never
saw. Here every figure in the document is either a number the submitter typed into their own
proposal or one derived from it by a formula printed alongside it in the ROI section — there is no
third-party data to protect, and rounding a submitter's declared ¥1,200,000 cost to the nearest
thousand would misstate their proposal back to them.

The identifier-safety property that grid normally provides is obtained directly instead: caller
identifiers are validated against an inert alphabet and reproduced byte-for-byte, so a shop-floor
id such as `MFG-KAIZEN-20260712-001` reaches the document unchanged, and
`tests/unit/test_output_invariant.py` pins that.

### Output Document Sections

1. **Proposal Overview** — ID, title, submitter, department, category, impact tier
2. **Background / Current State** — affected processes + current situation
3. **Proposed Improvement** — the concrete change
4. **Expected Effects** — quantified savings + net annual benefit + impact tier
5. **Implementation Plan** — period, one-time cost, standard rollout steps
6. **Cost & ROI Summary** — cost vs. savings framing
7. **ROI Analysis** (trailer) — ROI %, payback period, positive-return verdict, ROI tier

## Security Configuration

| Layer | Gate | Implementation |
|-------|------|---------------|
| S-1 | Trust enforcement | PreProcessNode `required_trust_level = VERIFIED_EXTERNAL` |
| S-2 | Input validation | PreProcessNode (size cap, structural JSON, injection screen, credential screen, identifier check) + InputValidateNode (domain rules, finite+bounded numerics, structural caps). Screens live in `src/security/screening.py` and are enforced inside the nodes, so they still apply on a direct `execute()` call with no framework wrapper in front |
| S-3 | Output gate | Module-level `_security_gate_output()` in `post_process_node.py`, enforced in PostProcessNode.execute(); also declared on the agent class (delegates to the same scanner). Scans the UNION of the template's patterns (API keys, JWTs, Bearer tokens, credential assignments) and the framework's `detect_credentials` (Stripe keys, AWS key ids, database connection strings). On a violation it returns a truthy sanitised stub AND clears the pre-gate output-bearing state fields; `get_output()` independently withholds the structured domain fields on any non-success status |
| S-4 | Audit logging | `emit_trace_event()` in every node's `execute()` (at least one domain-specific event) |
| S-5 | Credential handling | No credentials in State; secrets via InvocationContext only |

**config/config.yaml** (runtime parameters; `config/agent.yaml` is the flat manifest and
carries no runtime block):
```yaml
max_retry: 3
timeout_s: 30
llm:
  system_prompt_template: prompts/kaizen_proposal.j2
  temperature: 0.0
  max_tokens: 4000
security:
  s3_gate_enabled: true
```

`src/graph/graph.py` reads this file when the agent is constructed without an explicit
config, so the declared values govern a standalone deployment as well as a registry-managed
one. Each key is annotated in the file itself with the code that reads it; `timeout_s` and
`security.s3_gate_enabled` are declarative — no code path reads either, and the output gate
runs unconditionally by design rather than under a switch.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (`config["configurable"]` — session_id, trust_level)
- [x] S-3: module-level `_security_gate_output()` in `post_process_node.py` — credential/secret scan on output string
- [x] S-4: `emit_trace_event()` — at least one domain-specific event per node `execute()`
- [x] `to_json()` / `from_json()` helpers in `src/schemas/state.py` — ADR-005 serialisation contract

### Composition Pattern

- **Pattern**: Cat 2 nested two-layer — GraphNode wrapping inner BaseGraph
- **Outer graph**: `KaizenProposalDigitizationROITrackingAgent(AgentBaseGraph)` — fixed 5-node backbone
- **Inner graph**: `DomainWorkflowGraph(BaseGraph)` — 5-node linear domain pipeline
- **Error propagation**: propagate (SubgraphError on inner failure; outer backbone retries pre_process)

## Import Isolation Confirmation
- [x] Template does not import the Level-0 platform SDK
- [x] Import targets: `framework/` and `shared/` only (no Level-0 SDK)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed sequential pipeline; no LLM reasoning loop required |
| Composition pattern | Cat 1 (flat) | Cat 2 (nested GraphNode) | Cat 2 nested | 5 sequential domain steps; DocGenerationAgent pattern |
| ROI computation | Inline in generate | Separate CalculateRoiNode | Separate node | Single-responsibility principle (CoE §2-1) |
| State dict fields | bare dict | JSON-serialised str | JSON-serialised str | ADR-005: msgpack serialisation safety |
| LLM integration | Real LLM | Deterministic stub | Deterministic stub (v1) | No `framework.services.llm_client` in SDK v1.0.0rc1 |
| Category normalisation | separate node | In InputValidateNode | In InputValidateNode | Reduces node count; normalisation is still validation scope |
