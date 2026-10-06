"""AgentCore Platform v1.0"""

# MFG-C2-054 — PostProcessNode
# Outer backbone post_process slot: S-3 credential scan + expose final
# Kaizen document as formatted_output and result.
#
# S-3 responsibility: scan the kaizen_document string for credential-like
# patterns (API keys, JWTs, Bearer tokens, password assignments, AWS key ids
# and database connection strings) before returning the result to the caller.
# On a violation, return a sanitised stub for BOTH formatted_output and result,
# CLEAR the pre-gate output-bearing state fields, and set status=ERROR.
#
# The domain S-3 gate is a MODULE-LEVEL function (_security_gate_output)
# called from inside execute() — NOT an instance method on the node class
# (which would be auto-wrapped by the real SDK and cause AttributeError on
# the real invoke path; see CoE finding a peer template / a peer template).  The agent
# class in graph.py exposes a `_security_gate_output` declaration that
# delegates to this same module-level scanner (S-3 gate discoverable there).
#
# Returns ONLY the state keys this node writes (partial-dict contract).

import logging
from typing import Any, ClassVar, Dict, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.security.screening import screen_credentials_text

logger = logging.getLogger(__name__)


def _security_gate_output(content: str) -> Optional[str]:
    """Scan the assembled document for disallowed credential/secret patterns.

    Returns the first violation name, or None if the output is clean.

    Module-level function (not a node instance method) — this form is required
    by CoE §5-6: an instance method of this name is intercepted by the
    framework's @final gate machinery and fails at class definition time.

    The pattern set is the UNION of this template's own patterns and the
    framework's ``detect_credentials`` (see src/security/screening.py). Both
    halves are load-bearing and neither may be dropped in favour of the other:
      * the template's ``credential_assignment`` catches ``password=...``, a
        shape the framework's format-based patterns do not describe at all;
      * the framework catches ``sk_live_``, ``AKIA...`` and database connection
        strings, which the template's prefixes miss — and because the framework
        runs its own scan over every value this node returns, a shape it catches
        and this gate misses raises from inside the framework wrapper, which
        discards the redaction prepared below and hands the caller an opaque
        error instead of a named one.
    """
    return screen_credentials_text(content or "")


class PostProcessNode(FunctionNode):
    """Apply S-3 output gate and expose the final Kaizen document.

    Outer backbone post_process slot.  Declared ANONYMOUS — trust was
    already enforced at PreProcessNode (VERIFIED_EXTERNAL).

    Input state keys:
        kaizen_document: str   — formatted document from inner OutputFormatNode
        roi_positive:    bool  — positive-ROI flag

    Output state keys (partial dict):
        formatted_output: str
        result:           str
        status:           str
        error_log:        list[str]  (only on ERROR)
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        kaizen_document: str = state.get("kaizen_document") or ""
        roi_positive: bool = bool(state.get("roi_positive"))

        # ── Fallback for empty document ───────────────────────────────────────
        if not kaizen_document.strip():
            logger.warning("PostProcessNode: kaizen_document is empty — using fallback message")
            kaizen_document = (
                "[Kaizen Proposal Document] No document content generated. " "Check error_log for upstream failures."
            )

        # ── S-3 domain output gate ────────────────────────────────────────────
        violation = _security_gate_output(kaizen_document)
        if violation:
            logger.error("PostProcessNode: S-3 violation detected in output — %s", violation)
            emit_trace_event(
                "post_process_s3_violation",
                {"violation": violation},
                state,
            )
            sanitised = (
                f"[KAIZEN DOCUMENT REDACTED: output contained a disallowed pattern "
                f"({violation}). Contact the Kaizen office for the original document.]"
            )
            # Containment: the replacement is truthy AND the output-bearing
            # state fields are cleared in the same partial dict.
            #
            # A truthy replacement matters because the framework envelope falls
            # back to state["result"] whenever formatted_output is falsy — an
            # empty string would re-open the very channel this gate closes.
            #
            # Clearing matters because returning a redacted document while
            # leaving the raw one on state only relocates the leak: the pre-gate
            # kaizen_document / proposal_sections / roi_analysis are still
            # checkpointed and still visible to anything that reads state
            # afterwards. The graph's get_output() also withholds them on a
            # non-success status; that is a second, independent layer, and each
            # is asserted at its own boundary rather than through the other.
            return {
                "formatted_output": sanitised,
                "result": sanitised,
                "kaizen_document": None,
                "proposal_sections": None,
                "roi_analysis": None,
                "roi_positive": None,
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PostProcessNode: S-3 credential pattern detected — {violation}"],
            }

        logger.info(
            "PostProcessNode: output gate passed — length=%d roi_positive=%s",
            len(kaizen_document),
            roi_positive,
        )
        emit_trace_event(
            "post_process_complete",
            {
                "output_length": len(kaizen_document),
                "roi_positive": roi_positive,
            },
            state,
        )

        return {
            "formatted_output": kaizen_document,
            "result": kaizen_document,
            "status": AgentStatus.SUCCESS.value,
        }
