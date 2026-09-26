"""Auction strategy: budget split, sample roster plans, and tier buying guidance.

The games cap means only the core (about games_cap / games_per_player players
per team) produces stats that count. So the plan is: spend almost everything on
the core, and fill the rest of the roster with minimum-bid long shots.

Roster plans are solved as a small integer program: pick players to maximize
total score within the budget, with lineup-feasibility rows guaranteeing every
slot can be filled by an eligible player; slots are assigned afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

from .config import BENCH, LeagueSettings
from .market import is_market_star
from .positions import SLOT_ELIGIBILITY, can_fill, parse_positions


@dataclass(frozen=True)
class PlanSpec:
    key: str
    name: str
    summary: str
    # Exactly this many stars (by our tiers) in the core; None = no rule.
    stars: int | None = None
    # Tiny tie-break that spreads leftover money evenly; at fair prices many rosters
    # score about the same, and this picks the sensible one.
    evenness: float = 0.01
    # What a player costs: "value" (our fair price), "market" (average auction price),
    # or "expected" (market, with top talent at a premium; see market.expected_price).
    cost: str = "value"


PLAN_SPECS = (
    PlanSpec(
        "one_star",
        "One star + depth",
        "Win one star, then spread the rest evenly across the other nine core spots.",
        stars=1,
    ),
    PlanSpec(
        "two_stars",
        "Two stars + depth",
        "Win two stars, then fill evenly.",
        stars=2,
    ),
    PlanSpec(
        "balanced",
        "No stars: balanced ten",
        "Ten solid players and no star.",
        stars=0,
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
        "Anyone the market prices at $40+ costs 10% over their average, because that's what it takes to win them. Everyone else costs the market price, so the budget flows to players the room underrates.",
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


def _hall_rows(slots: list[str], positions: list[frozenset[str]]) -> tuple[np.ndarray, np.ndarray]:
    """Lineup-feasibility constraints on player picks (Hall's theorem).

    For every group of position-restricted slots, at least as many picked players must
    be eligible for one of them as there are slots in the group. UTIL and bench take
    anyone, so with the total pick count fixed, these rows guarantee a legal lineup.
    """
    restricted = [s for s in slots if SLOT_ELIGIBILITY[s] is not None]
    need: dict[frozenset[int], int] = {}
    for r in range(1, len(restricted) + 1):
        for group in combinations(range(len(restricted)), r):
            members = frozenset(
                i for i, pos in enumerate(positions) if any(can_fill(restricted[g], pos) for g in group)
            )
            need[members] = max(need.get(members, 0), r)
    rows = np.zeros((len(need), len(positions)))
    for k, members in enumerate(need):
        rows[k, list(members)] = 1
    return rows, np.array(list(need.values()), dtype=float)


def assign_slots(picks: pd.DataFrame, slots: list[str]) -> list[str]:
    """Slot for each pick: best scores into position-restricted slots first (bipartite
    matching), then UTIL, then bench."""
    restricted = [j for j, s in enumerate(slots) if SLOT_ELIGIBILITY[s] is not None]
    owner = dict.fromkeys(restricted, -1)
    positions = list(picks["positions"])

    def place(i: int, seen: set[int]) -> bool:
        for j in restricted:
            if j in seen or not can_fill(slots[j], positions[i]):
                continue
            seen.add(j)
            if owner[j] == -1 or place(owner[j], seen):
                owner[j] = i
                return True
        return False

    order = np.argsort(-picks["score"].to_numpy(), kind="stable")
    leftover = [i for i in order if not place(i, set())]
    flexible = iter(j for j, s in sorted(enumerate(slots), key=lambda js: js[1] == BENCH) if j not in owner)
    slot_of = {i: slots[j] for j, i in owner.items() if i != -1}
    slot_of.update({i: slots[next(flexible)] for i in leftover})
    return [slot_of[i] for i in range(len(picks))]


def solve_roster(
    players: pd.DataFrame,
    settings: LeagueSettings,
    spec: PlanSpec,
    slots: list[str],
    budget: int,
    position_caps: dict[str, int],
    prices: pd.Series | None = None,
    must_include: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Best set of players for `slots` within `budget`. players needs player, pos, score, auction_value.

    prices overrides the spec's cost (e.g. simulated auction prices); must_include
    forces players onto the roster (e.g. stars already won).
    """
    pool = players.assign(
        positions=players["pos"].map(parse_positions),
        price=bid_price(plan_cost(players, spec) if prices is None else prices.loc[players.index], settings),
        value_price=bid_price(players["auction_value"], settings),
    ).reset_index(drop=True)
    positions = list(pool["positions"])
    score = pool["score"].to_numpy()
    price = pool["price"].to_numpy(dtype=float)

    hall, need = _hall_rows(slots, positions)
    constraints = [
        LinearConstraint(np.ones((1, len(pool))), len(slots), len(slots)),  # fill every slot
        LinearConstraint(hall, need, np.inf),  # ...with a legal lineup
        LinearConstraint(price[None, :], 0, budget),
    ]
    if spec.stars is not None:
        stars = pool["star"].to_numpy(dtype=float)
        constraints.append(LinearConstraint(stars[None, :], spec.stars, spec.stars))
    if must_include:
        forced = pool["player"].isin(must_include).to_numpy(dtype=float)
        constraints.append(LinearConstraint(forced[None, :], forced.sum(), forced.sum()))
    if position_caps:
        caps = np.array([[float(pos in p) for p in positions] for pos in position_caps])
        constraints.append(LinearConstraint(caps, 0, list(position_caps.values())))

    result = milp(
        -(score - spec.evenness * score**2),  # milp minimizes
        integrality=np.ones(len(pool)),
        bounds=Bounds(0, 1),
        constraints=constraints,
    )
    if not result.success:
        raise ValueError(f"no feasible roster for plan {spec.key!r}: {result.message}")

    picks = pool[result.x > 0.5].reset_index(drop=True)
    picks["slot"] = assign_slots(picks, slots)
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
        bench_spec = replace(spec, stars=None, evenness=0.0)
        bench = solve_roster(
            candidates, settings, bench_spec, [BENCH] * settings.bench_spots, settings.team_bench_budget, {}
        )
    return Plan(spec, core, bench)


def max_bids(
    players: pd.DataFrame, settings: LeagueSettings, names: list[str], prices: pd.Series | None = None
) -> pd.DataFrame:
    """For each player: the most you can pay and still beat the best core without him.

    Everyone else costs `prices` (default: what we expect to pay, or our value without
    market prices). Above the max bid, the money does more spread across others.
    """
    cost = "expected" if has_market(players) else "value"
    spec = PlanSpec("max-bid", "", "", evenness=0.0, cost=cost)
    core_pool = players[players["core"] | players["player"].isin(names)]
    base_prices = plan_cost(core_pool, spec) if prices is None else prices.loc[core_pool.index]
    slots = core_slots(settings)

    def best(pool: pd.DataFrame, prices: pd.Series, forced: frozenset[str] = frozenset()) -> float:
        try:
            roster = solve_roster(pool, settings, spec, slots, settings.core_budget,
                                  settings.core_position_caps, prices=prices, must_include=forced)
        except ValueError:
            return float("-inf")
        return float(roster["score"].sum())

    rows = []
    for name in names:
        without = best(core_pool[core_pool["player"] != name], base_prices)
        idx = core_pool.index[core_pool["player"] == name][0]
        lo, hi = settings.min_bid, settings.core_budget - settings.min_bid * (settings.core_size - 1)
        if best(core_pool, base_prices.mask(base_prices.index == idx, lo), frozenset([name])) < without:
            lo = 0  # not worth rostering even at the minimum
        else:
            while lo < hi:  # highest price that still beats the core without him
                mid = (lo + hi + 1) // 2
                with_him = best(core_pool, base_prices.mask(base_prices.index == idx, mid), frozenset([name]))
                lo, hi = (mid, hi) if with_him >= without else (lo, mid - 1)
        rows.append({"player": name, "max_bid": int(lo), "expected": int(bid_price(base_prices[[idx]], settings).iloc[0])})
    out = pd.DataFrame(rows)
    out["room"] = out["max_bid"] - out["expected"]  # > 0: worth overpaying by up to this much
    return out


def price_bands(players: pd.DataFrame, settings: LeagueSettings, n: int = 10) -> list[dict]:
    """Where to hunt: stars and what they'll cost, then the best buys in each market-price band."""
    drafted = players[players["drafted"] & players["market_price"].notna()]
    headline = drafted["star"] | (drafted["star_label"] == "Hype")
    stars = drafted[headline].sort_values(["star", "auction_value"], ascending=False)
    bands = [{"label": "Stars and hype", "kind": "stars", "players": stars}]
    for label, low, high in settings.price_bands:
        in_band = drafted[(drafted["market_price"] >= low) & (drafted["market_price"] < (high or float("inf")))]
        bands.append({"label": label, "kind": "steals", "low": low, "high": high,
                      "players": in_band[~headline.loc[in_band.index]].nlargest(n, "market_gap")})
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
                "advice": "Stars" if g["star"].all() else _tier_advice(len(g), last["field_cliff_strength"]),
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
    """Non-star core players the market prices furthest below (bargains) and above
    (overpriced) our value. Stars and hype have their own table."""
    priced = players[players["core"] & players["market_price"].notna() & (players["star_label"] == "")]
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
        "market_star_price": settings.market_star_price,
        "stars": players.loc[players["star"], "player"].tolist(),
        "star_premium": settings.star_premium,
        "price_bands": price_bands(players, settings) if market else None,
        "tier_guide": tier_guide(players, tiers),
        "market": market_gaps(players) if market else None,
        "bench": BENCH,
    }
