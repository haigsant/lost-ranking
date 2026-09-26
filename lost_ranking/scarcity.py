"""Positional tiers and cliffs.

A cliff is an unusually large score drop between consecutive players at the
same position. Players just above a cliff are scarce: once they're gone, the
next option at that position is worse.

Two strengths:
  major - large against the whole position (e.g. the drop after Wembanyama at C)
  minor - large against the gaps around it, so groups keep splitting into
          tiers further down the list where every drop is smaller.
"""

from __future__ import annotations

import pandas as pd

from .config import LeagueSettings
from .positions import BASE_POSITIONS

FIELD = "ALL"  # tier group covering every player, regardless of position
MAD_TO_STD = 1.4826  # scales median absolute deviation to a normal-dist std
EPS = 1e-9  # float tolerance for threshold comparisons


def major_threshold(gaps: pd.Series, settings: LeagueSettings) -> float:
    """Robust outlier threshold: median + z * MAD-std, floored at cliff_min_gap.

    Median/MAD instead of mean/std so a few huge gaps at the top don't hide
    the smaller cliffs further down.
    """
    median = gaps.median()
    mad = (gaps - median).abs().median() * MAD_TO_STD
    return max(median + settings.cliff_z * mad, settings.cliff_min_gap)


def _tier_numbers(breaks: pd.Series) -> pd.Series:
    """Tier 1 until the first break, then +1 after each break."""
    return 1 + breaks.shift(fill_value=False).cumsum()


def _split_long_tiers(breaks: pd.Series, gaps: pd.Series, max_size: int) -> pd.Series:
    """Add breaks at the largest internal gap until no tier exceeds max_size."""
    breaks = breaks.copy()
    while True:
        tiers = _tier_numbers(breaks)
        sizes = tiers.value_counts()
        long = sizes[sizes > max_size].index
        if long.empty:
            return breaks
        for tier in long:
            members = tiers.index[tiers == tier]
            breaks[gaps[members[:-1]].idxmax()] = True


def classify_cliffs(gaps: pd.Series, in_pool: pd.Series, settings: LeagueSettings) -> pd.Series:
    """'major', 'minor' or '' for each gap (gap = this player's score - next player's)."""
    local = gaps.rolling(2 * settings.tier_window + 1, center=True, min_periods=1).median()
    has_gap = gaps.notna()
    minor = has_gap & (gaps >= settings.tier_min_gap) & (gaps >= settings.tier_ratio * local - EPS)

    major = pd.Series(False, index=gaps.index)
    pool_gaps = gaps[in_pool & has_gap]
    if len(pool_gaps):
        major = has_gap & (gaps >= major_threshold(pool_gaps, settings))

    breaks = _split_long_tiers(minor | major, gaps.fillna(0.0), settings.max_tier_size)
    strength = pd.Series("", index=gaps.index)
    strength[breaks] = "minor"
    strength[major] = "major"
    return strength


def position_tiers(df: pd.DataFrame, settings: LeagueSettings) -> pd.DataFrame:
    """Long table: one row per (player, group) with rank/gap/tier in that group.

    Groups are the whole field (pos == "ALL") and each eligible base position.
    Expects df sorted by score descending with 'positions' and 'drafted' columns.
    """
    frames = []
    for pos in (FIELD, *BASE_POSITIONS):
        in_group = df["positions"].map(lambda p, pos=pos: pos == FIELD or pos in p)
        at_pos = df.loc[in_group, ["player", "score", "drafted"]]
        if at_pos.empty:
            continue
        t = at_pos.assign(pos=pos, pos_rank=range(1, len(at_pos) + 1))
        t["next_player"] = t["player"].shift(-1)
        t["gap_to_next"] = t["score"] - t["score"].shift(-1)
        t["cliff_strength"] = classify_cliffs(t["gap_to_next"], t["drafted"], settings)
        t["cliff_after"] = t["cliff_strength"] != ""
        t["pos_tier"] = _tier_numbers(t["cliff_after"])
        t["left_in_tier"] = t.groupby("pos_tier").cumcount(ascending=False)
        frames.append(t.drop(columns=["player", "score", "drafted"]))
    return pd.concat(frames).rename_axis("player_idx").reset_index()


def _note(row: pd.Series) -> str:
    if not row["drafted"]:
        return ""
    pos, gap, nxt = row["scarce_pos"], row["gap_to_next"], row["next_player"]
    if row["cliff_strength"] == "major":
        return f"Last {pos} before major cliff: -{gap:.2f} to {nxt}"
    if row["cliff_strength"] == "minor":
        return f"Last {pos} in tier {row['pos_tier']}: -{gap:.2f} to {nxt}"
    if row["left_in_tier"] == 1:
        return f"1 {pos} left in tier {row['pos_tier']}"
    return ""


TIER_COLUMNS = ["pos_rank", "pos_tier", "gap_to_next", "next_player", "cliff_after", "cliff_strength", "left_in_tier"]


def add_scarcity(df: pd.DataFrame, tiers: pd.DataFrame) -> pd.DataFrame:
    """Attach overall tier, tier info at the scarcest position, and a readable note."""
    field = tiers[tiers["pos"] == FIELD].set_index("player_idx")
    df = df.join(field[["pos_tier"]].rename(columns={"pos_tier": "field_tier"}))
    at_scarce = tiers.merge(
        df[["scarce_pos"]].rename_axis("player_idx").reset_index(),
        left_on=["player_idx", "pos"],
        right_on=["player_idx", "scarce_pos"],
    ).set_index("player_idx")
    df = df.join(at_scarce[TIER_COLUMNS])
    df["cliff_after"] = df["cliff_after"].fillna(False).astype(bool)
    df["cliff_strength"] = df["cliff_strength"].fillna("")
    df["scarcity_note"] = df.apply(_note, axis=1)
    return df


def position_summary(
    df: pd.DataFrame, tiers: pd.DataFrame, levels: dict[str, float]
) -> pd.DataFrame:
    """One row per base position: depth, replacement level, tier count, major cliffs."""
    rows = []
    for pos in BASE_POSITIONS:
        t = tiers[tiers["pos"] == pos].join(df[["player", "drafted"]], on="player_idx")
        pool = t[t["drafted"]]
        majors = pool[pool["cliff_strength"] == "major"]
        rows.append(
            {
                "pos": pos,
                "eligible_drafted": len(pool),
                "tiers": int(pool["pos_tier"].max()) if len(pool) else 0,
                "replacement": round(levels[pos], 2),
                "vs_field": round(levels[pos] - levels["ALL"], 2),
                "major_cliffs": "; ".join(
                    f"after #{r.pos_rank} {r.player} (-{r.gap_to_next:.2f})"
                    for r in majors.itertuples()
                ),
            }
        )
    return pd.DataFrame(rows)
