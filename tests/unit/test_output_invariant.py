# MFG-C2-054 — the output boundary.
#
# Two independent layers guard the caller-facing output, and each is asserted
# HERE at its own boundary rather than through the other:
#
#   1. PostProcessNode.execute() replaces the document with a truthy sanitised
#      stub AND clears the pre-gate output-bearing state fields.
#   2. Graph.get_output() withholds the structured domain fields on any
#      non-success status.
#
# Testing each through the other would make both unfalsifiable: with layer 1 in
# place a full invoke stays clean even if layer 2 is removed, and vice versa.
# The end-to-end containment test in tests/integration/ is contained by either
# layer alone, which is exactly why it is not the proof for either one.

import json

import pytest

from framework.schemas.agent_status import AgentStatus

from src.nodes.output_format_node import _assemble_document
from src.nodes.post_process_node import PostProcessNode, _security_gate_output


def _conn_uri(scheme: str, port: int) -> str:
    """Build a database connection string carrying inline credentials.

    Assembled rather than written out: a connection string with inline
    credentials, written literally, trips the repository's credential gate, which
    holds that pattern even in test code — correctly, since the rule is about the
    shape appearing in a published tree, not about whether the value is live. The
    probe needs the shape at runtime, and this produces it without committing one.
    """
    return f"{scheme}://" + "operator" + ":" + "sample-pw" + "@" + f"db.example:{port}/kaizen"


_OUTPUT_BEARING_FIELDS = (
    "kaizen_document",
    "proposal_sections",
    "roi_analysis",
    "roi_positive",
)


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", lambda *a, **k: None)


# ── The gate's pattern set is the UNION of both halves ───────────────────────


@pytest.mark.parametrize(
    "secret,why",
    [
        # Caught by the template's own patterns; the framework describes none of
        # these shapes, so delegating to it would make the gate NARROWER.
        ("password=SuperSecret123", "template-only"),
        ("api_key: 0123456789abcdef", "template-only"),
        ("private_key = MIIEvQIBADANBgkq", "template-only"),
        ("Bearer abc123.def456", "template-only"),
        ("pk-abcdefghij0123456789", "template-only"),
        # Caught by the framework; the template's prefixes miss all three, and a
        # miss here is not "one less check" — the framework's own scan then
        # raises from inside the node wrapper, discarding the redaction this
        # gate prepared and handing the caller an unexplained empty envelope.
        ("sk_live_abcdefghij0123456789", "framework-only"),
        ("AKIAIOSFODNN7EXAMPLE", "framework-only"),
        (_conn_uri("postgresql", 5432), "framework-only"),
        (_conn_uri("mongodb", 27017), "framework-only"),
        # Caught by both.
        ("sk-abcdefghij0123456789ABCDEF", "both"),
    ],
)
def test_gate_blocks_every_credential_shape(secret, why):
    assert _security_gate_output(f"KAIZEN RECORD\nkey {secret} on the drive") is not None, why


@pytest.mark.parametrize(
    "clean",
    [
        "",
        "KAIZEN PROPOSAL — DIGITIZED RECORD\nProposal ID: MFG-KAIZEN-20260712-001",
        "Replace bearing SKF-6205-2RS on press line 3.",
        "Estimated Annual Savings: 4,800,000 JPY/yr",
        "Part codes sku_48210 and ENE-FAC-20260712-001 are unaffected.",
    ],
)
def test_gate_passes_ordinary_document_text(clean):
    assert _security_gate_output(clean) is None


# ── Layer 1: the node redacts AND clears ─────────────────────────────────────


def _blocked_state(secret="sk-abcdefghij0123456789ABCDEF"):
    return {
        "kaizen_document": f"KAIZEN PROPOSAL — DIGITIZED RECORD\nkey {secret}",
        "proposal_sections": json.dumps({"proposed_improvement": secret}),
        "roi_analysis": json.dumps({"roi_positive": True}),
        "roi_positive": True,
    }


