"""AgentCore Platform v1.0"""

# MFG-C2-054 — GenerateProposalSectionsNode
# Inner domain node 3: generate the digitized Kaizen document sections.
#
# v1 implementation: DETERMINISTIC template-based generation — NO LLM is invoked.
# The section text is synthesised from the proposal_data fields using the fixed
# templates below (there is no LLM client in SDK v1).
#
# Declared runtime config is reachable, not inert (SR #12 Medium 2026-07):
# config/config.yaml llm.system_prompt_template is forwarded by the
# outer graph (graph.py _parent_config) and seeded into the inner state by
# DomainWorkflowGraph._extra_initial_state(), so this node observes the declared
# prompt template end-to-end — on the real .invoke() path (via state) and on
# direct unit calls (via config). It is recorded in the trace event as the
# documented production LLM wiring point; v1 never fakes an LLM call.
#
# Sections produced:
#   1. proposal_overview
#   2. background_current_state
#   3. proposed_improvement
#   4. expected_effects
#   5. implementation_plan
#   6. cost_and_roi_summary
#
# Inner node — ANONYMOUS trust (review finding 5, corrected 2026-07-02).
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.security.screening import is_redacted

logger = logging.getLogger(__name__)

# Rendered in place of any value the platform privacy filter replaced.
#
# The filter substitutes its sentinel into the request before this template sees
# it, so a redacted field arrives as an ordinary string and would otherwise be
# typeset as though the submitter had written it — a document reporting
# "Submitter: [MASKED]" states something false about the proposal. Naming the
# redaction is both accurate and actionable: it tells the reader the value was
# removed in transit rather than left blank by the author.
_REDACTED_NOTICE = "(redacted by the platform privacy filter)"


def _render(d: Dict[str, Any], key: str, fallback: str) -> str:
    """Render one proposal field, distinguishing redaction from absence."""
    value = d.get(key)
    if is_redacted(value):
        return _REDACTED_NOTICE
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _section_overview(d: Dict[str, Any]) -> str:
    """Generate the Proposal Overview section."""
    lines = [
        f"Proposal ID:  {_render(d, 'proposal_id', 'N/A')}",
        f"Title:        {_render(d, 'title', 'N/A')}",
        f"Submitter:    {_render(d, 'submitter', 'unknown')}",
        f"Department:   {_render(d, 'department', 'unknown')}",
        f"Category:     {_render(d, 'category', 'uncategorized')}",
        f"Impact Tier:  {str(d.get('impact_tier', 'minor')).upper()}",
    ]
    redacted: List[str] = list(d.get("redacted_fields") or [])
    if redacted:
        lines.append(
            "Redacted:     " + ", ".join(redacted) + "  — removed by the platform privacy filter before digitization; "
            "the original text is not available to this agent."
        )
    return "\n".join(lines)


def _section_background(d: Dict[str, Any]) -> str:
    """Generate the Background / Current State section."""
    current = _render(d, "current_state", "Current state not described.")
    processes: List[str] = d.get("affected_processes", [])
    proc_line = ", ".join(processes) if processes else "N/A"
    return f"Affected Processes: {proc_line}\n\n{current}"


def _section_proposed(d: Dict[str, Any]) -> str:
    """Generate the Proposed Improvement section."""
    return _render(d, "proposed_improvement", "Proposed improvement not described.")


def _section_expected_effects(d: Dict[str, Any]) -> str:
    """Generate the Expected Effects section."""
    savings = int(d.get("estimated_annual_savings_jpy", 0))
    net = int(d.get("net_annual_benefit_jpy", 0))
    tier = str(d.get("impact_tier", "minor")).upper()
    lines = [
        f"  Estimated Annual Savings: {savings:,} JPY/yr",
        f"  Net Annual Benefit:       {net:,} JPY/yr",
        f"  Impact Tier:              {tier}",
    ]
    return "\n".join(lines)


