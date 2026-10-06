"""AgentCore Platform v1.0"""

# MFG-C2-054 — CalculateRoiNode
# Inner domain node 4: ROI computation and positive-return determination.
#
# Computes the return-on-investment metrics for the Kaizen proposal so the
# digitized document carries an auditable ROI verdict:
#   - roi_percent             = (annual_savings - cost) / cost * 100
#   - payback_period_months   = cost / (annual_savings / 12)
#   - roi_positive            = annual_savings > 0 AND payback <= 24 months
#   - first_year_net_positive = annual_savings > cost (reported separately)
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

# Positive-return threshold: payback within this horizon (months).
_PAYBACK_HORIZON_MONTHS = 24


def _determine_roi(
    estimated_cost_jpy: int,
    estimated_annual_savings_jpy: int,
) -> Dict[str, Any]:
    """Evaluate ROI metrics and the positive-return verdict.

    Returns a dict with roi_percent, payback_period_months, the net benefit,
    and roi_positive/roi_tier for full traceability in the ROI section.

    The verdict is the payback test: a proposal is a positive return when it
    recovers its one-time cost within the declared horizon.

    It used to additionally require a positive FIRST-YEAR net benefit, and that
    conjunction made the horizon meaningless. ``payback = 12 * cost / savings``,
    so ``savings > cost`` — the first-year test — already implies a payback
    under 12 months. Measured across a 81x81 grid of cost/savings combinations:
    the longest payback any positive verdict ever carried was 11.8 months, the
    24-month constant never once decided an outcome, and the ``acceptable`` tier
    (payback 12-24 months) was returned zero times.

    What that cost in practice: a ¥5,000,000 improvement saving ¥3,000,000 a
    year recovers its cost in 20 months — comfortably inside the stated horizon
    — and was reported ``not_viable``, on a document that printed "Payback
    Period: 20.0 months" and "Positive Return: NO" three lines apart.

    The first-year figure is not discarded; it is reported on its own as
    ``first_year_net_positive`` so a reviewer who wants that stricter test still
    has it, and neither signal now hides the other.
    """
    net_annual_benefit_jpy = estimated_annual_savings_jpy - estimated_cost_jpy

    if estimated_cost_jpy > 0:
        roi_percent = round(net_annual_benefit_jpy / estimated_cost_jpy * 100.0, 1)
    else:
        # No cost recorded — treat as immediately positive if any savings exist.
        roi_percent = float("inf") if estimated_annual_savings_jpy > 0 else 0.0

    if estimated_annual_savings_jpy > 0:
        payback_period_months = round(estimated_cost_jpy / (estimated_annual_savings_jpy / 12.0), 1)
    else:
        payback_period_months = float("inf")

    roi_positive = estimated_annual_savings_jpy > 0 and payback_period_months <= _PAYBACK_HORIZON_MONTHS

    if not roi_positive:
        roi_tier = "not_viable"
    elif payback_period_months <= 6:
        roi_tier = "excellent"
    elif payback_period_months <= 12:
        roi_tier = "strong"
    else:
        roi_tier = "acceptable"

    # JSON cannot represent inf — normalise for ADR-005 serialisation.
    if payback_period_months == float("inf"):
        payback_period_months = -1.0
    if roi_percent == float("inf"):
        roi_percent = -1.0

    return {
        "roi_percent": roi_percent,
        "payback_period_months": payback_period_months,
        "net_annual_benefit_jpy": net_annual_benefit_jpy,
        "first_year_net_positive": net_annual_benefit_jpy > 0,
        "estimated_cost_jpy": estimated_cost_jpy,
        "estimated_annual_savings_jpy": estimated_annual_savings_jpy,
        "roi_positive": roi_positive,
        "roi_tier": roi_tier,
        "payback_horizon_months": _PAYBACK_HORIZON_MONTHS,
        "basis": (
            "roi_percent = (annual_savings - cost) / cost * 100; "
            "payback_period_months = cost / (annual_savings / 12); "
            f"positive iff annual_savings > 0 and payback <= {_PAYBACK_HORIZON_MONTHS} months; "
            "first_year_net_positive iff annual_savings > cost "
            "(-1 denotes not computable / undefined)"
        ),
    }


class CalculateRoiNode(FunctionNode):
    """ROI computation for MFG-C2-054.

    Determines the return-on-investment metrics and positive-return verdict,
    producing a roi_analysis dict with full traceability data for inclusion
    in the Kaizen document's ROI section.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        proposal_data: str  — JSON-serialised enriched proposal payload (ADR-005)

    Output state keys (partial dict):
        roi_analysis: str   — JSON-serialised ROI result dict (ADR-005)
        roi_positive: bool  — True if the proposal clears the ROI threshold
        status:       str
        error_log:    list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        proposal_data: Dict[str, Any] = from_json(state.get("proposal_data"), {})

        if not proposal_data:
            logger.error("CalculateRoiNode: proposal_data missing in state")
            emit_trace_event(
                "calculate_roi_failed",
                {"reason": "missing_proposal_data"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["CalculateRoiNode: proposal_data missing in state"],
            }

        proposal_id = proposal_data.get("proposal_id", "unknown")
        estimated_cost_jpy = int(proposal_data.get("estimated_cost_jpy", 0))
        estimated_annual_savings_jpy = int(proposal_data.get("estimated_annual_savings_jpy", 0))

        # ── ROI evaluation ────────────────────────────────────────────────────
        roi_result = _determine_roi(estimated_cost_jpy, estimated_annual_savings_jpy)
        roi_positive = bool(roi_result["roi_positive"])

        logger.info(
            "CalculateRoiNode: proposal_id=%s roi_percent=%s payback=%s roi_positive=%s",
            proposal_id,
            roi_result["roi_percent"],
            roi_result["payback_period_months"],
            roi_positive,
        )
        emit_trace_event(
            "calculate_roi_complete",
            {
                "proposal_id": proposal_id,
                "roi_percent": roi_result["roi_percent"],
                "payback_period_months": roi_result["payback_period_months"],
                "roi_positive": roi_positive,
                "roi_tier": roi_result["roi_tier"],
            },
            state,
        )

        return {
            "roi_analysis": to_json(roi_result),
            "roi_positive": roi_positive,
            "status": AgentStatus.SUCCESS.value,
        }
