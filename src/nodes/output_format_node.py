"""AgentCore Platform v1.0"""

# MFG-C2-054 — OutputFormatNode (inner domain node 5, last in DomainWorkflowGraph)
# Assembles the final digitized Kaizen document from proposal_sections and
# roi_analysis.  This is the last inner node — it produces the kaizen_document
# string that the outer PostProcessNode will S-3-gate.
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

from src.schemas.state import from_json

logger = logging.getLogger(__name__)

# Section order for the final digitized Kaizen document.
_SECTION_ORDER = [
    "proposal_overview",
    "background_current_state",
    "proposed_improvement",
    "expected_effects",
    "implementation_plan",
    "cost_and_roi_summary",
]

# Human-readable section headers.
_SECTION_HEADERS: Dict[str, str] = {
    "proposal_overview": "1. Proposal Overview",
    "background_current_state": "2. Background / Current State",
    "proposed_improvement": "3. Proposed Improvement",
    "expected_effects": "4. Expected Effects",
    "implementation_plan": "5. Implementation Plan",
    "cost_and_roi_summary": "6. Cost & ROI Summary",
}

_SEPARATOR = "=" * 72
_SUBSEP = "-" * 72


def _fmt_number(value: Any) -> str:
    """Render a possibly-sentinel numeric as a display string."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if num < 0:
        return "N/A"
    if num.is_integer():
        return f"{int(num):,}"
    return f"{num:,.1f}"


def _assemble_document(
    proposal_id: str,
    sections: Dict[str, str],
    roi: Dict[str, Any],
) -> str:
    """Assemble the full Kaizen document from sections and ROI data."""
    lines = [
        _SEPARATOR,
        "KAIZEN PROPOSAL — DIGITIZED RECORD",
        f"Proposal ID: {proposal_id}",
        _SEPARATOR,
        "",
    ]

    for key in _SECTION_ORDER:
        header = _SECTION_HEADERS.get(key, key.replace("_", " ").title())
        content = sections.get(key, "(Section not generated)")
        lines.append(header)
        lines.append(_SUBSEP)
        lines.append(content)
        lines.append("")

    # Append ROI analysis trailer.
    roi_positive = roi.get("roi_positive", False)
    roi_percent = roi.get("roi_percent", -1)
    payback = roi.get("payback_period_months", -1)
    net_benefit = roi.get("net_annual_benefit_jpy", 0)
    roi_tier = str(roi.get("roi_tier", "not_viable")).upper()
    horizon = roi.get("payback_horizon_months", 24)
    first_year_positive = roi.get("first_year_net_positive")
    lines += [
        _SEPARATOR,
        "7. ROI Analysis",
        _SUBSEP,
        f"  Net Annual Benefit:    {_fmt_number(net_benefit)} JPY/yr",
        f"  ROI:                   {_fmt_number(roi_percent)} %",
        f"  Payback Period:        {_fmt_number(payback)} months",
        f"  ROI Tier:              {roi_tier}",
        f"  Positive Return:       " f"{('YES — payback within %d months' % horizon) if roi_positive else 'NO'}",
        # Reported alongside the verdict rather than folded into it: a proposal
        # can recover its cost inside the horizon while still costing more in
        # year one than it saves, and a reviewer applying the stricter first-year
        # test needs to see that distinction rather than infer it.
        f"  First-Year Net:        " f"{'POSITIVE' if first_year_positive else 'NEGATIVE'}",
        _SEPARATOR,
    ]

    return "\n".join(lines)


class OutputFormatNode(FunctionNode):
    """Assemble the final digitized Kaizen document (inner domain node).

    Reads proposal_sections and roi_analysis from State, renders the full
    document text, and writes it to kaizen_document (and result) for the
    outer PostProcessNode.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        proposal_sections: str  — JSON-serialised section dict (ADR-005)
        roi_analysis:      str  — JSON-serialised ROI result (ADR-005)
        proposal_data:     str  — JSON-serialised proposal payload (ADR-005)

    Output state keys (partial dict):
        kaizen_document: str
        result:          str  (same as kaizen_document — backbone convention)
        status:          str
        error_log:       list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        sections: Dict[str, str] = from_json(state.get("proposal_sections"), {})
        roi: Dict[str, Any] = from_json(state.get("roi_analysis"), {})
        proposal_data: Dict[str, Any] = from_json(state.get("proposal_data"), {})

        proposal_id = proposal_data.get("proposal_id", "unknown")

        if not sections:
            logger.error(
                "OutputFormatNode: proposal_sections missing in state for proposal_id=%s",
                proposal_id,
            )
            emit_trace_event(
                "output_format_failed",
                {"reason": "missing_proposal_sections", "proposal_id": proposal_id},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"OutputFormatNode: proposal_sections missing for proposal_id={proposal_id}"],
            }

        # ── Assemble the document ─────────────────────────────────────────────
        document = _assemble_document(proposal_id, sections, roi)

        logger.info(
            "OutputFormatNode: proposal_id=%s document_chars=%d roi_positive=%s",
            proposal_id,
            len(document),
            roi.get("roi_positive", False),
        )
        emit_trace_event(
            "output_format_complete",
            {
                "proposal_id": proposal_id,
                "document_length": len(document),
                "section_count": len(sections),
                "roi_positive": roi.get("roi_positive", False),
            },
            state,
        )

        return {
            "kaizen_document": document,
            "result": document,
            "status": AgentStatus.SUCCESS.value,
        }
