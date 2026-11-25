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


_FAMILIES = tuple(sorted(VALID_MODELS))


def _family(model: str) -> str | None:
    m = model.lower()
    if m in VALID_MODELS:
        return m
    found = [f for f in _FAMILIES if f in m.split("-")]
    return found[0] if len(found) == 1 else None


def tier_of(model, tiers: dict) -> str | None:
    """The org tier name a model runs as (phase 7e, F102): a tier whose model is exactly this one, else one of
    the same family (opus / sonnet / haiku); the first such tier in TIERS order. Unknown -> None."""
    if not isinstance(model, str) or not model or not isinstance(tiers, dict):
        return None
    model = model.strip()
    if model.endswith("[1m]"):
        model = model[:-4]
    vals = {k: (v[:-4] if isinstance(v, str) and v.endswith("[1m]") else v) for k, v in tiers.items()}
    for k in TIERS:
        if vals.get(k) == model:
            return k
    fam = _family(model)
    if fam is None:
        return None
    for k in TIERS:
        v = vals.get(k)
        if isinstance(v, str) and v and _family(v) == fam:
            return k
    return None


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


def lint_agent(model: str | None, description: str | None, tiers: dict) -> list[str]:
    """Non-blocking warnings for an agent; a save is never refused for them."""
    out = []
    if model and model not in tier_values(tiers):
        out.append(f'model "{model}" is not one of the org\'s tier models ({", ".join(tier_values(tiers))})')
    d = (description or "").lower()
    if "use for" not in d:
        out.append('the description should say when to use this agent ("Use for …")')
    if "because" not in d:
        out.append('the description should say why ("… because …")')
    return out
