"""Turn a score column into auction dollars.

Method (standard value-over-replacement auction pricing, adjusted for a games cap):
  1. Simulate the league draft twice, filling slots with the best available
     eligible player, most-restrictive slot first:
       - the full rosters -> who gets drafted at all
       - the core only (starters + enough bench to use the games cap)
         -> whose games actually count
  2. Replacement level = the best players outside the core, both for the whole
     field and for each base position. With a games cap, a bench player's games
     mostly don't count, so the core's edge over them is what wins categories.
     Starter scarcity: a position whose last starter is weak (e.g. the 15th-best C
     vs the 15th-best PG) gets a score edge, since good players there run out first.
  3. VORP = score - replacement.
  4. Budget splits into a core pool and a small bench pool (bench_budget per
     team). Every drafted player costs at least min_bid; the rest of each pool
     is split in proportion to positive VORP within it. Bench VORP is measured
     against the last player drafted.

Two prices come out, so they can be compared:
  field_value - vs. replacement at any position
  pos_value   - vs. replacement at the player's scarcest position
scarcity_premium = pos_value - field_value.
"""

from __future__ import annotations

import pandas as pd

from .config import LeagueSettings
from .positions import BASE_POSITIONS, SLOT_ELIGIBILITY, base_positions, can_fill, slot_priority


def simulate_draft(df: pd.DataFrame, slots: dict[str, int]) -> pd.Series:
    """Return the slot each player fills from the league-wide `slots` (None if not taken).

    Expects df sorted by score descending with a 'positions' column.
    """
    open_slots = dict(slots)
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
    df: pd.DataFrame, pool: pd.Series, settings: LeagueSettings
) -> dict[str, float]:
    """Replacement score for the whole field ('ALL') and each base position.

    Replacement = the best players outside `pool` (the valued players).
    """
    outside = df.loc[~pool]
    last_in_pool = float(df.loc[pool, "score"].min())
    levels = {"ALL": _replacement(outside["score"], last_in_pool, settings.replacement_depth)}

    for pos in BASE_POSITIONS:
        eligible = df["positions"].map(lambda p, pos=pos: pos in p)
        in_pool = df.loc[eligible & pool, "score"]
        fallback = float(in_pool.min()) if len(in_pool) else levels["ALL"]
        levels[pos] = _replacement(
            df.loc[eligible & ~pool, "score"], fallback, settings.replacement_depth
        )
    return levels


def _split(pool_dollars: float, weights: pd.Series, members: pd.Series, floor: float) -> pd.Series:
    """`floor` per member + the rest of pool_dollars split by positive weight."""
    surplus = pool_dollars - floor * int(members.sum())
    positive = weights.where(members, 0.0).clip(lower=0.0)
    share = positive / positive.sum() if positive.sum() > 0 else members / max(int(members.sum()), 1)
    return (floor + surplus * share).where(members, 0.0)


def to_dollars(
    vorp: pd.Series, scores: pd.Series, core: pd.Series, drafted: pd.Series, settings: LeagueSettings
) -> pd.Series:
    """Bench pool split by score above the last drafted player; core pool split by core VORP.

    A core player never costs less than the priciest bench player.
    """
    bench = drafted & ~core
    last_drafted = scores[drafted].min()
    bench_dollars = _split(settings.teams * settings.team_bench_budget, scores - last_drafted, bench, settings.min_bid)
    core_floor = max(float(bench_dollars.max()), settings.min_bid)
    core_dollars = _split(settings.teams * settings.core_budget, vorp, core, core_floor)
    return core_dollars + bench_dollars


def starter_demand(settings: LeagueSettings) -> dict[str, float]:
    """League-wide starting spots per base position. A slot open to several base positions
    (G, PF/C) is split evenly between them; UTIL and bench are open to everyone and skipped."""
    demand = dict.fromkeys(BASE_POSITIONS, 0.0)
    for slot, n in settings.roster.items():
        allowed = [p for p in BASE_POSITIONS if SLOT_ELIGIBILITY[slot] and p in SLOT_ELIGIBILITY[slot]]
        for pos in allowed:
            demand[pos] += n * settings.teams / len(allowed)
    return demand


def starter_levels(df: pd.DataFrame, settings: LeagueSettings) -> dict[str, float]:
    """Score of the last starter at each position: the Nth-best eligible player, N = its demand."""
    levels = {}
    for pos, n in starter_demand(settings).items():
        eligible = df.loc[df["positions"].map(lambda p, pos=pos: pos in p), "score"]
        if n and len(eligible):
            levels[pos] = float(eligible.iloc[min(round(n), len(eligible)) - 1])
    return levels


def position_edge(levels: dict[str, float], starters: dict[str, float], weight: float) -> dict[str, float]:
    """Score bonus per position vs. the field (positive = scarce).

    Bench depth: how much worse the position's replacement is than the field's.
    Starter depth: how much worse its last starter is than the average position's.
    """
    mean_starter = sum(starters.values()) / len(starters) if starters else 0.0
    return {
        pos: (levels["ALL"] - levels[pos]) + weight * (mean_starter - starters.get(pos, mean_starter))
        for pos in BASE_POSITIONS
    }


def _scarcest_position(positions: frozenset[str], edge: dict[str, float]) -> str:
    """Eligible base position with the biggest scarcity edge ('UTIL' if none)."""
    eligible = base_positions(positions)
    return max(eligible, key=edge.__getitem__) if eligible else "UTIL"


def value_players(
    df: pd.DataFrame, settings: LeagueSettings
) -> tuple[pd.DataFrame, dict[str, float], dict[str, float]]:
    """Add draft, replacement, VORP and dollar columns.

    Returns (df, replacement levels, starter levels).
    """
    df = df.sort_values("score", ascending=False, ignore_index=True)
    roster_slot = simulate_draft(df, settings.league_slots())
    core_slot = simulate_draft(df, settings.league_slots(settings.core_roster()))
    drafted = roster_slot.notna()
    core = core_slot.notna() & drafted
    df["drafted"] = drafted
    df["core"] = core
    df["draft_slot"] = core_slot.where(core, roster_slot)

    levels = replacement_levels(df, core, settings)
    starters = starter_levels(df, settings)
    edge = position_edge(levels, starters, settings.starter_scarcity_weight)
    df["scarce_pos"] = df["positions"].map(lambda p: _scarcest_position(p, edge))
    df["pos_edge"] = df["scarce_pos"].map(lambda p: edge.get(p, 0.0))
    df["pos_replacement"] = levels["ALL"] - df["pos_edge"]

    df["field_vorp"] = df["score"] - levels["ALL"]
    df["pos_vorp"] = df["score"] - df["pos_replacement"]
    df["field_value"] = to_dollars(df["field_vorp"], df["score"], core, drafted, settings)
    df["pos_value"] = to_dollars(df["pos_vorp"], df["score"], core, drafted, settings)
    df["scarcity_premium"] = df["pos_value"] - df["field_value"]
    df["auction_value"] = df["pos_value"]
    df["overall_rank"] = range(1, len(df) + 1)
    return df, levels, starters
