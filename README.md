# Kaizen Proposal & ROI Tracking Agent

AI agent for digitizing kaizen proposals and tracking their ROI, built with Agentic Star.

> **Category**: Cat 2 (domain pipeline — a multi-step workflow for one manufacturing job-to-be-done)
> **Industry**: Manufacturing
> **Template ID**: MFG-C2-054

## Overview

Turns a kaizen (continuous-improvement) proposal submitted as a form into a structured,
reviewable document, and scores its return on investment so the improvement office can rank
proposals consistently instead of by whoever wrote the most persuasive paragraph.

A caller posts one proposal — an id, a title, the current state, the proposed improvement, the
affected processes, and the one-time cost, expected annual saving and implementation period. The
agent validates and bounds every field, generates the six standard proposal sections, computes ROI
percentage, payback period and a positive-return verdict against a 24-month horizon, and assembles
the whole thing into a single plain-text record with a traceable ROI section: every figure in the
document is either a value the submitter supplied or one derived from them by a stated formula.

Section text is generated deterministically from templates rather than by a language model, so the
same proposal always produces the same document — the property an audit trail needs.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded mode.
The agent's modules import the framework package directly, so if it is not installed — or the
installed version does not provide the interfaces this template uses — start-up fails at import
time rather than serving requests from a partially wired graph. This is intentional: a half-running
agent is worse than one that refuses to start.

## Input

`POST /invoke` takes the proposal as a JSON-encoded string in `input`:

```json
{
  "proposal_id": "MFG-KAIZEN-20260712-001",
  "title": "Reduce die-changeover time on the Line 3 stamping press",
  "submitter": "T. Sato",
  "department": "Press Shop",
  "category": "efficiency",
  "current_state": "Die changeover takes 48 minutes on average ...",
  "proposed_improvement": "Adopt an SMED kit with quick-clamp fixtures ...",
  "affected_processes": ["die changeover", "press setup"],
  "estimated_cost_jpy": 1200000,
  "estimated_annual_savings_jpy": 4800000,
  "implementation_period_months": 3
}
```

`proposal_id`, `title`, `current_state` and `proposed_improvement` are required. Every numeric
field must be finite and within its declared range; a value that cannot be read is refused rather
than defaulted, because reporting a fabricated figure as the submitter's own is worse than
returning an error.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/nodes/     the pipeline: validate → parse → generate sections → score ROI → format
src/graph/     the agent graph (outer backbone) and the domain workflow graph it wraps
src/security/  template-owned input screening and numeric bounds
src/schemas/   the state contract shared by every node
src/api/       the standalone HTTP entry point
tests/         unit, integration and boundary tests
config/        agent.yaml (manifest) and config.yaml (runtime parameters)
docs/          design specification and test specification
```

See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/config.yaml` for your own environment and policies — the ROI payback horizon and
   the impact tiers are the two constants most deployments change first (`src/nodes/calculate_roi_node.py`
   and `src/nodes/parse_proposal_node.py`).
2. Replace the sample proposal in `deploy/invoke_payload.json` with one of your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic — the section
   templates in `generate_proposal_sections_node.py` are where a house document format goes.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