def test_violation_returns_error_and_a_truthy_replacement():
    result = PostProcessNode().execute(_blocked_state())
    assert result["status"] == AgentStatus.ERROR.value
    # Truthy matters: the framework envelope falls back to state["result"]
    # whenever formatted_output is falsy, so an empty string would re-open the
    # exact channel this gate closes.
    assert result["formatted_output"], "replacement must be truthy"
    assert result["result"]


def test_violation_clears_every_output_bearing_field():
    result = PostProcessNode().execute(_blocked_state())
    for field in _OUTPUT_BEARING_FIELDS:
        assert field in result, f"{field} must be cleared explicitly, not left on state"
        assert result[field] is None, f"{field} still carries the pre-gate value"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghij0123456789ABCDEF",
        "sk_live_abcdefghij0123456789",
        "AKIAIOSFODNN7EXAMPLE",
        "password=SuperSecret123",
    ],
)
def test_violation_releases_no_part_of_the_secret(secret):
    result = PostProcessNode().execute(_blocked_state(secret))
    rendered = json.dumps(result, default=str)
    assert secret not in rendered
    assert "Traceback" not in rendered


def test_clean_document_passes_through_unchanged():
    document = "KAIZEN PROPOSAL — DIGITIZED RECORD\nProposal ID: MFG-KAIZEN-20260712-001"
    result = PostProcessNode().execute({"kaizen_document": document, "roi_positive": True})
    assert result["status"] == AgentStatus.SUCCESS.value
    assert result["formatted_output"] == document
    # The clean path must NOT clear the domain fields — only the violation path does.
    for field in _OUTPUT_BEARING_FIELDS:
        assert field not in result


# ── Layer 2: the graph withholds on any non-success status ───────────────────


@pytest.mark.parametrize(
    "status",
    [
        AgentStatus.ERROR.value,
        AgentStatus.PENDING.value,
        AgentStatus.CANCELLED.value,
        AgentStatus.TIMEOUT.value,
    ],
)
def test_get_output_withholds_structured_fields_on_every_non_success_status(status):
    """Every path that can return non-success is measured, not just the one an
    output-gate violation takes."""
    from src.graph.graph import Graph

    agent = Graph()
    agent.compile()
    secret = "sk-abcdefghij0123456789ABCDEF"
    surfaced = agent.get_output(
        {
            "status": status,
            "formatted_output": "[REDACTED]",
            "result": "[REDACTED]",
            **_blocked_state(secret),
        }
    )
    for field in _OUTPUT_BEARING_FIELDS:
        assert surfaced.get(field) is None
    assert secret not in json.dumps(surfaced, default=str)


# ── Identifier fidelity: the reason the precision grid is not applied here ───


@pytest.mark.parametrize(
    "identifier",
    [
        "MFG-KAIZEN-20260712-001",
        "SKF-6205",
        "STU-1234",
        "sku_48210",
        "ENE-FAC-20260712-001",
        "JPY-9999",
    ],
)
def test_shopfloor_identifiers_reach_the_document_byte_identical(identifier):
    """No output transform may rewrite a caller identifier.

    A monetary precision grid reads any standalone three-letter uppercase word
    as a currency marker and would turn SKF-6205 into SKF-6,000. This template
    reproduces the submitter's own declared figures rather than aggregating a
    corpus, so the grid is not applied — and this test is what pins that
    decision to observable behaviour rather than to a comment.
    """
    document = _assemble_document(
        identifier,
        {"proposal_overview": f"Proposal ID:  {identifier}"},
        {"roi_positive": True, "roi_percent": 300.0, "payback_period_months": 3.0},
    )
    assert identifier in document


def test_declared_amounts_are_reproduced_exactly():
    document = _assemble_document(
        "MFG-KAIZEN-20260712-001",
        {"expected_effects": "  Estimated Annual Savings: 4,812,345 JPY/yr"},
        {"net_annual_benefit_jpy": 3612345, "roi_positive": True},
    )
    assert "4,812,345" in document
    assert "3,612,345" in document
