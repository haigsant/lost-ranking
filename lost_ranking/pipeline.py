"""End-to-end: CSV in -> valued player table (+ position summary) out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import LeagueSettings
from .loaders import load_market_prices, load_rankings
from .names import name_key
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
    "field_value",
    "scarcity_premium",
    "field_tier",
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

TIER_PLAYER_COLUMNS = ["player", "team", "score", "auction_value", "market_price", "drafted", "core", "source_url"]


@dataclass
class ValuationResult:
    players: pd.DataFrame
    positions: pd.DataFrame
    # One row per (player, eligible position): rank, tier and cliff at that position.
    tiers: pd.DataFrame
    replacement: dict[str, float]
    settings: LeagueSettings


def add_market(df: pd.DataFrame, market: pd.DataFrame, min_bid: int) -> pd.DataFrame:
    """Join market prices by name. market_gap > 0 means our value is above the market (a bargain).

    Market lists only include players who go for at least the minimum, so a drafted
    player missing from the list is priced at min_bid (market_listed = False).
    """
    df = df.assign(name_key=df["player"].map(name_key)).merge(market, on="name_key", how="left").drop(columns="name_key")
    df["market_listed"] = df["market_price"].notna()
    df["market_price"] = df["market_price"].mask(~df["market_listed"] & df["drafted"], float(min_bid))
    df["market_gap"] = df["auction_value"] - df["market_price"]
    return df


def run(
    path: str | Path,
    settings: LeagueSettings,
    source: str = "dynatyze",
    market_path: str | Path | None = None,
    market_source: str = "espn",
) -> ValuationResult:
    df = load_rankings(path, source)
    df, levels = value_players(df, settings)
    if market_path:
        df = add_market(df, load_market_prices(market_path, market_source), settings.min_bid)
    tiers = position_tiers(df, settings)
    df = add_scarcity(df, tiers)
    columns = [c for c in OUTPUT_COLUMNS if c in df.columns]
    return ValuationResult(
        players=df[columns],
        positions=position_summary(df, tiers, levels),
        tiers=tiers.join(df[[c for c in TIER_PLAYER_COLUMNS if c in df.columns]], on="player_idx"),
        replacement=levels,
        settings=settings,
    )
