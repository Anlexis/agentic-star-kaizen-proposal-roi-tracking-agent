"""AgentCore Platform v1.0"""

# MFG-C2-054 — PreProcessNode
# Outer backbone pre_process slot: trust gate + Kaizen input validation.
#
# Responsibilities:
#   - Enforce VERIFIED_EXTERNAL trust (required_trust_level)
#   - Reject empty / oversized / non-JSON input early (fail-fast)
#   - Confirm required Kaizen proposal fields are present
#   - Screen the parsed payload for injection and credential shapes, in the
#     node itself, so the check still applies where a platform gate does not
#   - Write validated_input (normalised JSON string) + enriched_context to State
#   - Emit an audit event for every validation decision
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
    MAX_PAYLOAD_CHARS,
    ScreeningError,
    identifier_token,
    inert_identifier,
    screen_credentials,
    screen_injection,
)

logger = logging.getLogger(__name__)

# Required top-level keys for a valid Kaizen proposal payload.
# proposal_id is the mandatory identifier; title / current_state /
# proposed_improvement are the minimum needed to digitize the proposal.
_REQUIRED_PROPOSAL_KEYS = frozenset(
    {
        "proposal_id",
        "title",
        "current_state",
        "proposed_improvement",
    }
)


class PreProcessNode(FunctionNode):
    """Caller-facing input validation for MFG-C2-054.

    Validates the caller-supplied Kaizen proposal payload before the domain
    workflow runs.  This is the outer backbone's pre_process slot — the only
    node with VERIFIED_EXTERNAL trust, so unauthenticated or anonymous callers
    are rejected here (fail-fast; inner domain nodes carry ANONYMOUS trust and
    never see untrusted input directly).

    The injection and credential screens below are deliberately owned by this
    node rather than left to the platform.  A platform gate that is absent,
    disabled, or bypassed by a direct execute() call would otherwise leave the
    pipeline fail-OPEN, and the credential case additionally matters for the
    caller's sake: a credential-shaped string that reaches document assembly
    trips the framework's own output gate deep inside the pipeline and returns
    an opaque error, whereas refusing it here names what was wrong.

    Input state keys:
        user_input: str  — caller-supplied JSON Kaizen proposal payload

    Output state keys (partial dict):
        validated_input:  str        — normalised JSON string (re-serialised)
        enriched_context: str        — JSON-serialised channel metadata
        status:           str        — AgentStatus.SUCCESS or ERROR
        error_log:        list[str]  — set only on ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})
        if not isinstance(input_context, dict):
            input_context = {}

        def _reject(reason: str, message: str, **detail: Any) -> Dict[str, Any]:
            """Reject the request without echoing any caller-supplied value."""
            emit_trace_event(
                "pre_process_validation_failed",
                {"reason": reason, **detail},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: {message}"],
            }

        # ── Emptiness check ───────────────────────────────────────────────────
        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            logger.warning("PreProcessNode: user_input is empty or missing")
            return _reject("empty_input", "user_input is empty or missing")

        # ── Size cap ──────────────────────────────────────────────────────────
        # A proposal is a human-authored form. Capping before json.loads keeps a
        # single oversized request from being parsed and rendered at all.
        if len(user_input) > MAX_PAYLOAD_CHARS:
            logger.warning(
                "PreProcessNode: payload rejected — %d chars exceeds the limit",
                len(user_input),
            )
            return _reject(
                "payload_too_large",
                f"payload exceeds the {MAX_PAYLOAD_CHARS:,}-character limit",
                payload_chars=len(user_input),
            )

        # ── JSON parse ────────────────────────────────────────────────────────
        try:
            payload: Dict[str, Any] = json.loads(user_input.strip())
        except (json.JSONDecodeError, ValueError) as exc:
            # str(exc) carries json's own position/context text, not the payload.
            logger.warning("PreProcessNode: JSON parse failed — %s", exc)
            return _reject("json_parse_error", f"invalid JSON — {exc}")

        if not isinstance(payload, dict):
            return _reject("payload_not_object", "JSON root must be an object")

        # ── Injection screen (template-owned) ─────────────────────────────────
        # Runs over keys as well as values, depth-first, raw and markup-stripped.
        injection = screen_injection(payload)
        if injection:
            logger.warning("PreProcessNode: injection screen tripped — %s", injection)
            return _reject(
                "injection_detected",
                f"request refused by the input screen ({injection})",
                violation=injection,
            )

        # ── Credential screen (template-owned, union with the framework's) ─────
        credential = screen_credentials(payload)
        if credential:
            logger.warning("PreProcessNode: credential screen tripped — %s", credential)
            return _reject(
                "credential_detected",
                "request refused: a field carries a credential-shaped value "
                f"({credential}). Remove it and resubmit.",
                violation=credential,
            )

        # ── Required field check ──────────────────────────────────────────────
        missing = _REQUIRED_PROPOSAL_KEYS - payload.keys()
        if missing:
            return _reject(
                "missing_required_fields",
                f"missing required fields: {sorted(missing)}",
                missing=sorted(missing),
            )

        # ── Identifier check ──────────────────────────────────────────────────
        # proposal_id renders into the document header and is recorded in audit
        # events, so it is validated against an inert alphabet — preserved
        # byte-for-byte when it conforms, refused when it does not.
        try:
            proposal_id = identifier_token(payload.get("proposal_id"), field="proposal_id")
        except ScreeningError as exc:
            return _reject("invalid_proposal_id", str(exc), field=exc.field)

        # ── Success ───────────────────────────────────────────────────────────
        normalised_json = json.dumps(payload, ensure_ascii=False)

        logger.info(
            "PreProcessNode: validated proposal_id=%s payload_keys=%d",
            proposal_id,
            len(payload),
        )
        emit_trace_event(
            "pre_process_validated",
            {
                "proposal_id": proposal_id,
                "payload_key_count": len(payload),
            },
            state,
        )

        return {
            "validated_input": normalised_json,
            "enriched_context": to_json(
                {
                    "source": "KaizenProposalDigitizationROITrackingAgent",
                    "channel": inert_identifier(input_context.get("channel"), default="unknown"),
                    "proposal_id": proposal_id,
                }
            ),
            "status": AgentStatus.SUCCESS.value,
        }
