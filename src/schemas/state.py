"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# MFG-C2-054 — Kaizen Proposal Digitization & ROI Tracking Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# ADR-005 compliance: all dict/list-valued fields are stored as JSON-
# serialized Optional[str].  Use to_json() / from_json() helpers below
# at every producer and consumer node — one contract end-to-end.
# Never type a dict/list field as a bare dict/list; that causes msgpack
# serialization failures and a CoE Stage-6 state-contract finding.

import json
from typing import Any, NotRequired, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a value to a JSON string for State storage (ADR-005)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON string from State storage (ADR-005)."""
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for MFG-C2-054.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.

    ADR-005: dict/list fields use JSON-serialized Optional[str].
    formatted_output is NOT re-declared here — it is inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer — set by PreProcessNode (pre_process backbone, S-1)
    # ------------------------------------------------------------------

    # Validated and normalised JSON string of the Kaizen proposal payload.
    # Produced by PreProcessNode; consumed by inner InputValidateNode.
    validated_input: NotRequired[Optional[str]]

    # JSON-serialised channel/request metadata dict (ADR-005: stored as str).
    # Shape: {"source": str, "channel": str, "proposal_id": str}
    enriched_context: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Inner layer — domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # JSON-serialised parsed/enriched Kaizen proposal (ADR-005: stored as str).
    # Shape: {proposal_id, title, submitter, department, category,
    #   current_state, proposed_improvement, affected_processes (list),
    #   estimated_cost_jpy (int), estimated_annual_savings_jpy (int),
    #   implementation_period_months (int), impact_tier (str),
    #   net_annual_benefit_jpy (int)}
    proposal_data: NotRequired[Optional[str]]

    # JSON-serialised document section dict (ADR-005: stored as str).
    # Keys match the digitized Kaizen document sections:
    #   proposal_overview, background_current_state, proposed_improvement,
    #   expected_effects, implementation_plan, cost_and_roi_summary
    # Each value is the rendered text for that section.
    proposal_sections: NotRequired[Optional[str]]

    # JSON-serialised ROI computation result (ADR-005: stored as str).
    # Shape: {roi_percent: float, payback_period_months: float,
    #   net_annual_benefit_jpy: int, estimated_cost_jpy: int,
    #   estimated_annual_savings_jpy: int, roi_positive: bool, roi_tier: str,
    #   basis: str}
    roi_analysis: NotRequired[Optional[str]]

    # True if the proposal's ROI clears the positive-return threshold.
    # Criteria: net_annual_benefit_jpy > 0 AND payback_period_months <= 24.
    roi_positive: NotRequired[Optional[bool]]

    # Final formatted Kaizen document (plain text, digitized + ROI-annotated).
    # Assembled by inner OutputFormatNode from proposal_sections + roi_analysis.
    kaizen_document: NotRequired[Optional[str]]

    # Manifest-forwarded declared LLM prompt template path (config passthrough).
    # Seeded by DomainWorkflowGraph._extra_initial_state() so the declared
    # runtime config is reachable at GenerateProposalSectionsNode on the real
    # invoke path (SR #12 Medium 2026-07). v1 is deterministic — this is the
    # documented production LLM wiring point, not a secret and not consumed by
    # an LLM in v1.
    system_prompt_template: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Outer layer — set by PostProcessNode (post_process backbone, S-3)
    # ------------------------------------------------------------------

    # Primary result surfaced to the caller.
    # Set to the same content as kaizen_document after the S-3 gate passes.
    # formatted_output (from AgentState) is also set by PostProcessNode.
    result: NotRequired[Optional[str]]

    # ------------------------------------------------------------------
    # Tracing / audit — framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: NotRequired[Optional[str]]
    correlation_id: NotRequired[Optional[str]]
    # node_history inherited from AgentState
