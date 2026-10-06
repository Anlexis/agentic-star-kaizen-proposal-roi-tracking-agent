# PB-6: Invoke Execution Order Verification
# Verifies BaseNode.__call__() enforces: S-1 trust gate -> S-4 node_start ->
# S-2 _security_gate_input() -> execute() -> S-3 _security_gate_output() ->
# S-4 node_complete, for every concrete node under src/nodes/.
#
# Also verifies the full backbone invoke order for the outer
# KaizenProposalDigitizationROITrackingAgent (Cat 2 two-layer nested graph):
#   InitializeNode -> PreProcessNode (pre_process) -> KaizenDocumentGraphNode (main)
#   -> PostProcessNode (post_process) -> FinalizeNode
#
# PB-6 invoke uses VERIFIED_EXTERNAL caller trust (the real external path) — NEVER
# for_internal(). A VERIFIED_EXTERNAL InvocationContext exercises the same code path a
# real STG caller uses: it clears the outer PreProcessNode S-1 gate
# (required_trust_level = VERIFIED_EXTERNAL) AND passes through the inner ANONYMOUS
# domain nodes. for_internal() (INTERNAL) would not represent a real external caller,
# so it is deliberately not used.

import importlib
import inspect
import json
import pkgutil
from pathlib import Path

import pytest

# ── Template-specific constants ───────────────────────────────────────────────

# Class name of the node in the `main` backbone slot (Cat 2 GraphNode).
_MAIN_SLOT_NODE = "KaizenDocumentGraphNode"

