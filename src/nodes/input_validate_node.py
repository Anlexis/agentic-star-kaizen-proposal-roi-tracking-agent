"""AgentCore Platform v1.0"""

# MFG-C2-054 — InputValidateNode
# Inner domain node 1: domain-level validation of the Kaizen proposal payload.
#
# Distinct from PreProcessNode (trust + structural JSON check): this node
# applies domain-business rules — field types, category normalisation, bounded
# numeric parsing, and structural caps.
#
# Inner node — ANONYMOUS trust: the outer PreProcessNode (VERIFIED_EXTERNAL)
# already enforced trust, and the inner nodes must be ANONYMOUS so the caller's
# InvocationContext passes the GraphNode boundary without a second rejection.
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import json
import logging
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json
from src.security.screening import (
    MAX_SHORT_FIELD_CHARS,
    MAX_TEXT_FIELD_CHARS,
    ScreeningError,
    bounded_list,
    bounded_text,
    identifier_token,
    inert_identifier,
    jpy_amount,
    month_count,
    redacted_fields,
    single_line,
)

logger = logging.getLogger(__name__)

# Known Kaizen category aliases for normalisation (canonical labels).
_CATEGORY_ALIASES: Dict[str, str] = {
    "efficiency": "efficiency",
    "productivity": "efficiency",
    "cost": "cost_reduction",
    "cost_reduction": "cost_reduction",
    "quality": "quality",
    "safety": "safety",
    "5s": "5s",
    "ergonomics": "safety",
    "delivery": "delivery",
    "lead_time": "delivery",
}


def _normalise_category(raw: Any) -> str:
    """Map a raw category label to its canonical form.

    The label is first reduced to the inert identifier alphabet, so an
    unrecognised category cannot carry punctuation or layout into the rendered
    header row; an unmapped but well-formed label is kept as written.
    """
    token = inert_identifier(raw, default="uncategorized")
    return _CATEGORY_ALIASES.get(token, token)


class InputValidateNode(FunctionNode):
    """Domain validation of the Kaizen proposal payload for MFG-C2-054.

    Applies business-rule checks beyond the structural JSON check in
    PreProcessNode: field types, category normalisation, bounded numeric
    parsing of cost / savings / period fields, and structural caps.

    Every caller-supplied number goes through the finite+bounded parser and
    fails CLOSED.  The previous best-effort coercion returned 0 for anything it
    could not read, which reported a fabricated figure as the submitter's own —
    "NaN" savings became "0 JPY/yr" on a document that looks authoritative.

    Inner node — ANONYMOUS trust (see module docstring).

    Input state keys:
        validated_input: str  — normalised JSON string from PreProcessNode
                                Falls back to user_input for unit-test convenience.

    Output state keys (partial dict):
        proposal_data: str   — JSON-serialised normalised proposal payload
        status:        str
        error_log:     list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = state.get("validated_input") or state.get("user_input", "")

        def _reject(reason: str, message: str, **detail: Any) -> Dict[str, Any]:
            emit_trace_event("input_validate_failed", {"reason": reason, **detail}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputValidateNode: {message}"],
            }

        # ── Parse ─────────────────────────────────────────────────────────────
        try:
            payload: Dict[str, Any] = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("InputValidateNode: JSON parse error — %s", exc)
            return _reject("json_parse_error", f"JSON parse error — {exc}")

        if not isinstance(payload, dict):
            return _reject("payload_not_dict", "payload is not a JSON object")

        # ── Validate, bound and normalise ─────────────────────────────────────
        # One try/except: every ScreeningError already names its own field and
        # never carries the offending value.
        try:
            proposal_id = identifier_token(payload.get("proposal_id"), field="proposal_id")
            title = single_line(payload.get("title"), field="title", limit=MAX_SHORT_FIELD_CHARS)
            current_state = bounded_text(
                payload.get("current_state"),
                field="current_state",
                limit=MAX_TEXT_FIELD_CHARS,
            )
            proposed_improvement = bounded_text(
                payload.get("proposed_improvement"),
                field="proposed_improvement",
                limit=MAX_TEXT_FIELD_CHARS,
            )
            submitter = single_line(payload.get("submitter"), field="submitter", limit=MAX_SHORT_FIELD_CHARS)
            department = single_line(
                payload.get("department"),
                field="department",
                limit=MAX_SHORT_FIELD_CHARS,
            )
            affected_processes = bounded_list(payload.get("affected_processes"), field="affected_processes")
            estimated_cost_jpy = jpy_amount(payload.get("estimated_cost_jpy"), field="estimated_cost_jpy")
            estimated_annual_savings_jpy = jpy_amount(
                payload.get("estimated_annual_savings_jpy"),
                field="estimated_annual_savings_jpy",
            )
            implementation_period_months = month_count(
                payload.get("implementation_period_months"),
                field="implementation_period_months",
            )
        except ScreeningError as exc:
            logger.warning("InputValidateNode: %s", exc)
            return _reject("field_validation_failed", str(exc), field=exc.field)

        if not (title and current_state and proposed_improvement):
            return _reject(
                "empty_narrative_fields",
                "title / current_state / proposed_improvement must all be non-empty",
            )

        category = _normalise_category(payload.get("category"))

        # ── Record what the platform privacy filter removed ───────────────────
        # The filter replaces detected personal-data spans with a sentinel BEFORE
        # this node runs, so a redacted field arrives as an ordinary string.
        # Recording the field names here is what lets the document render the
        # redaction honestly instead of certifying the sentinel as content.
        redacted = redacted_fields(
            {
                "proposal_id": proposal_id,
                "title": title,
                "submitter": submitter,
                "department": department,
                "current_state": current_state,
                "proposed_improvement": proposed_improvement,
                "affected_processes": affected_processes,
            }
        )

        proposal_data: Dict[str, Any] = {
            "proposal_id": proposal_id,
            "title": title,
            "submitter": submitter or "unknown",
            "department": department or "unknown",
            "category": category,
            "current_state": current_state,
            "proposed_improvement": proposed_improvement,
            "affected_processes": affected_processes,
            "estimated_cost_jpy": estimated_cost_jpy,
            "estimated_annual_savings_jpy": estimated_annual_savings_jpy,
            "implementation_period_months": implementation_period_months,
            "redacted_fields": redacted,
        }

        logger.info(
            "InputValidateNode: proposal_id=%s category=%s cost=%d savings=%d redacted=%d",
            proposal_id,
            category,
            estimated_cost_jpy,
            estimated_annual_savings_jpy,
            len(redacted),
        )
        emit_trace_event(
            "input_validate_complete",
            {
                "proposal_id": proposal_id,
                "category": category,
                "estimated_cost_jpy": estimated_cost_jpy,
                "estimated_annual_savings_jpy": estimated_annual_savings_jpy,
                "redacted_fields": redacted,
            },
            state,
        )

        return {
            "proposal_data": to_json(proposal_data),
            "status": AgentStatus.SUCCESS.value,
        }