def _section_implementation_plan(d: Dict[str, Any]) -> str:
    """Generate the Implementation Plan section."""
    months = int(d.get("implementation_period_months", 0))
    cost = int(d.get("estimated_cost_jpy", 0))
    period = f"{months} month(s)" if months else "to be scheduled"
    lines = [
        f"  Implementation Period: {period}",
        f"  Estimated Cost:        {cost:,} JPY (one-time)",
        "  Steps: 1) approve proposal  2) procure/prepare  "
        "3) pilot on affected processes  4) roll out  5) verify effects",
    ]
    return "\n".join(lines)


def _section_cost_roi_summary(d: Dict[str, Any]) -> str:
    """Generate the Cost & ROI Summary placeholder section.

    The precise ROI figures are computed downstream by CalculateRoiNode and
    appended by OutputFormatNode; this section frames the cost/savings inputs.
    """
    cost = int(d.get("estimated_cost_jpy", 0))
    savings = int(d.get("estimated_annual_savings_jpy", 0))
    lines = [
        f"  One-time Cost:            {cost:,} JPY",
        f"  Estimated Annual Savings: {savings:,} JPY/yr",
        "  ROI %, payback period, and positive-return verdict are computed in " "the ROI Analysis section below.",
    ]
    return "\n".join(lines)


class GenerateProposalSectionsNode(FunctionNode):
    """Generate the 6 digitized Kaizen document sections.

    v1: DETERMINISTIC template-based synthesis — no LLM is invoked.
    The declared LLM prompt template (config/config.yaml
    llm.system_prompt_template) is forwarded end-to-end and read here as the
    documented production LLM wiring point; it is never faked.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        proposal_data:          str  — JSON-serialised enriched proposal payload (ADR-005)
        system_prompt_template: str  — declared prompt path (manifest-forwarded; optional)

    Output state keys (partial dict):
        proposal_sections: str  — JSON-serialised section dict (ADR-005)
        status:            str
        error_log:         list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        proposal_data: Dict[str, Any] = from_json(state.get("proposal_data"), {})

        # Declared LLM prompt template — reachable end-to-end (SR #12 Medium):
        # seeded into state by DomainWorkflowGraph._extra_initial_state() on the
        # real .invoke() path; also available via config on direct unit calls.
        # v1 generation is deterministic (templates below); the declared template
        # is recorded as the production LLM wiring point but never invoked/faked.
        configurable = (config or {}).get("configurable", {})
        system_prompt_template = state.get("system_prompt_template") or configurable.get("system_prompt_template") or ""

        if not proposal_data:
            logger.error("GenerateProposalSectionsNode: proposal_data missing in state")
            emit_trace_event(
                "generate_proposal_sections_failed",
                {"reason": "missing_proposal_data"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["GenerateProposalSectionsNode: proposal_data missing in state"],
            }

        proposal_id = proposal_data.get("proposal_id", "unknown")

        # ── Generate all sections ─────────────────────────────────────────────
        sections: Dict[str, str] = {
            "proposal_overview": _section_overview(proposal_data),
            "background_current_state": _section_background(proposal_data),
            "proposed_improvement": _section_proposed(proposal_data),
            "expected_effects": _section_expected_effects(proposal_data),
            "implementation_plan": _section_implementation_plan(proposal_data),
            "cost_and_roi_summary": _section_cost_roi_summary(proposal_data),
        }

        logger.info(
            "GenerateProposalSectionsNode: proposal_id=%s sections=%d",
            proposal_id,
            len(sections),
        )
        emit_trace_event(
            "generate_proposal_sections_complete",
            {
                "proposal_id": proposal_id,
                "section_count": len(sections),
                "section_keys": sorted(sections.keys()),
                "generation_mode": "deterministic_template_v1",
                "declared_prompt_template": system_prompt_template or None,
            },
            state,
        )

        return {
            "proposal_sections": to_json(sections),
            "status": AgentStatus.SUCCESS.value,
        }
