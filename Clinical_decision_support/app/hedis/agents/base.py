"""Agent framework: tiers, findings, the action catalogue and the policy every agent run goes through.

Tiers
  L1  Autonomous   Executes on its own, but only reversible, low-risk actions from the L1 catalogue
                   (open a data-quality task, set work priorities, assemble a draft batch).
  L2  Assisted     Proposes; a person holding the action's permission approves, and the action then
                   runs under that person's name. Agents never approve, sign or transmit.
  L3  Escalation   Never acts. Packages the case and routes it to an expert role, who records the outcome.

Determinations are rule-based and reproducible. A language model, when enabled with HEDIS_AGENT_LLM=1,
only rewrites the explanation shown to people; it never decides what an agent does.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TierPolicy:
    tier: str
    label: str
    executes: str  # "auto" | "on_approval" | "never"
    summary: str


TIERS = {
    "L1": TierPolicy("L1", "Autonomous", "auto", "Runs reversible, low-risk actions itself; every action is logged and can be undone."),
    "L2": TierPolicy("L2", "Assisted", "on_approval", "Proposes; a person with the right permission approves before anything happens."),
    "L3": TierPolicy("L3", "Escalation", "never", "Never acts; packages the case for an expert who decides."),
}


@dataclass(frozen=True)
class ActionDef:
    name: str
    tiers: tuple  # tiers allowed to use this action
    permission: tuple  # (module, action) a human needs to approve it (L2)
    reversible: bool
    handler: Callable[..., dict]  # (payload, actor, path) -> result
    label: str


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    tier: str
    purpose: str
    reads: tuple
    actions: tuple
    escalates_to: tuple = ()


@dataclass
class Finding:
    """One thing an agent wants done (L1/L2) or someone to look at (L3)."""

    action: str  # catalogue action, or "escalate" for L3
    target: str
    title: str
    rationale: str
    dedupe_key: str
    payload: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    confidence: float = 1.0
    escalate_to: tuple = ()
    severity: str = "medium"


def narrate(finding: Finding) -> str:
    """Plain-language explanation. Template by default; optionally rephrased by the configured LLM."""
    text = finding.rationale
    if os.environ.get("HEDIS_AGENT_LLM", "0") != "1":
        return text
    try:
        from app.llm_client import call_llm

        prompt = (f"Rewrite for a quality-operations reviewer in two sentences. Keep every number, ID and code exactly. "
                  f"Do not add facts or recommendations.\n\nTitle: {finding.title}\nExplanation: {text}")
        rewritten = (call_llm(prompt, system_prompt="You rewrite text; you never change facts.", temperature=0.1) or "").strip()
        return rewritten or text
    except Exception:  # the narrative is optional; a provider failure never blocks an agent
        return text
