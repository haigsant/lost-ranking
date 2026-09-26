"""Auction strategy: budget split, sample roster plans, and tier buying guidance.

The games cap means only the core (about games_cap / games_per_player players
per team) produces stats that count. So the plan is: spend almost everything on
the core, and fill the rest of the roster with minimum-bid long shots.

Roster plans are solved as a small integer program: pick one player per core
slot (each player fits only slots their positions allow), stay within the core
budget, and maximize total score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from .config import BENCH, LeagueSettings
from .positions import can_fill, parse_positions

STARTER_NUDGE = 1e-3


@dataclass(frozen=True)
class PlanSpec:
    key: str
    name: str
    summary: str
    # Exactly `stars` core players priced at or above star_price (None = no rule).
    stars: int = 0
    star_price: int | None = None
    # Tiny tie-break that spreads leftover money evenly; at fair prices many rosters
    # score about the same, and this picks the sensible one.
    evenness: float = 0.01
    # What a player costs: "value" (our fair price) or "market" (e.g. ESPN average price).
    cost: str = "value"


PLAN_SPECS = (
    PlanSpec(
        "superstar",
        "One superstar + depth",
        "Win one of the three elite players, then spread the rest evenly across the other nine core spots.",
        stars=1,
        star_price=75,
    ),
    PlanSpec(
        "two_stars",
        "Two stars + depth",
        "Skip the top three. Buy two players from the next group, then fill evenly.",
        stars=2,
        star_price=40,
    ),
    PlanSpec(
        "balanced",
        "Balanced ten",
        "Nobody over $40. Ten solid players and no star.",
        stars=0,
        star_price=40,
    ),
    PlanSpec(
        "max_score",
        "Max score at value",
        "No rules except the center limit: the highest core score the budget buys at our prices. This is the ceiling.",
        evenness=0.0,
    ),
)
MARKET_PLAN = PlanSpec(
    "market",
    "Best buys at market prices",
    "Same search, but each player costs what the market pays on average. Where the market underrates a player, the budget stretches further.",
    evenness=0.0,
    cost="market",
)


@dataclass
class Plan:
    spec: PlanSpec
    picks: pd.DataFrame = field(repr=False)

    @property
    def spend(self) -> int:
        return int(self.picks["price"].sum())

    @property
    def total_score(self) -> float:
        return float(self.picks["score"].sum())

    @property
    def worth(self) -> int:
        """What the picks cost at our values; above spend means the plan buys under value."""
        return int(self.picks["value_price"].sum())


def core_slots(settings: LeagueSettings) -> list[str]:
    """One team's core slots, one entry per spot (e.g. ['PG', ..., 'BN', 'BN'])."""
    return [slot for slot, n in settings.core_roster().items() for _ in range(n)]


def bid_price(values: pd.Series, settings: LeagueSettings) -> pd.Series:
    """Whole-dollar price to plan with: value rounded, never below the minimum bid."""
    return values.round().clip(lower=settings.min_bid).astype(int)


def has_market(players: pd.DataFrame) -> bool:
    return "market_price" in players.columns and players["market_price"].notna().any()


def plan_cost(players: pd.DataFrame, spec: PlanSpec) -> pd.Series:
    """Dollars to plan with. Market plans fall back to our value where no market price exists."""
    if spec.cost == "market":
        return players["market_price"].fillna(players["auction_value"])
    return players["auction_value"]


