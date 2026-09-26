"""End-to-end: CSV in -> valued player table (+ position summary) out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import LeagueSettings
from .loaders import load_rankings
from .scarcity import add_scarcity, position_summary, position_tiers
from .valuation import value_players

OUTPUT_COLUMNS = [
    "overall_rank",
    "player",
    "pos",
    "team",
    "score",
    "auction_value",
    "field_value",
    "scarcity_premium",
    "scarce_pos",
    "pos_rank",
    "pos_tier",
    "left_in_tier",
    "gap_to_next",
    "cliff_after",
    "scarcity_note",
    "field_vorp",
    "pos_vorp",
    "pos_replacement",
    "drafted",
    "draft_slot",
    "source",
    "source_rank",
    "source_id",
    "source_url",
    "source_updated",
]


@dataclass
class ValuationResult:
    players: pd.DataFrame
    positions: pd.DataFrame
    replacement: dict[str, float]


def run(path: str | Path, settings: LeagueSettings, source: str = "dynatyze") -> ValuationResult:
    df = load_rankings(path, source)
    df, levels = value_players(df, settings)
    tiers = position_tiers(df, settings)
    df = add_scarcity(df, tiers)
    columns = [c for c in OUTPUT_COLUMNS if c in df.columns]
    return ValuationResult(
        players=df[columns],
        positions=position_summary(df, tiers, levels),
        replacement=levels,
    )
