"""Render a ValuationResult as a self-contained HTML auction board.

The board is assembled from templates/: base.html (shell + shared styles),
common.js (helpers, header, page tabs), and one html/js (+ optional css)
per page listed in PAGES.
"""

from __future__ import annotations

import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pandas as pd

from .pipeline import ValuationResult
from .positions import SLOT_ELIGIBILITY
from .simulate import SimSettings, optimized_payload
from .strategy import Plan, build_strategy

TEMPLATES = files("lost_ranking") / "templates"
# (page key, tab label). Each key has templates/<key>.html and <key>.js, optionally <key>.css.
PAGES = (("values", "Values"), ("strategy", "Strategy"))
# Added when the page is opened straight from disk; hosts like claude.ai artifacts add their own.
STANDALONE_HEAD = (
    '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<style>body{margin:0}</style>\n"
)
PLAYER_FIELDS = [
    "overall_rank", "player", "pos", "team", "score", "auction_value", "market_price", "market_gap",
    "market_trend", "market_listed", "expected_price", "field_value",
    "scarcity_premium", "field_tier", "scarce_pos", "pos_tier", "cliff_strength", "scarcity_note",
    "drafted", "core", "source_url",
]
TIER_FIELDS = [
    "pos", "pos_rank", "pos_tier", "player", "team", "score", "auction_value", "market_price",
    "drafted", "core", "gap_to_next", "cliff_strength", "source_url",
]
PICK_FIELDS = ["slot", "player", "pos", "price", "value_price", "score", "field_tier"]
GAP_FIELDS = [
    "player", "pos", "team", "score", "auction_value", "market_price", "expected_price",
    "market_gap", "market_trend", "market_listed", "core", "field_tier",
]


def _records(df: pd.DataFrame, fields: list[str] | None = None) -> list[dict]:
    df = df[[f for f in fields if f in df.columns]] if fields else df
    df = df.round(2)
    return df.astype(object).where(df.notna(), None).to_dict("records")


def _source(players: pd.DataFrame) -> tuple[str, str | None]:
    """(source name, source updated date) from the first player row."""
    first = players.iloc[0]
    updated = first.get("source_updated")
    return str(first.get("source", "")).title(), (str(updated) if pd.notna(updated) else None)


def _plan_record(p: Plan) -> dict:
    return {
        "key": p.spec.key,
        "name": p.spec.name,
        "summary": p.spec.summary,
        "spend": p.spend,
        "worth": p.worth,
        "cost": p.spec.cost,
        "total_score": round(p.total_score, 2),
        "picks": _records(p.picks, PICK_FIELDS),
        "bench": _records(p.bench, PICK_FIELDS),
        "bench_spend": p.bench_spend,
    }


def _optimized_record(opt: dict | None) -> dict | None:
    if not opt:
        return None
    best = opt["best"]
    return {
        **opt,
        "best": {
            **{k: v for k, v in best.items() if k != "plan"},
            "star_targets": _records(best["star_targets"], ["player", "pos", "auction_value", "market_price", "price", "cap"]),
            "target_list": _records(best["target_list"]),
        },
    }


def _strategy_payload(result: ValuationResult, sim: SimSettings | None) -> dict:
    strategy = build_strategy(result.players, result.tiers, result.settings)
    plans = strategy.pop("plans")
    opt = optimized_payload(result.players, result.settings, sim) if sim and sim.scenarios else None
    if opt:
        # The simulation's winner leads the plan list, so the planner opens on it.
        best = opt["best"]["plan"]
        spec = replace(best.spec, key="optimized", name=f"Optimized: {opt['best']['name']}",
                       summary="The strategy that scored best across the simulated auctions. Targets at expected prices.")
        plans = [Plan(spec, best.picks, best.bench), *plans]
    return {
        **strategy,
        "optimized": _optimized_record(opt),
        "plans": [_plan_record(p) for p in plans],
        "long_shots": _records(strategy["long_shots"], ["player", "pos", "team", "score", "price", "value_price"]),
        "price_bands": [
            {**{k: v for k, v in band.items() if k != "players"}, "players": _records(band["players"], GAP_FIELDS)}
            for band in strategy["price_bands"]
        ] if strategy["price_bands"] else None,
        "tier_guide": _records(strategy["tier_guide"]),
        "market": {k: _records(v, GAP_FIELDS) for k, v in strategy["market"].items()} if strategy["market"] else None,
    }


def build_payload(result: ValuationResult, sim: SimSettings | None = None) -> dict:
    s = result.settings
    name, updated = _source(result.players)
    return {
        "title": f"{name} · source updated {updated}" if updated else name,
        "league": {
            "teams": s.teams,
            "budget": s.budget_per_team,
            "min_bid": s.min_bid,
            "roster": s.roster,
            "games_cap": s.games_cap,
            "core_size": s.core_size,
            "core_position_caps": s.core_position_caps,
            "field_replacement": round(result.replacement["ALL"], 2),
        },
        "slot_eligibility": {k: sorted(v) if v else None for k, v in SLOT_ELIGIBILITY.items()},
        "has_market": bool(result.players.get("market_price", pd.Series(dtype=float)).notna().any()),
        "players": _records(result.players, PLAYER_FIELDS),
        "positions": _records(result.positions),
        "tiers": _records(result.tiers, TIER_FIELDS),
        "strategy": _strategy_payload(result, sim),
    }


def _read(name: str) -> str:
    path = TEMPLATES / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def render_board(result: ValuationResult, standalone: bool = True, sim: SimSettings | None = None) -> str:
    # Escape "</" so player data can never close the <script> tag.
    data = json.dumps(build_payload(result, sim), ensure_ascii=False).replace("</", "<\\/")
    title = f"{_source(result.players)[0]} Auction Board".strip()
    tabs = "".join(f'<a href="#{key}" data-view="{key}">{label}</a>' for key, label in PAGES)
    scripts = "\n".join(f"<script>\n{_read(name)}</script>" for name in ["common.js", *(f"{k}.js" for k, _ in PAGES)])
    html = (
        _read("base.html")
        .replace("__TITLE__", title)
        .replace("__STYLES__", "\n".join(_read(f"{k}.css") for k, _ in PAGES))
        .replace("__TABS__", tabs)
        .replace("__VIEWS__", "\n".join(_read(f"{k}.html") for k, _ in PAGES))
        .replace("__SCRIPTS__", scripts)
        .replace("__DATA__", data)  # last, so player text is never scanned for placeholders
    )
    return STANDALONE_HEAD + html if standalone else html


def write_board(
    result: ValuationResult, path: Path, standalone: bool = True, sim: SimSettings | None = None
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_board(result, standalone, sim), encoding="utf-8")
    return path
