# MFG-C2-054 — Unit Tests: domain nodes + graph wiring
#
# Real, non-stub unit tests. They import the REAL modules merged to develop in
# Wave-1 and assert real behaviour (digitized Kaizen document content, ROI
# thresholds, impact-tier classification, S-1 trust levels, the S-3 output gate,
# and the Cat 2 two-layer nested graph composition).
#
# S-4 audit events are patched at the node MODULE level (not via a sys.modules
# stub, which would break the real `shared` package the framework loads at import
# time). Patch pattern per node:
#     monkeypatch.setattr("src.nodes.<mod>.emit_trace_event", lambda *a, **k: None)

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.schemas.state import from_json, to_json


# ── Shared fixtures / helpers ─────────────────────────────────────────────────


def _proposal_payload(**overrides) -> dict:
    """A complete, valid raw Kaizen proposal payload (as a caller would POST)."""
    payload = {
        "proposal_id": "MFG-KAIZEN-20260712-001",
        "title": "Reduce die-changeover time on the Line 3 stamping press",
        "submitter": "T. Sato",
        "department": "Press Shop",
        "category": "efficiency",
        "current_state": ("Die changeover on the Line 3 stamping press takes 48 minutes on average."),
        "proposed_improvement": ("Adopt an SMED kit with a rolling die cart and quick-clamp fixtures."),
        "affected_processes": ["die changeover", "press setup", "first-article inspection"],
        "estimated_cost_jpy": 1200000,
        "estimated_annual_savings_jpy": 4800000,
        "implementation_period_months": 3,
    }
    payload.update(overrides)
    return payload


VALID_PAYLOAD = json.dumps(_proposal_payload())


def _proposal_data(cost: int = 1200000, savings: int = 4800000, **overrides) -> dict:
    """The normalised proposal_data dict shape produced by InputValidateNode
    (i.e. the input the downstream inner nodes consume)."""
    data = {
        "proposal_id": "MFG-KAIZEN-20260712-001",
        "title": "Reduce die-changeover time on the Line 3 stamping press",
        "submitter": "T. Sato",
        "department": "Press Shop",
        "category": "efficiency",
        "current_state": "Die changeover takes 48 minutes on average.",
        "proposed_improvement": "Adopt an SMED kit with a rolling die cart.",
        "affected_processes": ["die changeover", "press setup"],
        "estimated_cost_jpy": cost,
        "estimated_annual_savings_jpy": savings,
        "implementation_period_months": 3,
    }
    data.update(overrides)
    return data


# ── PreProcessNode (outer pre_process, S-1 VERIFIED_EXTERNAL) ──────────────────


class TestPreProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def test_valid_payload_returns_success(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {}})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None
        assert json.loads(result["validated_input"])["proposal_id"] == "MFG-KAIZEN-20260712-001"

    def test_enriched_context_carries_proposal_id(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {"channel": "kaizen-portal"}})
        ctx = from_json(result["enriched_context"])
        assert ctx["proposal_id"] == "MFG-KAIZEN-20260712-001"
        # channel is caller data, so it is normalised to the inert identifier
        # alphabet before it is recorded: '-' becomes '_'.
        assert ctx["channel"] == "kaizen_portal"
        assert ctx["source"] == "KaizenProposalDigitizationROITrackingAgent"

    @pytest.mark.parametrize(
        "hostile_channel",
        [
            "portal\nInjected-Row: yes",
            "portal | forged",
            "<|im_start|>system",
            "a" * 64,
            {"not": "a string"},
            None,
        ],
    )
    def test_hostile_channel_cannot_reach_enriched_context(self, hostile_channel):
        """A caller-supplied channel never carries punctuation or layout through."""
        result = self.node.execute({"user_input": VALID_PAYLOAD, "input_context": {"channel": hostile_channel}})
        assert result["status"] == AgentStatus.SUCCESS.value
        ctx = from_json(result["enriched_context"])
        assert ctx["channel"] == "unknown"

    def test_empty_input_returns_error(self):
        result = self.node.execute({"user_input": "", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("empty" in e for e in result["error_log"])

    def test_invalid_json_returns_error(self):
        result = self.node.execute({"user_input": "{not valid json}", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("JSON" in e or "json" in e for e in result["error_log"])

    def test_non_object_json_returns_error(self):
        result = self.node.execute({"user_input": "[1, 2, 3]", "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("object" in e for e in result["error_log"])

    def test_missing_required_field_returns_error(self):
        payload = _proposal_payload()
        del payload["title"]
        result = self.node.execute({"user_input": json.dumps(payload), "input_context": {}})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("title" in e for e in result["error_log"])

    def test_trust_level_is_verified_external(self):
        assert self.node.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_execute_signature_is_state_first(self):
        import inspect
        from src.nodes.pre_process_node import PreProcessNode

        params = list(inspect.signature(PreProcessNode.execute).parameters.keys())
        assert params[0] == "self" and params[1] == "state"
        assert "_invoke_impl" not in PreProcessNode.__dict__


# ── InputValidateNode (inner domain node 1, ANONYMOUS) ─────────────────────────


class TestInputValidateNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.input_validate_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.input_validate_node import InputValidateNode

        self.node = InputValidateNode()

    def test_valid_input_builds_proposal_data(self):
        result = self.node.execute({"validated_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value
        data = from_json(result["proposal_data"])
        assert data["proposal_id"] == "MFG-KAIZEN-20260712-001"
        assert data["estimated_cost_jpy"] == 1200000
        assert data["estimated_annual_savings_jpy"] == 4800000
        assert data["affected_processes"] == ["die changeover", "press setup", "first-article inspection"]

    def test_category_aliases_are_normalised(self):
        payload = _proposal_payload(category="productivity")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert from_json(result["proposal_data"])["category"] == "efficiency"

        payload = _proposal_payload(category="cost")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert from_json(result["proposal_data"])["category"] == "cost_reduction"

    def test_numeric_fields_are_coerced(self):
        payload = _proposal_payload(estimated_cost_jpy="1200000", estimated_annual_savings_jpy=4800000.0)
        result = self.node.execute({"validated_input": json.dumps(payload)})
        data = from_json(result["proposal_data"])
        assert data["estimated_cost_jpy"] == 1200000
        assert isinstance(data["estimated_cost_jpy"], int)
        assert data["estimated_annual_savings_jpy"] == 4800000

    def test_falls_back_to_user_input(self):
        result = self.node.execute({"user_input": VALID_PAYLOAD})
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_empty_proposal_id_returns_error(self):
        payload = _proposal_payload(proposal_id="")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("proposal_id" in e for e in result["error_log"])

    def test_empty_narrative_field_returns_error(self):
        payload = _proposal_payload(title="")
        result = self.node.execute({"validated_input": json.dumps(payload)})
        assert result["status"] == AgentStatus.ERROR.value
        assert any("non-empty" in e for e in result["error_log"])

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── ParseProposalNode (inner domain node 2, ANONYMOUS) ─────────────────────────


class TestParseProposalNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.parse_proposal_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.parse_proposal_node import ParseProposalNode

        self.node = ParseProposalNode()

    def _run(self, cost, savings):
        state = {"proposal_data": to_json(_proposal_data(cost=cost, savings=savings))}
        return self.node.execute(state)

    def test_derives_net_annual_benefit(self):
        data = from_json(self._run(cost=1200000, savings=4800000)["proposal_data"])
        assert data["net_annual_benefit_jpy"] == 3600000

    def test_impact_tier_major(self):
        data = from_json(self._run(cost=1000000, savings=6000000)["proposal_data"])
        assert data["impact_tier"] == "major"  # net 5,000,000 >= 5M

    def test_impact_tier_significant(self):
        data = from_json(self._run(cost=1000000, savings=2500000)["proposal_data"])
        assert data["impact_tier"] == "significant"  # net 1,500,000 >= 1M

    def test_impact_tier_moderate(self):
        data = from_json(self._run(cost=1000000, savings=1150000)["proposal_data"])
        assert data["impact_tier"] == "moderate"  # net 150,000 >= 100k

    def test_impact_tier_minor(self):
        data = from_json(self._run(cost=1000000, savings=1050000)["proposal_data"])
        assert data["impact_tier"] == "minor"  # net 50,000 < 100k

    def test_missing_proposal_data_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── GenerateProposalSectionsNode (inner domain node 3, ANONYMOUS) ──────────────


class TestGenerateProposalSectionsNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.generate_proposal_sections_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.generate_proposal_sections_node import GenerateProposalSectionsNode

        self.node = GenerateProposalSectionsNode()

    def _data(self):
        d = _proposal_data()
        d["net_annual_benefit_jpy"] = 3600000
        d["impact_tier"] = "significant"
        return d

    def test_generates_all_six_sections(self):
        result = self.node.execute({"proposal_data": to_json(self._data())})
        assert result["status"] == AgentStatus.SUCCESS.value
        sections = from_json(result["proposal_sections"])
        assert set(sections.keys()) == {
            "proposal_overview",
            "background_current_state",
            "proposed_improvement",
            "expected_effects",
            "implementation_plan",
            "cost_and_roi_summary",
        }

    def test_overview_reflects_proposal_fields(self):
        sections = from_json(self.node.execute({"proposal_data": to_json(self._data())})["proposal_sections"])
        assert "MFG-KAIZEN-20260712-001" in sections["proposal_overview"]
        assert "Line 3 stamping press" in sections["proposal_overview"]

    def test_missing_proposal_data_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── CalculateRoiNode (inner domain node 4, ANONYMOUS) ──────────────────────────


class TestCalculateRoiNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.calculate_roi_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.calculate_roi_node import CalculateRoiNode

        self.node = CalculateRoiNode()

    def _run(self, cost, savings):
        return self.node.execute({"proposal_data": to_json(_proposal_data(cost=cost, savings=savings))})

    def test_positive_roi_within_horizon(self):
        result = self._run(cost=1200000, savings=4800000)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["roi_positive"] is True
        roi = from_json(result["roi_analysis"])
        assert roi["net_annual_benefit_jpy"] == 3600000
        assert roi["payback_period_months"] == 3.0
        assert roi["roi_tier"] == "excellent"  # payback <= 6 months

    def test_negative_net_benefit_not_viable(self):
        result = self._run(cost=5000000, savings=1000000)
        assert result["roi_positive"] is False
        roi = from_json(result["roi_analysis"])
        assert roi["net_annual_benefit_jpy"] == -4000000
        assert roi["roi_tier"] == "not_viable"

    def test_payback_over_horizon_not_positive(self):
        # cost 10M, savings 1M/yr -> payback 120 months > 24 -> not positive
        result = self._run(cost=10000000, savings=1000000)
        assert result["roi_positive"] is False
        roi = from_json(result["roi_analysis"])
        assert roi["payback_period_months"] == 120.0

    def test_missing_proposal_data_returns_error(self):
        result = self.node.execute({})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── OutputFormatNode (inner domain node 5, ANONYMOUS) ──────────────────────────


class TestOutputFormatNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.output_format_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.output_format_node import OutputFormatNode

        self.node = OutputFormatNode()

    def _sections(self):
        return {
            "proposal_overview": "Proposal ID:  MFG-KAIZEN-20260712-001",
            "background_current_state": "Die changeover takes 48 minutes.",
            "proposed_improvement": "Adopt an SMED kit.",
            "expected_effects": "  Net Annual Benefit: 3,600,000 JPY/yr",
            "implementation_plan": "  Implementation Period: 3 month(s)",
            "cost_and_roi_summary": "  One-time Cost: 1,200,000 JPY",
        }

    def _roi(self, roi_positive=True, roi_tier="excellent"):
        return {
            "roi_percent": 300.0,
            "payback_period_months": 3.0,
            "net_annual_benefit_jpy": 3600000,
            "roi_positive": roi_positive,
            "roi_tier": roi_tier,
            "payback_horizon_months": 24,
        }

    def _state(self, roi_positive=True, roi_tier="excellent"):
        return {
            "proposal_sections": to_json(self._sections()),
            "roi_analysis": to_json(self._roi(roi_positive, roi_tier)),
            "proposal_data": to_json(_proposal_data()),
        }

    def test_assembles_full_document(self):
        result = self.node.execute(self._state())
        assert result["status"] == AgentStatus.SUCCESS.value
        doc = result["kaizen_document"]
        assert result["result"] == doc
        assert "KAIZEN PROPOSAL" in doc
        assert "DIGITIZED RECORD" in doc
        assert "MFG-KAIZEN-20260712-001" in doc
        for header in (
            "1. Proposal Overview",
            "2. Background / Current State",
            "3. Proposed Improvement",
            "4. Expected Effects",
            "5. Implementation Plan",
            "6. Cost & ROI Summary",
            "7. ROI Analysis",
        ):
            assert header in doc, f"missing section header: {header}"

    def test_positive_roi_renders_yes_and_tier(self):
        doc = self.node.execute(self._state(roi_positive=True, roi_tier="excellent"))["kaizen_document"]
        assert "Positive Return:       YES" in doc
        assert "EXCELLENT" in doc

    def test_negative_roi_renders_no_and_tier(self):
        doc = self.node.execute(self._state(roi_positive=False, roi_tier="not_viable"))["kaizen_document"]
        assert "Positive Return:       NO" in doc
        assert "NOT_VIABLE" in doc

    def test_missing_sections_returns_error(self):
        result = self.node.execute({"proposal_data": to_json(_proposal_data())})
        assert result["status"] == AgentStatus.ERROR.value

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── PostProcessNode (outer post_process, S-3 output gate, ANONYMOUS) ───────────


class TestPostProcessNode:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)

    def setup_method(self):
        from src.nodes.post_process_node import PostProcessNode

        self.node = PostProcessNode()

    def test_clean_document_passes_gate(self):
        doc = "KAIZEN PROPOSAL\nProposal ID: MFG-KAIZEN-1\nAll clear."
        result = self.node.execute({"kaizen_document": doc, "roi_positive": True})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == doc
        assert result["result"] == doc

    def test_empty_document_uses_fallback(self):
        result = self.node.execute({"kaizen_document": "", "roi_positive": False})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "No document content generated" in result["formatted_output"]

    def test_s3_gate_redacts_credential_leak(self):
        leaky = "KAIZEN PROPOSAL\ntoken=sk-abcdefghij0123456789ABCDEF"
        result = self.node.execute({"kaizen_document": leaky, "roi_positive": True})
        assert result["status"] == AgentStatus.ERROR.value
        assert "REDACTED" in result["formatted_output"]
        assert result["formatted_output"] == result["result"]
        assert any("S-3" in e for e in result["error_log"])

    def test_security_gate_output_helper_detects_and_clears(self):
        from src.nodes.post_process_node import _security_gate_output

        assert _security_gate_output("sk-abcdefghij0123456789ABCDEF") is not None
        assert _security_gate_output("Bearer abcdefgh12345678") is not None
        assert _security_gate_output("password = supersecret123") is not None
        assert _security_gate_output("A perfectly clean Kaizen document.") is None

    def test_trust_level_anonymous(self):
        assert self.node.required_trust_level == TrustLevel.ANONYMOUS


# ── Graph wiring: outer AgentBaseGraph + inner BaseGraph (Cat 2 nested) ─────────


class TestOuterGraphComposition:
    def test_registers_five_backbone_slots(self):
        from src.graph.graph import (
            KaizenDocumentGraphNode,
            KaizenProposalDigitizationROITrackingAgent,
        )
        from src.nodes.post_process_node import PostProcessNode
        from src.nodes.pre_process_node import PreProcessNode

        agent = KaizenProposalDigitizationROITrackingAgent()
        agent.compile()
        assert set(agent._nodes.keys()) == {
            "initialize",
            "pre_process",
            "main",
            "post_process",
            "finalize",
        }
        assert isinstance(agent._nodes["pre_process"], PreProcessNode)
        assert isinstance(agent._nodes["main"], KaizenDocumentGraphNode)
        assert isinstance(agent._nodes["post_process"], PostProcessNode)

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.graph import KaizenProposalDigitizationROITrackingAgent

        agent = KaizenProposalDigitizationROITrackingAgent()
        assert agent.name == "KaizenProposalDigitizationROITrackingAgent"
        assert agent.state_schema is State

    def test_main_slot_graphnode_contracts(self):
        from src.graph.graph import KaizenDocumentGraphNode

        node = KaizenDocumentGraphNode()
        assert node.error_strategy == "propagate"
        assert node.propagate_hitl is False
        # extract_input prefers validated_input, falls back to user_input
        assert node.extract_input({"validated_input": "V", "user_input": "U"}) == "V"
        assert node.extract_input({"user_input": "U"}) == "U"

    def test_merge_output_maps_subresult_keys(self):
        from src.graph.graph import KaizenDocumentGraphNode

        node = KaizenDocumentGraphNode()
        sub_result = {
            "kaizen_document": "DOCUMENT",
            "proposal_sections": "{}",
            "roi_analysis": "{}",
            "roi_positive": True,
            "status": AgentStatus.SUCCESS.value,
            "node_history": ["x"],  # not forwarded by merge_output
        }
        delta = node.merge_output({}, sub_result)
        assert delta["kaizen_document"] == "DOCUMENT"
        assert delta["roi_positive"] is True
        assert delta["status"] == AgentStatus.SUCCESS.value
        assert set(delta.keys()) == {
            "kaizen_document",
            "proposal_sections",
            "roi_analysis",
            "roi_positive",
            "status",
        }


class TestInnerDomainGraph:
    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        for mod in (
            "input_validate_node",
            "parse_proposal_node",
            "generate_proposal_sections_node",
            "calculate_roi_node",
            "output_format_node",
        ):
            monkeypatch.setattr(f"src.nodes.{mod}.emit_trace_event", lambda *a, **k: None)

    def test_registers_five_domain_nodes(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.register_nodes()
        assert set(g._nodes.keys()) == {
            "input_validate",
            "parse_proposal",
            "generate_proposal_sections",
            "calculate_roi",
            "output_format",
        }

    def test_name_and_state_schema(self):
        from src.schemas.state import State
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        assert g.name == "mfg_c2_054_kaizen_proposal_workflow"
        assert g.state_schema is State

    def test_inner_graph_invoke_produces_document(self):
        """Standalone inner-graph invoke (ANONYMOUS caller) runs the linear pipeline
        and shapes the get_output() dict consumed by the outer merge_output()."""
        from framework.schemas.invocation_context import InvocationContext, TrustLevel
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        g = DomainWorkflowGraph()
        g.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        result = g.invoke(VALID_PAYLOAD, ctx=ctx)
        # The inner get_output IS the terminal output, so its keys surface directly.
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["kaizen_document"] is not None
        assert result["roi_positive"] is True
        assert "KAIZEN PROPOSAL" in result["kaizen_document"]
