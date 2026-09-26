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
from .market import expected_price
from .strategy import PlanSpec, Plan, core_slots, has_market, max_bids, solve_plan, solve_roster


@dataclass(frozen=True)
class SimSettings:
    scenarios: int = 60
    # Lognormal sigma of clearing price around its center (0.25 ~ +/-25%).
    price_noise: float = 0.25
    # Share of the gap between our value and the market that other managers bid away.
    bargain_shrink: float = 0.25
    star_counts: tuple[int, ...] = (0, 1, 2, 3)
    # How high to bid on targeted stars: their expected price, or their max bid
    # (the break-even price vs. the best core without them; see strategy.max_bids).
    cap_rules: tuple[str, ...] = ("expected", "max_bid")
    seed: int = 7


@dataclass
class StrategyResult:
    stars: int
    cap_rule: str
    targets: Plan
    scores: np.ndarray = field(repr=False)
    stars_won: np.ndarray = field(repr=False)
    band_spend: pd.DataFrame = field(repr=False)  # one row per scenario, one column per band
    # Share of scenarios each player ended up on the roster: the most reliable targets.
    buy_rate: pd.Series = field(repr=False)
    caps: pd.Series = field(repr=False)  # max bid per targeted star

    @property
    def key(self) -> str:
        return f"{self.stars}-{self.cap_rule}"

    @property
    def name(self) -> str:
        if not self.stars:
            return "No stars: all depth"
        limit = "his max bid" if self.cap_rule == "max_bid" else "expected price"
        return f"{self.stars} star{'s' if self.stars > 1 else ''}, bid to {limit}"


def simulate_prices(players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings, rng: np.random.Generator) -> pd.DataFrame:
    """One row per scenario, one column per player index: whole-dollar clearing prices."""
    center = expected_price(players, settings, sim.bargain_shrink)
    noise = rng.lognormal(0.0, sim.price_noise, size=(sim.scenarios, len(players)))
    prices = np.maximum(settings.min_bid, np.round(center.to_numpy() * noise))
    return pd.DataFrame(prices, columns=players.index)


def band_of(price: float, star: bool, settings: LeagueSettings) -> str:
    """Spend bucket: stars (by our tiers) first, then the market-price bands."""
    if star:
        return "stars"
    for label, low, high in settings.price_bands:
        if low <= price < (high or float("inf")):
            return label
    return "other"


def run_strategy(
    players: pd.DataFrame,
    settings: LeagueSettings,
    prices: pd.DataFrame,
    stars: int,
    cap_rule: str,
    bids: dict[str, int],
) -> StrategyResult:
    core_pool = players[players["core"]]
    spec = PlanSpec(f"sim-{stars}", "", "", stars=stars, evenness=0.0, cost="expected")
    targets = solve_plan(players, settings, spec)
    target_stars = targets.picks[targets.picks["star"]]
    caps = target_stars["price"] if cap_rule == "expected" else target_stars["player"].map(bids)

    star_idx = [players.index[players["player"] == name][0] for name in target_stars["player"]]
    depth = core_pool[~core_pool["star"]]
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
                    replace(spec, stars=None), core_slots(settings), settings.core_budget,
                    settings.core_position_caps, prices=scenario, must_include=frozenset(won),
                )
            except ValueError:
                if not won:
                    raise
                won.pop()  # those stars leave no legal roster in budget: stop at one fewer
        scores.append(roster["score"].sum())
        bought.extend(roster["player"])
        won_counts.append(len(won))
        spends.append(roster.groupby([band_of(p, st, settings) for p, st in zip(roster["price"], roster["star"])])["price"].sum())
    band_spend = pd.DataFrame(spends).fillna(0)
    buy_rate = pd.Series(bought).value_counts() / len(prices)
    return StrategyResult(
        stars, cap_rule, targets, np.array(scores), np.array(won_counts), band_spend, buy_rate,
        pd.Series(caps.to_numpy(), index=target_stars["player"]),
    )


def star_bids(players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings) -> pd.DataFrame:
    """Max bid for every star and hype player, with the room bidding like the simulation."""
    names = players.loc[players["star_label"] != "", "player"].tolist()
    return max_bids(players, settings, names, prices=expected_price(players, settings, sim.bargain_shrink))


def optimize(
    players: pd.DataFrame, settings: LeagueSettings, sim: SimSettings = SimSettings(), bids: pd.DataFrame | None = None
) -> list[StrategyResult] | None:
    """Every strategy in the family, best average core score first. None without market prices."""
    if not has_market(players):
        return None
    drafted = players[players["drafted"]]
    bids = star_bids(drafted, settings, sim) if bids is None else bids
    bid_map = dict(zip(bids["player"], bids["max_bid"]))
    prices = simulate_prices(drafted, settings, sim, np.random.default_rng(sim.seed))
    results = []
    for stars in sim.star_counts:
        for rule in (sim.cap_rules if stars else sim.cap_rules[:1]):
            results.append(run_strategy(drafted, settings, prices, stars, rule, bid_map))
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
    if not has_market(players):
        return None
    bids = star_bids(players[players["drafted"]], settings, sim)
    results = optimize(players, settings, sim, bids)
    best = results[0]
    stars = best.targets.picks[best.targets.picks["player"].isin(best.caps.index)]
    by_name = players.set_index("player")
    target_list = (
        best.buy_rate.drop(best.caps.index, errors="ignore").head(20).rename("buy_rate").to_frame()
        .join(by_name[["pos", "score", "auction_value", "market_price", "expected_price", "field_tier", "core", "star_label"]])
        .rename_axis("player").reset_index()
    )
    band_order = ["stars", *(label for label, _, _ in settings.price_bands)]
    spend = best.band_spend.mean()
    summarize = lambda r: {
        "key": r.key, "name": r.name, "stars": r.stars, "cap_rule": r.cap_rule,
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
        # Who is worth overpaying: max bid vs. expected price for every star and hype player.
        "bids": bids.merge(players[["player", "pos", "star_label", "auction_value", "market_price"]], on="player"),
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
