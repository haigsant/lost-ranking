"""Positional tiers and cliffs.

A cliff is an unusually large score drop between consecutive players at the
same position. Players just above a cliff are scarce: once they're gone, the
next option at that position is much worse.
"""

from __future__ import annotations

import pandas as pd

from .config import LeagueSettings
from .positions import BASE_POSITIONS

MAD_TO_STD = 1.4826  # scales median absolute deviation to a normal-dist std


def cliff_threshold(gaps: pd.Series, settings: LeagueSettings) -> float:
    """Robust outlier threshold: median + z * MAD-std, floored at cliff_min_gap.

    Median/MAD instead of mean/std so a few huge gaps at the top (e.g. an
    MVP-level player) don't hide the smaller cliffs further down.
    """
    median = gaps.median()
    mad = (gaps - median).abs().median() * MAD_TO_STD
    return max(median + settings.cliff_z * mad, settings.cliff_min_gap)


def position_tiers(df: pd.DataFrame, settings: LeagueSettings) -> pd.DataFrame:
    """Long table: one row per (player, eligible base position) with rank/gap/tier.

    Expects df sorted by score descending with 'positions' and 'drafted' columns.
    """
    frames = []
    for pos in BASE_POSITIONS:
        at_pos = df.loc[df["positions"].map(lambda p, pos=pos: pos in p), ["player", "score", "drafted"]]
        if at_pos.empty:
            continue
        t = at_pos.assign(pos=pos, pos_rank=range(1, len(at_pos) + 1))
        t["next_player"] = t["player"].shift(-1)
        t["gap_to_next"] = t["score"] - t["score"].shift(-1)

        # Only cliffs inside the draftable pool matter for auction pricing.
        in_pool = t["drafted"] & t["gap_to_next"].notna()
        threshold = cliff_threshold(t.loc[in_pool, "gap_to_next"], settings)
        t["cliff_after"] = in_pool & (t["gap_to_next"] >= threshold)

        t["pos_tier"] = 1 + t["cliff_after"].shift(fill_value=False).cumsum()
        t["left_in_tier"] = t.groupby("pos_tier").cumcount(ascending=False)
        frames.append(t.drop(columns=["player", "score", "drafted"]))
    return pd.concat(frames).rename_axis("player_idx").reset_index()


def _note(row: pd.Series) -> str:
    if not row["drafted"]:
        return ""
    pos = row["scarce_pos"]
    if row["cliff_after"]:
        return f"Last {pos} before cliff: -{row['gap_to_next']:.2f} to {row['next_player']}"
    if row["left_in_tier"] == 1:
        return f"1 {pos} left in tier {row['pos_tier']}"
    return ""


def add_scarcity(df: pd.DataFrame, tiers: pd.DataFrame) -> pd.DataFrame:
    """Attach each player's tier info at their scarcest position + a readable note."""
    at_scarce = tiers.merge(
        df[["scarce_pos"]].rename_axis("player_idx").reset_index(),
        left_on=["player_idx", "pos"],
        right_on=["player_idx", "scarce_pos"],
    ).set_index("player_idx")
    cols = ["pos_rank", "pos_tier", "gap_to_next", "next_player", "cliff_after", "left_in_tier"]
    df = df.join(at_scarce[cols])
    df["cliff_after"] = df["cliff_after"].fillna(False).astype(bool)
    df["scarcity_note"] = df.apply(_note, axis=1)
    return df


def position_summary(
    df: pd.DataFrame, tiers: pd.DataFrame, levels: dict[str, float]
) -> pd.DataFrame:
    """One row per base position: depth, replacement level, and where cliffs fall."""
    rows = []
    for pos in BASE_POSITIONS:
        t = tiers[tiers["pos"] == pos].join(df["player"], on="player_idx")
        cliffs = t[t["cliff_after"]]
        rows.append(
            {
                "pos": pos,
                "eligible_drafted": int(df.loc[t["player_idx"], "drafted"].sum()),
                "replacement": round(levels[pos], 2),
                "vs_field": round(levels[pos] - levels["ALL"], 2),
                "cliffs": "; ".join(
                    f"after #{r.pos_rank} {r.player} (-{r.gap_to_next:.2f})"
                    for r in cliffs.itertuples()
                ),
            }
        )
    return pd.DataFrame(rows)
