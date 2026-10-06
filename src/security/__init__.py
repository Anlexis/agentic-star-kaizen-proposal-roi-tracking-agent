"""AgentCore Platform v1.0"""

# MFG-C2-054 — template-owned security helpers.
#
# The framework supplies platform-level gates (trust, PII masking, injection
# policy, credential scan).  This package holds the checks the TEMPLATE owns:
# they run inside the domain nodes, so they still apply when a caller reaches
# the agent by a path where a platform gate is absent or configured off.