# A SUCCESS-yielding, positive-ROI Kaizen proposal payload for the backbone invoke
# test. All PreProcessNode + InputValidateNode required fields present (proposal_id,
# title, current_state, proposed_improvement). ROI: net benefit 3,600,000 JPY/yr,
# payback 3.0 months (<= 24) => roi_positive = True.
#
# CONTRACT (reference_newgen_stg_deploy): deploy/invoke_payload.json["input"] MUST
# equal this exact string — the Stage-5 deploy-stg evidence invoke and the PB-6 test
# must exercise the identical payload. test_invoke_payload_matches_pb6 below asserts
# that equality so the two can never drift. ASCII-only so json.dumps stays byte-
# identical between here and the deploy file.
_PAYLOAD_DICT = {
    "proposal_id": "MFG-KAIZEN-20260712-001",
    "title": "Reduce die-changeover time on the Line 3 stamping press",
    "submitter": "T. Sato",
    "department": "Press Shop",
    "category": "efficiency",
    "current_state": (
        "Die changeover on the Line 3 stamping press takes 48 minutes on average. "
        "Operators fetch dies from the central rack and align them manually, which "
        "stops the press and idles two downstream cells."
    ),
    "proposed_improvement": (
        "Adopt an SMED (single-minute exchange of die) kit: pre-stage the next die on "
        "a rolling cart with quick-clamp fixtures and a shadow board, so changeover "
        "drops to under 15 minutes and can run while the press finishes the prior lot."
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

# ─────────────────────────────────────────────────────────────────────────────


def _discover_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete BaseNode subclasses."""
    from framework.nodes.base_node import BaseNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


def _patch_domain_emit(monkeypatch):
    """Patch emit_trace_event in every domain node module (avoids audit-backend calls)."""
    for mod_suffix in (
        "pre_process_node",
        "input_validate_node",
        "parse_proposal_node",
        "generate_proposal_sections_node",
        "calculate_roi_node",
        "output_format_node",
        "post_process_node",
        "main_node",
    ):
        try:
            monkeypatch.setattr(
                f"src.nodes.{mod_suffix}.emit_trace_event",
                lambda *a, **k: None,
            )
        except AttributeError:
            pass  # module not yet imported / no emit symbol; fine


class TestInvokeOrder:
    """PB-6: __call__ must run S-1 -> node_start -> S-2 -> execute() -> S-3 -> node_complete."""

    def test_call_order_for_every_node(self, monkeypatch):
        node_classes = _discover_node_classes()
        if not node_classes:
            pytest.skip("no concrete BaseNode subclasses found under src/nodes/")

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            # caller trust == the node's required level so the S-1 gate always passes here;
            # the gate-denial branch is asserted separately in TestS1TrustGate.
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n" f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)


class TestS1TrustGate:
    """PB-6 S-1: the trust gate in BaseNode.__call__ runs BEFORE execute() and denies
    a caller whose trust is below the node's required_trust_level."""

    def test_pre_process_denies_anonymous_caller(self, monkeypatch):
        """PreProcessNode (required VERIFIED_EXTERNAL) must refuse an ANONYMOUS caller."""
        _patch_domain_emit(monkeypatch)
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.trust_level import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(
            {
                "caller_trust_level": TrustLevel.ANONYMOUS.value,
                "user_input": _VALID_PAYLOAD,
                "correlation_id": "pb6-s1-denial",
            }
        )
        assert result["status"] == AgentStatus.ERROR.value
        assert any(
            "trust gate" in e.lower() for e in result.get("error_log", [])
        ), f"expected an S-1 trust-gate denial, got error_log={result.get('error_log')}"

    def test_pre_process_admits_verified_external_caller(self, monkeypatch):
        """The same node admits a VERIFIED_EXTERNAL caller and runs execute() to SUCCESS."""
        _patch_domain_emit(monkeypatch)
        from framework.schemas.agent_status import AgentStatus
        from framework.schemas.trust_level import TrustLevel
        from src.nodes.pre_process_node import PreProcessNode

        node = PreProcessNode()
        result = node(
            {
                "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
                "user_input": _VALID_PAYLOAD,
                "input_context": {},
                "correlation_id": "pb6-s1-admit",
            }
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None


class TestBackboneInvokeOrder:
    """PB-6 backbone: a full Graph().invoke() runs the 5-node backbone in order.

    Backbone order: InitializeNode -> PreProcessNode (pre_process) ->
                    KaizenDocumentGraphNode (main) ->
                    PostProcessNode (post_process) -> FinalizeNode

    Uses VERIFIED_EXTERNAL caller trust — the real external path.
    InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL) is mandatory;
    NEVER use for_internal(), which would not represent a real external caller.
    """

    def _invoke(self, monkeypatch):
        _patch_domain_emit(monkeypatch)
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.graph import KaizenProposalDigitizationROITrackingAgent

        agent = KaizenProposalDigitizationROITrackingAgent()
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return agent.invoke(_VALID_PAYLOAD, ctx=ctx)

    def test_backbone_invoke_succeeds_and_returns_output(self, monkeypatch):
        from framework.schemas.agent_status import AgentStatus

        result = self._invoke(monkeypatch)
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            f"Expected status={AgentStatus.SUCCESS.value!r}, got: {result.get('status')!r}\n"
            f"error_log: {result.get('error_log')}"
        )
        assert result.get("output") is not None, "output must be set after a successful invoke"
        # The assembled, digitized Kaizen document must be present in the surfaced output.
        assert "KAIZEN PROPOSAL" in result["output"]
        assert "DIGITIZED RECORD" in result["output"]
        assert "MFG-KAIZEN-20260712-001" in result["output"]

    def test_backbone_node_history_matches_expected_order(self, monkeypatch):
        result = self._invoke(monkeypatch)
        history = result.get("node_history", [])
        assert history == [
            "InitializeNode",
            "PreProcessNode",
            "KaizenDocumentGraphNode",
            "PostProcessNode",
            "FinalizeNode",
        ], f"unexpected backbone node_history: {history}"

    def test_main_slot_is_kaizen_document_graph_node(self):
        """The `main` backbone slot must be KaizenDocumentGraphNode (a GraphNode — Cat 2)."""
        from framework.nodes.graph_node import GraphNode
        from src.graph.graph import (
            KaizenDocumentGraphNode,
            KaizenProposalDigitizationROITrackingAgent,
        )

        agent = KaizenProposalDigitizationROITrackingAgent()
        agent.compile()
        main_node = agent._nodes.get("main")
        assert main_node is not None, "main slot must be registered"
        assert isinstance(
            main_node, KaizenDocumentGraphNode
        ), f"main slot must be KaizenDocumentGraphNode, got {type(main_node).__name__}"
        assert isinstance(main_node, GraphNode), "main slot node must subclass GraphNode (Cat 2 contract)"
        assert main_node.__class__.__name__ == _MAIN_SLOT_NODE

    def test_invoke_payload_matches_pb6(self):
        """deploy/invoke_payload.json["input"] MUST equal _VALID_PAYLOAD (Stage-5 alignment).

        The deploy-stg evidence invoke (stg_invoke_evidence.py POSTs invoke_payload.json
        as the request body) must exercise the same payload PB-6 asserts yields SUCCESS.
        """
        repo_root = Path(__file__).resolve().parents[2]
        payload_file = repo_root / "deploy" / "invoke_payload.json"
        assert payload_file.exists(), "deploy/invoke_payload.json is required for deploy-stg"
        body = json.loads(payload_file.read_text())
        assert body.get("input") == _VALID_PAYLOAD, (
            "deploy/invoke_payload.json['input'] must equal the PB-6 _VALID_PAYLOAD "
            "(reference_newgen_stg_deploy contract)"
        )
        # And the payload the STG server forwards to agent.invoke() must itself be a
        # valid, PreProcessNode-parseable Kaizen proposal JSON object.
        proposal = json.loads(body["input"])
        for required in ("proposal_id", "title", "current_state", "proposed_improvement"):
            assert required in proposal, f"invoke_payload input missing required field: {required}"
