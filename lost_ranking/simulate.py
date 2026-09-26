"""Optimize the auction strategy by simulating many auctions.

The hypothesis: pay up for a few stars, then build the rest of the core from
players the market underrates (the $10-20 steal zone and under-$10 wins).
That is a family of strategies, one per (number of stars targeted, how far over
their expected price we'll chase them). Each is scored across simulated auctions:

  - Clearing prices vary around the market price (price_noise), and some of each
    bargain gets bid away because other managers see value too (bargain_shrink).
    Market stars carry the star premium.
  - A targeted star is won only if his clearing price is within our cap
    (expected price x (1 + overpay)). Money from lost stars flows back to depth.
  - The rest of the core is bought at the simulated prices from non-star
    players, as a draft reveals prices one nomination at a time.

The strategy with the best average core score wins; the spread shows the risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .config import LeagueSettings
from .market import is_star
from .strategy import PlanSpec, Plan, bid_price, core_slots, has_market, solve_plan, solve_roster


@dataclass(frozen=True)
class SimSettings:
    scenarios: int = 60
    # Lognormal sigma of clearing price around its center (0.25 ~ +/-25%).
    price_noise: float = 0.25
    # Share of the gap between our value and the market that other managers bid away.
    bargain_shrink: float = 0.25
    star_counts: tuple[int, ...] = (0, 1, 2, 3)
    overpay: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3)
    seed: int = 7


@dataclass
class StrategyResult:
    stars: int
    overpay: float
    targets: Plan
    scores: np.ndarray = field(repr=False)
    stars_won: np.ndarray = field(repr=False)
    band_spend: pd.DataFrame = field(repr=False)  # one row per scenario, one column per band
    # Share of scenarios each player ended up on the roster: the most reliable targets.
    buy_rate: pd.Series = field(repr=False)
    caps: pd.Series = field(repr=False)  # max bid per targeted star

    @property
    def key(self) -> str:
        return f"{self.stars}-{round(self.overpay * 100)}"

    @property
    def name(self) -> str:
        if not self.stars:
            return "No stars: all depth"
        return f"{self.stars} star{'s' if self.stars > 1 else ''}, chase up to +{round(self.overpay * 100)}%"


def simulate_prices(players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings, rng: np.random.Generator) -> pd.DataFrame:
    """One row per scenario, one column per player index: whole-dollar clearing prices."""
    market = players["market_price"].fillna(players["auction_value"])
    center = market + sim.bargain_shrink * (players["auction_value"] - market).clip(lower=0)
    center = center.where(~is_star(players, settings) | (market < settings.star_price), center * (1 + settings.star_premium))
    noise = rng.lognormal(0.0, sim.price_noise, size=(sim.scenarios, len(players)))
    prices = np.maximum(settings.min_bid, np.round(center.to_numpy() * noise))
    return pd.DataFrame(prices, columns=players.index)


def band_of(price: float, settings: LeagueSettings) -> str:
    if price >= settings.star_price:
        return "stars"
    for label, low, high in settings.price_bands:
        if low <= price < (high or float("inf")):
            return label
    return "other"


def run_strategy(
    players: pd.DataFrame, settings: LeagueSettings, prices: pd.DataFrame, stars: int, overpay: float
) -> StrategyResult:
    core_pool = players[players["core"]]
    spec = PlanSpec(f"sim-{stars}", "", "", stars=stars, star_price=settings.star_price, evenness=0.0, cost="expected")
    targets = solve_plan(players, settings, spec)
    target_stars = targets.picks[targets.picks["price"] >= settings.star_price]
    caps = bid_price(target_stars["price"] * (1 + overpay), settings)

    star_idx = [players.index[players["player"] == name][0] for name in target_stars["player"]]
    depth = core_pool[~is_star(core_pool, settings)]
    spots = settings.core_size
    scores, won_counts, spends, bought = [], [], [], []
    for _, scenario in prices.iterrows():
        won, spent = [], 0
        for name, idx, cap in zip(target_stars["player"], star_idx, caps):
            cost = scenario[idx]
            # Win him if he clears under our cap and we can still fill the other spots.
            if cost <= cap and spent + cost + settings.min_bid * (spots - len(won) - 1) <= settings.core_budget:
                won.append(name)
                spent += cost
        roster = None
        while roster is None:
            try:
                roster = solve_roster(
                    pd.concat([depth, core_pool[core_pool["player"].isin(won)]]), settings,
                    replace(spec, stars=0, star_price=None), core_slots(settings), settings.core_budget,
                    settings.core_position_caps, prices=scenario, must_include=frozenset(won),
                )
            except ValueError:
                if not won:
                    raise
                won.pop()  # those stars leave no legal roster in budget: stop at one fewer
        scores.append(roster["score"].sum())
        bought.extend(roster["player"])
        won_counts.append(len(won))
        spends.append(roster.groupby(roster["price"].map(lambda p: band_of(p, settings)))["price"].sum())
    band_spend = pd.DataFrame(spends).fillna(0)
    buy_rate = pd.Series(bought).value_counts() / len(prices)
    return StrategyResult(
        stars, overpay, targets, np.array(scores), np.array(won_counts), band_spend, buy_rate,
        pd.Series(caps.to_numpy(), index=target_stars["player"]),
    )


def optimize(players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings = SimSettings()) -> list[StrategyResult] | None:
    """Every strategy in the family, best average core score first. None without market prices."""
    if not has_market(players):
        return None
    drafted = players[players["drafted"]]
    prices = simulate_prices(drafted, settings, sim, np.random.default_rng(sim.seed))
    results = []
    for stars in sim.star_counts:
        for overpay in (sim.overpay if stars else (0.0,)):
            results.append(run_strategy(drafted, settings, prices, stars, overpay))
    return sorted(results, key=lambda r: -r.scores.mean())


# Rooms to re-run the search in, to check the winner isn't an artifact of one set of assumptions.
ROOMS = (
    ("Tough room", "prices swing ±35% and half of each bargain gets bid away", {"price_noise": 0.35, "bargain_shrink": 0.5}),
    ("Easy room", "prices swing ±15% and only 10% of each bargain gets bid away", {"price_noise": 0.15, "bargain_shrink": 0.1}),
)


def robustness(players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings) -> list[dict]:
    """Re-run a smaller search in tougher and easier rooms; report the top strategies in each."""
    quick = replace(sim, scenarios=max(10, sim.scenarios // 2), star_counts=tuple(k for k in sim.star_counts if k <= 2))
    rooms = []
    for label, note, changes in ROOMS:
        results = optimize(players, settings, replace(quick, **changes)) or []
        rooms.append({"room": label, "note": note, "top": results[:3]})
    return rooms


def optimized_payload(players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings) -> dict | None:
    """Everything the board shows about the optimization, or None without market prices."""
    results = optimize(players, settings, sim)
    if not results:
        return None
    best = results[0]
    stars = best.targets.picks[best.targets.picks["player"].isin(best.caps.index)]
    by_name = players.set_index("player")
    target_list = (
        best.buy_rate.drop(best.caps.index, errors="ignore").head(20).rename("buy_rate").to_frame()
        .join(by_name[["pos", "score", "auction_value", "market_price", "expected_price", "field_tier", "core"]])
        .rename_axis("player").reset_index()
    )
    band_order = ["stars", *(label for label, _, _ in settings.price_bands)]
    spend = best.band_spend.mean()
    summarize = lambda r: {
        "key": r.key, "name": r.name, "stars": r.stars, "overpay": r.overpay,
        "mean": round(float(r.scores.mean()), 2),
        "p10": round(float(np.percentile(r.scores, 10)), 2),
        "p90": round(float(np.percentile(r.scores, 90)), 2),
        "stars_won": round(float(r.stars_won.mean()), 2),
    }
    return {
        "scenarios": sim.scenarios,
        "price_noise": sim.price_noise,
        "bargain_shrink": sim.bargain_shrink,
        "strategies": [summarize(r) for r in results],
        "best": {
            **summarize(best),
            "plan": best.targets,
            "star_targets": stars.assign(cap=stars["player"].map(best.caps)),
            "band_spend": [{"band": b, "spend": round(float(spend.get(b, 0.0)))} for b in band_order],
            "target_list": target_list,
        },
        "rooms": [
            {"room": room["room"], "note": room["note"], "top": [summarize(r) for r in room["top"]]}
            for room in robustness(players, settings, sim)
        ],
    }
