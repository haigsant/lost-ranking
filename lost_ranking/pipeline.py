"""End-to-end: CSV in -> valued player table (+ position summary) out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import LeagueSettings
from .loaders import load_market_prices, load_rankings
from .market import add_market, star_labels
from .scarcity import add_scarcity, position_summary, position_tiers
from .valuation import value_players

OUTPUT_COLUMNS = [
    "overall_rank",
    "player",
    "pos",
    "team",
    "score",
    "auction_value",
    "market_price",
    "market_gap",
    "market_trend",
    "market_listed",
    "expected_price",
    "field_value",
    "scarcity_premium",
    "field_tier",
    "star",
    "star_label",
    "scarce_pos",
    "pos_rank",
    "pos_tier",
    "left_in_tier",
    "gap_to_next",
    "cliff_after",
    "cliff_strength",
    "scarcity_note",
    "field_vorp",
    "pos_vorp",
    "pos_edge",
    "pos_replacement",
    "drafted",
    "core",
    "draft_slot",
    "source",
    "source_rank",
    "source_id",
    "source_url",
    "source_updated",
]

TIER_PLAYER_COLUMNS = [
    "player", "team", "score", "auction_value", "market_price", "drafted", "core", "star", "star_label", "source_url",
]


@dataclass
class ValuationResult:
    players: pd.DataFrame
    positions: pd.DataFrame
    # One row per (player, eligible position): rank, tier and cliff at that position.
    tiers: pd.DataFrame
    replacement: dict[str, float]
    # Score of the last starter at each base position (see valuation.starter_levels).
    starter_levels: dict[str, float]
    settings: LeagueSettings


def run(
    path: str | Path,
    settings: LeagueSettings,
    source: str = "dynatyze",
    market_path: str | Path | None = None,
    market_source: str = "espn",
) -> ValuationResult:
    df = load_rankings(path, source)
    df, levels, starters = value_players(df, settings)
    if market_path:
        df = add_market(df, load_market_prices(market_path, market_source), settings)
    tiers = position_tiers(df, settings)
    df = add_scarcity(df, tiers, settings)
    df["star_label"] = star_labels(df, settings)
    columns = [c for c in OUTPUT_COLUMNS if c in df.columns]
    return ValuationResult(
        players=df[columns],
        positions=position_summary(df, tiers, levels, starters),
        tiers=tiers.join(df[[c for c in TIER_PLAYER_COLUMNS if c in df.columns]], on="player_idx"),
        replacement=levels,
        starter_levels=starters,
        settings=settings,
    )
