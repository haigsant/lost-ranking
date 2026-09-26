"""Auction strategy: budget split, sample roster plans, and tier buying guidance.

The games cap means only the core (about games_cap / games_per_player players
per team) produces stats that count. So the plan is: spend almost everything on
the core, and fill the rest of the roster with minimum-bid long shots.

Roster plans are solved as a small integer program: pick one player per core
slot (each player fits only slots their positions allow), stay within the core
budget, and maximize total score.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from .config import BENCH, LeagueSettings
from .market import is_star
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
    # What a player costs: "value" (our fair price), "market" (average auction price),
    # or "expected" (market, with top talent at a premium; see market.expected_price).
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
MARKET_PLANS = (
    PlanSpec(
        "expected",
        "Pay up for stars, steal the rest",
        "Players the market prices as stars cost 10% over their average, because that's what it takes to win them. Everyone else costs the market price, so the budget flows to players the room underrates.",
        evenness=0.0,
        cost="expected",
    ),
    PlanSpec(
        "market",
        "Everyone at market price",
        "Each player costs the market average, stars included. The optimistic case: it assumes you win stars at their average price.",
        evenness=0.0,
        cost="market",
    ),
)
COST_COLUMNS = {"value": "auction_value", "market": "market_price", "expected": "expected_price"}


@dataclass
class Plan:
    spec: PlanSpec
    picks: pd.DataFrame = field(repr=False)
    bench: pd.DataFrame = field(repr=False)

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

    @property
    def bench_spend(self) -> int:
        return int(self.bench["price"].sum())


def core_slots(settings: LeagueSettings) -> list[str]:
    """One team's core slots, one entry per spot (e.g. ['PG', ..., 'BN', 'BN'])."""
    return [slot for slot, n in settings.core_roster().items() for _ in range(n)]


def bid_price(values: pd.Series, settings: LeagueSettings) -> pd.Series:
    """Whole-dollar price to plan with: value rounded, never below the minimum bid."""
    return values.round().clip(lower=settings.min_bid).astype(int)


def has_market(players: pd.DataFrame) -> bool:
    return "market_price" in players.columns and players["market_price"].notna().any()


def plan_cost(players: pd.DataFrame, spec: PlanSpec) -> pd.Series:
    """Dollars to plan with. Market-based costs fall back to our value where no market
    price exists (only undrafted players; drafted players missing from the list cost min_bid)."""
    return players[COST_COLUMNS[spec.cost]].fillna(players["auction_value"])


def solve_roster(
    players: pd.DataFrame,
    settings: LeagueSettings,
    spec: PlanSpec,
    slots: list[str],
    budget: int,
    position_caps: dict[str, int],
) -> pd.DataFrame:
    """Best set of players for `slots` within `budget`. players needs player, pos, score, auction_value."""
    pool = players.assign(
        positions=players["pos"].map(parse_positions),
        price=bid_price(plan_cost(players, spec), settings),
        value_price=bid_price(players["auction_value"], settings),
    ).reset_index(drop=True)

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
        LinearConstraint(price_row, 0, budget),
    ]
    if spec.star_price is not None:
        is_star = (pool["price"] >= spec.star_price).to_numpy()
        constraints.append(LinearConstraint([[float(is_star[i]) for i, _ in pairs]], spec.stars, spec.stars))
    caps = list(position_caps.items())
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
    return picks.drop(columns="positions")


def solve_plan(players: pd.DataFrame, settings: LeagueSettings, spec: PlanSpec) -> Plan:
    """Core from the core pool within the core budget, then the best bench the bench budget buys.

    The bench may include core-pool players the market lets go cheap.
    """
    core = solve_roster(
        players[players["core"]], settings, spec, core_slots(settings), settings.core_budget, settings.core_position_caps
    )
    bench = pd.DataFrame(columns=core.columns)
    if settings.bench_spots:
        candidates = players[players["drafted"] & ~players["player"].isin(core["player"])]
        bench_spec = replace(spec, stars=0, star_price=None, evenness=0.0)
        bench = solve_roster(
            candidates, settings, bench_spec, [BENCH] * settings.bench_spots, settings.team_bench_budget, {}
        )
    return Plan(spec, core, bench)


def price_bands(players: pd.DataFrame, settings: LeagueSettings, n: int = 10) -> list[dict]:
    """Where to hunt: stars and what they'll cost, then the best buys in each market-price band."""
    drafted = players[players["drafted"] & players["market_price"].notna()]
    stars = drafted[is_star(drafted, settings)].sort_values("auction_value", ascending=False)
    bands = [{"label": f"Stars (${settings.star_price}+)", "kind": "stars", "players": stars}]
    for label, low, high in settings.price_bands:
        in_band = drafted[(drafted["market_price"] >= low) & (drafted["market_price"] < (high or float("inf")))]
        bands.append({"label": label, "kind": "steals", "low": low, "high": high,
                      "players": in_band[~is_star(in_band, settings)].nlargest(n, "market_gap")})
    return bands


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
    market = has_market(players)
    # With market prices, the realistic plan leads; it's the one to draft from.
    specs = (MARKET_PLANS + PLAN_SPECS) if market else PLAN_SPECS
    plans = [solve_plan(players, settings, spec) for spec in specs]
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
        "long_shots": plans[0].bench,
        "long_shots_plan": plans[0].spec.name,
        "star_price": settings.star_price,
        "star_premium": settings.star_premium,
        "price_bands": price_bands(players, settings) if market else None,
        "tier_guide": tier_guide(players, tiers),
        "market": market_gaps(players) if market else None,
        "bench": BENCH,
    }
