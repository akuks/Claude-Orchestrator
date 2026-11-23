"""Org model tiers: strongest / standard / light map to model ids, and an
agent bound to a tier follows the org map (an agent's own model wins)."""

from __future__ import annotations

import re

from .constants import VALID_MODELS
from .models import GuardOrgSettings

TIERS = ("strongest", "standard", "light")
DEFAULT_TIERS = {"strongest": "opus", "standard": "sonnet", "light": "haiku"}
_MODEL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{1,60}(\[1m\])?$")


def _ok(value) -> bool:
    # at most 32 characters: Task.model and Agent.model are String(32)
    return isinstance(value, str) and (value in VALID_MODELS or (len(value) <= 32 and bool(_MODEL_ID.match(value))))


def normalized(stored) -> dict:
    """The defaults updated by every valid stored value (anything else is ignored)."""
    out = dict(DEFAULT_TIERS)
    if isinstance(stored, dict):
        for k in TIERS:
            if _ok(stored.get(k)):
                out[k] = stored[k]
    return out


def validate(value) -> dict:
    """The whole map after a PUT (keys left out take their default); ValueError with the 400 text."""
    if not isinstance(value, dict):
        raise ValueError("model_tiers must be an object")
    for k in value:
        if k not in TIERS:
            raise ValueError(f"unknown model tier: {k}")
    for k in TIERS:
        if k in value and not _ok(value[k]):
            raise ValueError(f"model_tiers.{k} must be opus, sonnet, haiku or a claude-… model id")
    return normalized(value)


def tier_values(tiers: dict) -> list[str]:
    return list(dict.fromkeys(tiers[k] for k in TIERS))


def valid_agent_model(model: str, tiers: dict) -> bool:
    return model in VALID_MODELS or model in tier_values(tiers)


async def org_tiers(s) -> dict:
    row = await s.get(GuardOrgSettings, 1)
    return normalized(row.model_tiers if row else None)


async def agent_model(s, agent) -> str | None:
    """Own model, else the model of its tier, else None (the project default applies)."""
    if agent.model:
        return agent.model
    if agent.tier in TIERS:
        return (await org_tiers(s))[agent.tier]
    return None
