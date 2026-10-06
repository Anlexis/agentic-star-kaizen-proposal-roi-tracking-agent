# MFG-C2-054 — Integration regression (SR #12 re-review 2026-07)
#
# The compiled OUTER graph must (1) surface the digitized Kaizen document result
# on a successful invoke, and (2) fail-closed on the S-3 output-gate path — the
# structured domain fields are withheld and only the sanitised POST-gate values
# are returned.
#
# Root cause fixed: KaizenProposalDigitizationROITrackingAgent inherited
# AgentBaseGraph.get_output(), which surfaces only the {output, status, ...}
# envelope — so a successful agent.invoke() dropped the structured domain result
# (formatted_output / result / kaizen_document / proposal_sections /
# roi_analysis / roi_positive) that KaizenDocumentGraphNode.merge_output() and
# PostProcessNode populate on the internal state. The existing ["output"]-only
# PB-6 test passed and never exercised the named domain keys, so the defect went
# unnoticed.

import json

from framework.schemas.agent_status import AgentStatus


# A SUCCESS-yielding, positive-ROI Kaizen proposal (matches the PB-6 payload):
# net benefit 3,600,000 JPY/yr, payback 3.0 months (<= 24) => roi_positive True.
_PAYLOAD_DICT = {
    "proposal_id": "MFG-KAIZEN-20260712-001",
    "title": "Reduce die-changeover time on the Line 3 stamping press",
    "submitter": "T. Sato",
    "department": "Press Shop",
    "category": "efficiency",
    "current_state": (
        "Die changeover on the Line 3 stamping press takes 48 minutes on average. "
        "Operators fetch dies from the central rack and align them manually."
    ),
    "proposed_improvement": (
        "Adopt an SMED kit: pre-stage the next die on a rolling cart with "
        "quick-clamp fixtures so changeover drops to under 15 minutes."
    ),
    "affected_processes": [
        "die changeover",
        "press setup",
        "first-article inspection",
    ],
    "estimated_cost_jpy": 1200000,
    "estimated_annual_savings_jpy": 4800000,
    "implementation_period_months": 3,
}

_VALID_PAYLOAD = json.dumps(_PAYLOAD_DICT)


def _patch_domain_emit(monkeypatch):
    """Patch emit_trace_event in every node module (avoids audit-backend calls)."""
    for mod_suffix in (
        "pre_process_node",
        "input_validate_node",
        "parse_proposal_node",
        "generate_proposal_sections_node",
        "calculate_roi_node",
        "output_format_node",
        "post_process_node",
    ):
        try:
            monkeypatch.setattr(
                f"src.nodes.{mod_suffix}.emit_trace_event",
                lambda *a, **k: None,
            )
        except AttributeError:
            pass  # module not imported / no emit symbol; fine


def _invoke(monkeypatch, payload):
    """Compile and invoke the OUTER graph as a real VERIFIED_EXTERNAL caller."""
    _patch_domain_emit(monkeypatch)
    from framework.schemas.invocation_context import InvocationContext, TrustLevel
    from src.graph.graph import Graph

    agent = Graph()
    agent.compile()
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return agent.invoke(payload, ctx=ctx)


class TestOuterInvokeReturnsDomainResult:
    """SR #12 re-review: get_output must surface the domain result and fail-closed."""

    def test_success_invoke_surfaces_domain_result(self, monkeypatch):
        result = _invoke(monkeypatch, _VALID_PAYLOAD)

        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"expected SUCCESS, got {result.get('status')!r}; " f"error_log={result.get('error_log')}"
        )

        # The regression: these named keys were ALL None before get_output().
        formatted_output = result.get("formatted_output")
        kaizen_document = result.get("kaizen_document")
        assert formatted_output is not None, "formatted_output must be surfaced on a successful invoke"
        assert kaizen_document is not None, "kaizen_document must be surfaced on a successful invoke"
        assert result.get("result") is not None, "result must be surfaced on a successful invoke"
        assert result.get("roi_positive") is True, "roi_positive must be surfaced (positive-ROI proposal)"
        assert result.get("proposal_sections") is not None, "proposal_sections must be surfaced"
        assert result.get("roi_analysis") is not None, "roi_analysis must be surfaced"

        # The surfaced document must actually contain the digitized Kaizen record.
        for needle in ("KAIZEN PROPOSAL", "DIGITIZED RECORD", "MFG-KAIZEN-20260712-001"):
            assert needle in formatted_output, f"{needle!r} missing from formatted_output"
            assert needle in kaizen_document, f"{needle!r} missing from kaizen_document"

        # The framework envelope is preserved (backward compatible).
        assert result.get("output") is not None
        assert "KAIZEN PROPOSAL" in result["output"]

    def test_get_output_withholds_structured_fields_on_s3_block(self):
        """S-3 fail-closed: get_output() withholds the structured domain fields on
        a non-SUCCESS (S-3-blocked) state and surfaces only the POST-gate sanitised
        values — never the pre-gate raw kaizen_document.

        Driven directly on the compiled agent's get_output() with the exact state
        PostProcessNode emits on an S-3 credential block. This proves the
        fail-closed contract deterministically, without relying on a credential
        threading through the full .invoke() input path: the framework's S-2 input
        handling can mask/reject a credential in the request BEFORE it ever reaches
        the output gate, which makes an end-to-end credential test environment-
        dependent (real-SDK vs the CI stub package divergence).
        """
        secret = "sk-abcdefghij0123456789ABCDEF"
        sanitised = (
            "[KAIZEN DOCUMENT REDACTED: output contained a disallowed pattern "
            "(api_key_pattern). Contact the Kaizen office for the original document.]"
        )
        # State as it stands AFTER PostProcessNode blocks the S-3 violation:
        # merge_output() already placed the pre-gate raw domain fields (carrying the
        # secret) on state; PostProcessNode then set status=ERROR and replaced the
        # caller-facing formatted_output / result / output with the sanitised stub.
        blocked_state = {
            "status": AgentStatus.ERROR.value,
            "output": sanitised,
            "formatted_output": sanitised,
            "result": sanitised,
            # Pre-gate raw domain result still on state — MUST be withheld.
            "kaizen_document": f"KAIZEN PROPOSAL — DIGITIZED RECORD\n{secret}",
            "proposal_sections": json.dumps({"proposed_improvement": secret}),
            "roi_analysis": json.dumps({"roi_positive": True, "roi_tier": "excellent"}),
            "roi_positive": True,
            "error_log": ["PostProcessNode: S-3 credential pattern detected — api_key_pattern"],
            "node_history": [
                "InitializeNode",
                "PreProcessNode",
                "KaizenDocumentGraphNode",
                "PostProcessNode",
                "FinalizeNode",
            ],
            "correlation_id": "sr12-s3-block",
            "trace_id": "sr12-s3-block",
        }

        from src.graph.graph import Graph

        agent = Graph()
        agent.compile()
        result = agent.get_output(blocked_state)

        # Structured domain fields are withheld on the non-SUCCESS path (fail-closed).
        assert result.get("kaizen_document") is None
        assert result.get("proposal_sections") is None
        assert result.get("roi_analysis") is None
        assert result.get("roi_positive") is None
        # Only the POST-gate sanitised values are surfaced.
        assert result.get("formatted_output") == sanitised
        assert result.get("result") == sanitised
        assert "REDACTED" in (result.get("formatted_output") or "")
        # The raw secret never leaks through any surfaced field.
        for surfaced in (result.get("formatted_output"), result.get("result"), result.get("output")):
            assert secret not in (surfaced or "")
