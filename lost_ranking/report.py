"""Render a ValuationResult as a self-contained HTML auction board."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pandas as pd

from .pipeline import ValuationResult

TEMPLATE = files("lost_ranking") / "templates" / "board.html"
# Added when the page is opened straight from disk; hosts like claude.ai artifacts add their own.
STANDALONE_HEAD = (
    '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<style>body{margin:0}</style>\n"
)
PLAYER_FIELDS = [
    "overall_rank", "player", "pos", "team", "score", "auction_value", "field_value",
    "scarcity_premium", "scarce_pos", "pos_tier", "cliff_strength", "scarcity_note",
    "drafted", "source_url",
]
TIER_FIELDS = [
    "pos", "pos_rank", "pos_tier", "player", "team", "score", "auction_value",
    "drafted", "gap_to_next", "cliff_strength", "source_url",
]


def _records(df: pd.DataFrame, fields: list[str]) -> list[dict]:
    df = df[[f for f in fields if f in df.columns]].round(2)
    return df.astype(object).where(df.notna(), None).to_dict("records")


def _source(players: pd.DataFrame) -> tuple[str, str | None]:
    """(source name, source updated date) from the first player row."""
    first = players.iloc[0]
    updated = first.get("source_updated")
    return str(first.get("source", "")).title(), (str(updated) if pd.notna(updated) else None)


def build_payload(result: ValuationResult) -> dict:
    s = result.settings
    name, updated = _source(result.players)
    return {
        "title": f"{name} · source updated {updated}" if updated else name,
        "league": {
            "teams": s.teams,
            "budget": s.budget_per_team,
            "roster": s.roster,
            "field_replacement": round(result.replacement["ALL"], 2),
        },
        "players": _records(result.players, PLAYER_FIELDS),
        "positions": _records(result.positions, list(result.positions.columns)),
        "tiers": _records(result.tiers, TIER_FIELDS),
    }


def render_board(result: ValuationResult, standalone: bool = True) -> str:
    # Escape "</" so player data can never close the <script> tag.
    data = json.dumps(build_payload(result), ensure_ascii=False).replace("</", "<\\/")
    title = f"{_source(result.players)[0]} Auction Board".strip()
    html = TEMPLATE.read_text(encoding="utf-8").replace("__TITLE__", title).replace("__DATA__", data)
    return STANDALONE_HEAD + html if standalone else html


def write_board(result: ValuationResult, path: Path, standalone: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_board(result, standalone), encoding="utf-8")
    return path
