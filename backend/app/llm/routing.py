"""Per-stage LLM routing: model choice, token budget, thinking config, and pricing.

Routes are static defaults; `get_route` applies per-route env overrides so a demo
operator can swap models or budgets without code changes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RouteConfig:
    model: str
    max_tokens: int
    adaptive_thinking: bool
    effort: str | None
    vision: bool


ROUTES: dict[str, RouteConfig] = {
    "stage1_diagnostic": RouteConfig(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        adaptive_thinking=False,
        effort=None,
        vision=True,
    ),
    "stage2_recommendation": RouteConfig(
        model="claude-opus-4-8",
        max_tokens=16000,
        adaptive_thinking=True,
        effort="medium",
        vision=False,
    ),
    "stage3_adjudication": RouteConfig(
        model="claude-opus-4-8",
        max_tokens=16000,
        adaptive_thinking=True,
        effort="medium",
        vision=False,
    ),
    "claimant_email": RouteConfig(
        model="claude-haiku-4-5",
        max_tokens=1024,
        adaptive_thinking=False,
        effort=None,
        vision=False,
    ),
    "eval_judge": RouteConfig(
        model="claude-haiku-4-5",
        max_tokens=1024,
        adaptive_thinking=False,
        effort=None,
        vision=False,
    ),
}

# (input USD per MTok, output USD per MTok)
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def get_route(name: str, settings_env: Mapping[str, str] | None = None) -> RouteConfig:
    """Resolve a route by name, applying env overrides (reads os.environ by default).

    Overrides: CLAIMFLOW_MODEL_<NAME>, CLAIMFLOW_MAX_TOKENS_<NAME>, CLAIMFLOW_EFFORT_<NAME>,
    where <NAME> is the upper-cased route name (e.g. STAGE2_RECOMMENDATION).
    """
    base = ROUTES[name]
    env: Mapping[str, str] = os.environ if settings_env is None else settings_env
    suffix = name.upper()
    model = env.get(f"CLAIMFLOW_MODEL_{suffix}") or base.model
    max_tokens_raw = env.get(f"CLAIMFLOW_MAX_TOKENS_{suffix}")
    max_tokens = int(max_tokens_raw) if max_tokens_raw else base.max_tokens
    effort = env.get(f"CLAIMFLOW_EFFORT_{suffix}") or base.effort
    return replace(base, model=model, max_tokens=max_tokens, effort=effort)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost estimate; unknown models cost 0.0 so accounting never crashes a stage."""
    prices = PRICES_PER_MTOK.get(model)
    if prices is None:
        return 0.0
    input_per_mtok, output_per_mtok = prices
    return (input_tokens * input_per_mtok + output_tokens * output_per_mtok) / 1_000_000
