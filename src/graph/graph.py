"""AgentCore Platform v1.0"""

# MFG-C2-054 — Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed — identical to Cat 1, do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#
#   `main` slot is a GraphNode subclass (KaizenDocumentGraphNode) that delegates
#   the full domain workflow to DomainWorkflowGraph (inner BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (multi-step topology)
#
# Rules enforced:
#   ✅ KaizenProposalDigitizationROITrackingAgent inherits AgentBaseGraph (L1 Base)
#   ✅ super().register_nodes() called first (fills initialize + finalize)
#   ✅ KaizenDocumentGraphNode assigned to self._nodes["main"]
#   ✅ PreProcessNode (VERIFIED_EXTERNAL) in pre_process slot (S-1 gate)
#   ✅ PostProcessNode (ANONYMOUS) in post_process slot (S-3 output gate)
#   ✅ S-3 _security_gate_output declared on the agent class (delegates to the
#      module-level credential scanner enforced at runtime in PostProcessNode)
#   ✅ merge_output() returns only changed keys
#   ✅ get_output() surfaces the domain result (SR #12 re-review 2026-07)
#   ✅ _parent_config() forwards the manifest runtime config (SR #12 Medium 2026-07)
#   ✅ class name matches config/agent.yaml class: field exactly
#   ❌ add_edges() NOT overridden on the outer graph
#   ❌ No Level-0 platform SDK imports

import os
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.post_process_node import PostProcessNode, _security_gate_output
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.graph.domain_workflow_graph import DomainWorkflowGraph

# Repo root — three levels up from this file:
# src/graph/graph.py → src/graph → src → <repo root>.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Runtime parameters live in config/config.yaml. config/agent.yaml is the static
# manifest and carries no runtime block at all — reading it for one would return
# nothing and silently drop every declared value back to a library default.
_RUNTIME_CONFIG_PATH = os.path.join(_REPO_ROOT, "config", "config.yaml")

# Keys the framework's AgentBaseGraph reads off self.config. Only these are
# forwarded to the base class; the rest of config.yaml belongs to the domain.
_FRAMEWORK_CONFIG_KEYS = ("max_retry", "timeout_s", "memory_enabled", "hitl")


def _load_runtime_config() -> Dict[str, Any]:
    """Return the parsed contents of config/config.yaml.

    Best-effort: a missing or unparseable file yields ``{}`` so graph
    construction never breaks — the framework and the inner nodes then fall back
    to their own declared defaults. PyYAML is loaded lazily because it is a
    framework runtime dependency, so importing it on demand avoids a hard
    module-load coupling.
    """
    try:
        import yaml

        with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


class KaizenDocumentGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of the agent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()  — instantiate and return DomainWorkflowGraph
      extract_input() — pull validated_input (S-1 output) from outer state
      merge_output()  — map sub_result fields into outer state delta (changed keys only)
      error_strategy  — "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> "DomainWorkflowGraph":
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the Cat 2 pattern.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the string input passed into inner_graph.invoke().

        PreProcessNode (S-1) validates and normalises the raw user_input and
        writes the result to validated_input.  Prefer that; fall back to
        user_input if validated_input is absent (e.g. in unit tests).
        """
        return str(state.get("validated_input") or state.get("user_input") or "")

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  → "kaizen_document", "proposal_sections",
                                       "roi_analysis", "roi_positive", "status"
          This merge_output() reads → sub_result.get(...) for each of these keys.

        PostProcessNode (outer post_process) reads kaizen_document + roi_positive
        from state to apply the S-3 gate and set formatted_output.
        """
        return {
            "kaizen_document": sub_result.get("kaizen_document"),
            "proposal_sections": sub_result.get("proposal_sections"),
            "roi_analysis": sub_result.get("roi_analysis"),
            "roi_positive": sub_result.get("roi_positive", False),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the declared runtime config to the inner graph.

        The SDK does not thread a graph's ``self.config`` into
        ``node.execute()``'s ``config`` argument on the real ``.invoke()`` path
        — that argument is only populated by direct unit calls. So the declared
        settings are exposed here under the LangGraph ``configurable`` key, and
        DomainWorkflowGraph._extra_initial_state() copies the declared
        ``system_prompt_template`` into the inner state, where
        GenerateProposalSectionsNode can observe it end-to-end.

        The values are read from ``config/config.yaml``. Only keys that file
        actually declares are forwarded; absent keys are omitted so the inner
        nodes fall back to their deterministic defaults.
        """
        llm = _load_runtime_config().get("llm") or {}
        declared = {
            "system_prompt_template": llm.get("system_prompt_template"),
            "temperature": llm.get("temperature"),
            "max_tokens": llm.get("max_tokens"),
        }
        return {"configurable": {k: v for k, v in declared.items() if v is not None}}


class KaizenProposalDigitizationROITrackingAgent(AgentBaseGraph):
    """Outer graph for MFG-C2-054 (Cat 2 — DocGenerationAgent).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in KaizenDocumentGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed — identical to Cat 1):
        START → initialize → pre_process → main → post_process → finalize → END

    register_nodes() and get_output() are the only overrides:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process: PreProcessNode    (VERIFIED_EXTERNAL — S-1 trust gate)
      - main:        KaizenDocumentGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode  (ANONYMOUS — S-3 output gate)
      - get_output(): surfaces the domain result (SR #12 re-review 2026-07)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.

    Class name MUST match config/agent.yaml `class:` field exactly.
    server.py imports this class directly; `Graph` (alias below) is the stable
    name exported by src/graph/__init__.py.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Default the framework config from ``config/config.yaml``.

        AgentRegistry passes the parsed runtime config in; a standalone entry
        point (``src/api/server.py``) constructs the agent with no arguments.
        Without this default the second case ran on an empty config, so the
        declared ``max_retry`` and ``timeout_s`` were unreachable and the
        framework silently used its own library defaults instead — the file
        looked authoritative and governed nothing.

        Only the keys AgentBaseGraph actually reads are forwarded, so a domain
        key in config.yaml cannot collide with a framework one. An explicit
        ``config`` argument always wins.
        """
        if config is None:
            declared = _load_runtime_config()
            config = {key: declared[key] for key in _FRAMEWORK_CONFIG_KEYS if key in declared}
        super().__init__(config=config)

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "KaizenProposalDigitizationROITrackingAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = KaizenDocumentGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Surface the digitized Kaizen document result on the outer invoke() return.

        SR #12 re-review (2026-07): AgentBaseGraph.get_output() returns only the
        minimal ``{output, status, trace_id, correlation_id, node_history}``
        envelope. On the compiled outer-graph success path that dropped the
        structured domain result that KaizenDocumentGraphNode.merge_output()
        merges into outer state (kaizen_document, proposal_sections,
        roi_analysis, roi_positive) and the S-3-gated PostProcessNode outputs
        (formatted_output, result) — from the dict returned by ``agent.invoke()``.
        They were all None to a programmatic caller even on a successful run.
        This override extends the base envelope so a successful invocation
        actually returns the domain result.

        S-3 invariant preserved (fail-closed):
          * ``formatted_output`` / ``result`` are the POST-gate, caller-facing
            values produced by PostProcessNode — the S-3 gate has already blocked
            credential patterns (status -> ERROR + sanitised stub). We surface
            those, and the surfaced ``kaizen_document`` is sourced from them,
            NEVER the pre-gate raw ``state["kaizen_document"]``, so the output
            gate cannot be bypassed.
          * The structured domain fields (kaizen_document / proposal_sections /
            roi_analysis / roi_positive) are surfaced ONLY when the gate passed
            (status == SUCCESS). On any non-success outcome — including an S-3
            credential block — they are withheld (None).
        """
        output: Dict[str, Any] = dict(
            super().get_output(state)
        )  # {output, status, trace_id, correlation_id, node_history}
        succeeded = state.get("status") == AgentStatus.SUCCESS.value
        gated_document = state.get("formatted_output") or state.get("result")

        # Caller-facing, already S-3-gated values (safe on both paths).
        output["formatted_output"] = state.get("formatted_output")
        output["result"] = state.get("result")

        # Structured domain result — surfaced only on the gated success path.
        output["kaizen_document"] = gated_document if succeeded else None
        output["proposal_sections"] = state.get("proposal_sections") if succeeded else None
        output["roi_analysis"] = state.get("roi_analysis") if succeeded else None
        output["roi_positive"] = state.get("roi_positive") if succeeded else None
        return output

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def _security_gate_output(self, content: str) -> Optional[str]:
        """Canonical S-3 output gate declaration (the framework rules §5-6).

        Delegates to the module-level credential/secret scanner that is
        ENFORCED at runtime inside PostProcessNode.execute() (post_process
        backbone slot).  Returns the first violation name, or None if the
        content is clean.  Declared here so the S-3 output gate is discoverable
        on the agent class; the runtime enforcement stays on the post_process
        node (the CoE-green nested-Cat-2 placement — mirror of the released
        A peer template / pilot a peer templates).
        """
        return _security_gate_output(content or "")


# Alias for the package export (src/graph/__init__.py) and backward compat.
# Class name KaizenProposalDigitizationROITrackingAgent matches config/agent.yaml
# class: field and the src/api/server.py import exactly.
Graph = KaizenProposalDigitizationROITrackingAgent
