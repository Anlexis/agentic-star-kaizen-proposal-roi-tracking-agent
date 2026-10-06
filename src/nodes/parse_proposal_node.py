"""AgentCore Platform v1.0"""

# MFG-C2-054 — ParseProposalNode
# Inner domain node 2: enrich and classify the validated Kaizen proposal.
#
# Responsibilities:
#   - Derive net annual benefit (savings - cost)
#   - Classify an impact tier from the net annual benefit magnitude
#   - Annotate proposal_data with derived fields for downstream nodes
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Impact-tier thresholds on net annual benefit (JPY).
_TIER_MAJOR = 5_000_000  # >= 5M JPY/yr net benefit
_TIER_SIGNIFICANT = 1_000_000  # >= 1M JPY/yr net benefit
_TIER_MODERATE = 100_000  # >= 100k JPY/yr net benefit


def _classify_impact_tier(net_annual_benefit_jpy: int) -> str:
    """Classify Kaizen impact tier from net annual benefit.

    Returns "major", "significant", "moderate", or "minor".
    """
    if net_annual_benefit_jpy >= _TIER_MAJOR:
        return "major"
    if net_annual_benefit_jpy >= _TIER_SIGNIFICANT:
        return "significant"
    if net_annual_benefit_jpy >= _TIER_MODERATE:
        return "moderate"
    return "minor"


class ParseProposalNode(FunctionNode):
    """Enrich proposal data with net-benefit and impact-tier derivation.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        proposal_data: str  — JSON-serialised proposal payload (ADR-005)

    Output state keys (partial dict):
        proposal_data: str  — enriched JSON string (ADR-005), same key updated
        status:        str
        error_log:     list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        proposal_data: Dict[str, Any] = from_json(state.get("proposal_data"), {})

        if not proposal_data:
            logger.error("ParseProposalNode: proposal_data is empty or missing")
            emit_trace_event(
                "parse_proposal_failed",
                {"reason": "missing_proposal_data"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ParseProposalNode: proposal_data missing in state"],
            }

        proposal_id = proposal_data.get("proposal_id", "unknown")
        estimated_cost_jpy = int(proposal_data.get("estimated_cost_jpy", 0))
        estimated_annual_savings_jpy = int(proposal_data.get("estimated_annual_savings_jpy", 0))

        # ── Derived metrics ───────────────────────────────────────────────────
        net_annual_benefit_jpy = estimated_annual_savings_jpy - estimated_cost_jpy
        impact_tier = _classify_impact_tier(net_annual_benefit_jpy)

        # ── Enrich proposal_data ──────────────────────────────────────────────
        enriched: Dict[str, Any] = dict(proposal_data)
        enriched["net_annual_benefit_jpy"] = net_annual_benefit_jpy
        enriched["impact_tier"] = impact_tier

        logger.info(
            "ParseProposalNode: proposal_id=%s net_benefit=%d impact_tier=%s",
            proposal_id,
            net_annual_benefit_jpy,
            impact_tier,
        )
        emit_trace_event(
            "parse_proposal_complete",
            {
                "proposal_id": proposal_id,
                "net_annual_benefit_jpy": net_annual_benefit_jpy,
                "impact_tier": impact_tier,
            },
            state,
        )

        return {
            "proposal_data": to_json(enriched),
            "status": AgentStatus.SUCCESS.value,
        }
