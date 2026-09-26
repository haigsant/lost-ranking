"""Turn a score column into auction dollars.

Method (standard value-over-replacement auction pricing):
  1. Simulate the league draft: fill every roster slot across all teams with
     the best available eligible player, most-restrictive slot first.
  2. Replacement level = what's left on waivers (best undrafted players),
     both for the whole field and for each base position.
  3. VORP = score - replacement.
  4. Every drafted player costs at least min_bid; the remaining budget is
     split in proportion to positive VORP.

Two prices come out, so they can be compared:
  field_value - vs. the best undrafted player at any position
  pos_value   - vs. the best undrafted player at the player's scarcest position
scarcity_premium = pos_value - field_value.
"""

from __future__ import annotations

import pandas as pd

from .config import LeagueSettings
from .positions import BASE_POSITIONS, base_positions, can_fill, slot_priority


def simulate_draft(df: pd.DataFrame, settings: LeagueSettings) -> pd.Series:
    """Return the roster slot each player fills (None if undrafted).

    Expects df sorted by score descending with a 'positions' column.
    """
    open_slots = settings.league_slots()
    order = slot_priority(list(open_slots))
    assigned: list[str | None] = []

    for positions in df["positions"]:
        slot = next(
            (s for s in order if open_slots[s] > 0 and can_fill(s, positions)),
            None,
        )
        if slot:
            open_slots[slot] -= 1
        assigned.append(slot)

    return pd.Series(assigned, index=df.index, name="draft_slot")


def _replacement(scores: pd.Series, fallback: float, depth: int) -> float:
    """Mean of the top `depth` scores, or fallback if none are available."""
    top = scores.nlargest(depth)
    return float(top.mean()) if len(top) else fallback


def replacement_levels(
    df: pd.DataFrame, drafted: pd.Series, settings: LeagueSettings
) -> dict[str, float]:
    """Replacement score for the whole field ('ALL') and each base position."""
    undrafted = df.loc[~drafted]
    last_drafted = float(df.loc[drafted, "score"].min())
    levels = {"ALL": _replacement(undrafted["score"], last_drafted, settings.replacement_depth)}

    for pos in BASE_POSITIONS:
        eligible = df["positions"].map(lambda p, pos=pos: pos in p)
        pool = df.loc[eligible & drafted, "score"]
        fallback = float(pool.min()) if len(pool) else levels["ALL"]
        levels[pos] = _replacement(
            df.loc[eligible & ~drafted, "score"], fallback, settings.replacement_depth
        )
    return levels


def to_dollars(vorp: pd.Series, drafted: pd.Series, settings: LeagueSettings) -> pd.Series:
    """Min bid for every drafted player + surplus budget split by positive VORP."""
    surplus = settings.total_budget - settings.min_bid * int(drafted.sum())
    positive = vorp.where(drafted, 0.0).clip(lower=0.0)
    dollars = settings.min_bid + surplus * positive / positive.sum()
    return dollars.where(drafted, 0.0)


def _scarcest_position(positions: frozenset[str], levels: dict[str, float]) -> str:
    """Eligible base position with the lowest replacement level ('UTIL' if none)."""
    eligible = base_positions(positions)
    return min(eligible, key=levels.__getitem__) if eligible else "UTIL"


def value_players(df: pd.DataFrame, settings: LeagueSettings) -> tuple[pd.DataFrame, dict[str, float]]:
    """Add draft, replacement, VORP and dollar columns. Returns (df, replacement levels)."""
    df = df.sort_values("score", ascending=False, ignore_index=True)
    df["draft_slot"] = simulate_draft(df, settings)
    drafted = df["draft_slot"].notna()
    df["drafted"] = drafted

    levels = replacement_levels(df, drafted, settings)
    df["scarce_pos"] = df["positions"].map(lambda p: _scarcest_position(p, levels))
    df["pos_replacement"] = df["scarce_pos"].map(lambda p: levels.get(p, levels["ALL"]))

    df["field_vorp"] = df["score"] - levels["ALL"]
    df["pos_vorp"] = df["score"] - df["pos_replacement"]
    df["field_value"] = to_dollars(df["field_vorp"], drafted, settings)
    df["pos_value"] = to_dollars(df["pos_vorp"], drafted, settings)
    df["scarcity_premium"] = df["pos_value"] - df["field_value"]
    df["auction_value"] = df["pos_value"]
    df["overall_rank"] = range(1, len(df) + 1)
    return df, levels