def solve_plan(players: pd.DataFrame, settings: LeagueSettings, spec: PlanSpec) -> Plan:
    """Best core roster for one plan spec. players needs player, pos, score, auction_value."""
    pool = players.assign(
        positions=players["pos"].map(parse_positions),
        price=bid_price(plan_cost(players, spec), settings),
        value_price=bid_price(players["auction_value"], settings),
    ).reset_index(drop=True)
    slots = core_slots(settings)

    # One binary variable per (player, slot) pair the player can fill.
    pairs = [(i, j) for i, pos in enumerate(pool["positions"]) for j, s in enumerate(slots) if can_fill(s, pos)]
    n = len(pairs)
    score = pool["score"].to_numpy()
    weight = score - spec.evenness * score**2
    # Nudge the best players into starting slots instead of the bench.
    objective = -np.array([weight[i] + STARTER_NUDGE * score[i] * (slots[j] != BENCH) for i, j in pairs])

    slot_rows = np.zeros((len(slots), n))
    player_rows = np.zeros((len(pool), n))
    for k, (i, j) in enumerate(pairs):
        slot_rows[j, k] = 1
        player_rows[i, k] = 1
    price_row = np.array([[pool.at[i, "price"] for i, _ in pairs]])
    constraints = [
        LinearConstraint(slot_rows, 1, 1),  # every core slot filled once
        LinearConstraint(player_rows, 0, 1),  # a player fills at most one slot
        LinearConstraint(price_row, 0, settings.core_budget),
    ]
    if spec.star_price is not None:
        is_star = (pool["price"] >= spec.star_price).to_numpy()
        constraints.append(LinearConstraint([[float(is_star[i]) for i, _ in pairs]], spec.stars, spec.stars))
    caps = list(settings.core_position_caps.items())
    if caps:
        cap_rows = [[float(pos in pool.at[i, "positions"]) for i, _ in pairs] for pos, _ in caps]
        constraints.append(LinearConstraint(cap_rows, 0, [cap for _, cap in caps]))

    result = milp(
        objective,
        integrality=np.ones(n),
        bounds=Bounds(0, 1),
        constraints=constraints,
    )
    if not result.success:
        raise ValueError(f"no feasible roster for plan {spec.key!r}: {result.message}")

    chosen = [pairs[k] for k in np.flatnonzero(result.x > 0.5)]
    picks = pool.loc[[i for i, _ in chosen]].assign(slot=[slots[j] for _, j in chosen])
    order = {s: k for k, s in enumerate(dict.fromkeys(slots))}
    picks = picks.sort_values(["slot", "price"], key=lambda c: c.map(order) if c.name == "slot" else -c)
    return Plan(spec, picks.drop(columns="positions"))


def long_shots(players: pd.DataFrame, settings: LeagueSettings, exclude: set[str], n: int) -> pd.DataFrame:
    """Best-scored bench-priced players (outside the core pool)."""
    cheap = players[players["drafted"] & ~players["core"] & ~players["player"].isin(exclude)]
    return cheap.head(n).assign(price=bid_price(cheap.head(n)["auction_value"], settings))


def tier_guide(players: pd.DataFrame, tiers: pd.DataFrame) -> pd.DataFrame:
    """One row per field tier in the core: price range, depth, and the drop after it."""
    field_rows = tiers[tiers["pos"] == "ALL"].set_index("player_idx")
    df = players.join(field_rows[["gap_to_next", "cliff_strength"]].add_prefix("field_"), how="left")
    core = df[df["core"]]
    rows = []
    for tier, g in core.groupby("field_tier", sort=True):
        last = g.iloc[-1]
        rows.append(
            {
                "tier": int(tier),
                "players": ", ".join(g["player"]),
                "count": len(g),
                "high": round(float(g["auction_value"].max()), 1),
                "low": round(float(g["auction_value"].min()), 1),
                "drop_after": round(float(last["field_gap_to_next"]), 2) if pd.notna(last["field_gap_to_next"]) else None,
                "cliff": last["field_cliff_strength"] or "",
                "advice": _tier_advice(len(g), last["field_cliff_strength"]),
            }
        )
    return pd.DataFrame(rows)


def _tier_advice(count: int, cliff: str) -> str:
    """Few substitutes + a big drop after = pay up; many near-equal players = wait."""
    if cliff == "major":
        return "Pay up"
    if count >= 5:
        return "Deep: wait"
    return "Fair price"


def market_gaps(players: pd.DataFrame, n: int = 12) -> dict[str, pd.DataFrame]:
    """Core players the market prices furthest below (bargains) and above (overpriced) our value."""
    priced = players[players["core"] & players["market_price"].notna()]
    return {
        "bargains": priced.nlargest(n, "market_gap"),
        "overpriced": priced.nsmallest(n, "market_gap"),
    }


def build_strategy(players: pd.DataFrame, tiers: pd.DataFrame, settings: LeagueSettings) -> dict:
    specs = PLAN_SPECS + ((MARKET_PLAN,) if has_market(players) else ())
    plans = [solve_plan(players[players["core"]], settings, spec) for spec in specs]
    taken = set().union(*(set(p.picks["player"]) for p in plans))
    bench_spots = settings.bench_spots
    return {
        "core_size": settings.core_size,
        "bench_spots": bench_spots,
        "core_budget": settings.core_budget,
        "bench_budget": settings.team_bench_budget,
        "games_cap": settings.games_cap,
        "games_per_player": settings.games_per_player,
        "core_slots": core_slots(settings),
        "roster_slots": [s for s, n in settings.roster.items() for _ in range(n)],
        "plans": plans,
        "long_shots": long_shots(players, settings, taken, max(bench_spots, 8)),
        "tier_guide": tier_guide(players, tiers),
        "market": market_gaps(players) if has_market(players) else None,
        "bench": BENCH,
    }
