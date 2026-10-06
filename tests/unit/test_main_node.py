# MFG-C2-054 — Unit Tests: Main Node

import json

from src.nodes.main_node import MainNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel


# Valid, PII-free Kaizen proposal payload used as the S-1 positive control.
# Deliberately all-lowercase with no digit groups, no "@", and no two
# consecutive Title-Case words, so the framework S-2 input gate masks no bytes
# before PreProcessNode.execute() parses the JSON.
_S1_CLEAN_PAYLOAD = json.dumps(
    {
        "proposal_id": "kaizen-proposal-alpha",
        "title": "reduce die changeover time on the stamping line",
        "current_state": "die changeover on the press takes too long every shift",
        "proposed_improvement": "adopt a quick-clamp kit and a rolling die cart for faster setup",
    }
)


class TestMainNode:
    """Unit tests for the main business logic node."""

    def setup_method(self):
        self.node = MainNode()

    def test_success_path(self):
        """TC: Main node processes valid input and returns SUCCESS.

        Invoked through BaseNode.__call__ (self.node(state)) rather than
        self.node.execute(state), so the S-1 trust gate runs first. MainNode
        requires ANONYMOUS trust, so an ANONYMOUS caller clears the gate and
        execute() runs normally.
        """
        state = {
            "validated_input": "test input",
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)
        # review finding 15: status is the lowercase .value string, not the bare enum member.
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["status"] == "success"
        assert result["result"] is not None

    def test_empty_input(self):
        """TC: Main node handles empty input gracefully."""
        state = {
            "validated_input": "",
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value

    def test_execute_method_signature(self):
        """Node contract: Node must implement execute(state) not _invoke_impl.

        Canonical contract (the review criteria §2-2):
          - Override: execute(self, state: AgentState) -> dict
          - PROHIBITED: _invoke_impl(), process() override
        """
        import inspect

        # Must have execute() defined on the concrete class (not just inherited stub)
        assert hasattr(MainNode, "execute"), "MainNode must implement execute()"

        sig = inspect.signature(MainNode.execute)
        params = list(sig.parameters.keys())
        # execute(self, state) — at minimum two parameters
        assert len(params) >= 2, f"execute() must accept (self, state), got params: {params}"
        assert params[1] == "state", f"Second parameter must be 'state', got '{params[1]}'"

        # Must NOT define _invoke_impl at the domain level
        assert (
            "_invoke_impl" not in MainNode.__dict__
        ), "_invoke_impl() must not be defined in MainNode — use execute() instead"


class TestS1TrustGate:
    """S-1 trust-gate coverage — CoE finding MFG-C2-054-CR-R1-01.

    Node invocations must go through BaseNode.__call__ (i.e. ``node(state)``),
    which runs the S-1 trust gate BEFORE execute() and cannot be bypassed by a
    subclass. PreProcessNode is the outer pre_process slot and the only node
    that requires VERIFIED_EXTERNAL trust, so it is the negative-authorization
    boundary: an under-trusted caller must be rejected before execute() runs.

    The gate RETURNS an error dict (it does not raise), so both cases assert on
    the returned dict.
    """

    def setup_method(self):
        from src.nodes.pre_process_node import PreProcessNode

        self.node = PreProcessNode()

    def test_s1_gate_rejects_untrusted_caller_before_execute(self):
        """ANONYMOUS < VERIFIED_EXTERNAL → gate denies; execute() never runs.

        Status is ERROR, error_log carries the 'trust gate denied' reason, and
        none of the keys PreProcessNode.execute() would have written
        (validated_input) appear in the result.
        """
        state = {
            "user_input": _S1_CLEAN_PAYLOAD,
            "input_context": {},
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert any("trust gate denied" in entry for entry in result["error_log"])
        assert "validated_input" not in result

    def test_s1_gate_admits_trusted_caller(self):
        """VERIFIED_EXTERNAL meets the requirement → execute() runs and the node
        returns SUCCESS with a validated_input payload (positive control)."""
        state = {
            "user_input": _S1_CLEAN_PAYLOAD,
            "input_context": {},
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "node_history": [],
            "error_log": [],
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["validated_input"] is not None
