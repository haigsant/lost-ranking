"""Ingest ranking sources into a canonical player table.

Every source is mapped to the same column names so downstream code never
cares where the data came from. Add a new source by adding a column map.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .positions import parse_positions

# Canonical columns: player, pos, team, score (+ optional extras).
SOURCE_COLUMN_MAPS: dict[str, dict[str, str]] = {
    "dynatyze": {
        "Rank": "source_rank",
        "Player": "player",
        "Pos": "pos",
        "Team": "team",
        "ROS Score": "score",
        "Dynatyze ID": "source_id",
        "Player URL": "source_url",
        "Source Updated": "source_updated",
    },
}

REQUIRED_COLUMNS = ("player", "pos", "score")


def load_rankings(path: str | Path, source: str = "dynatyze") -> pd.DataFrame:
    """Read a ranking CSV and return it with canonical columns and parsed positions."""
    column_map = SOURCE_COLUMN_MAPS[source]
    df = pd.read_csv(path).rename(columns=column_map)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}")

    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    dropped = df["score"].isna().sum()
    if dropped:
        print(f"warning: dropped {dropped} rows with no numeric score")
    df = df.dropna(subset=["score"])

    df["positions"] = df["pos"].map(parse_positions)
    df["source"] = source
    return df.sort_values("score", ascending=False, ignore_index=True)
