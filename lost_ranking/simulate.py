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
from .positions import can_fill, parse_positions
from .strategy import PlanSpec, Plan, bid_price, core_slots, has_market, max_bids, solve_plan, solve_roster


@dataclass(frozen=True)
class SimSettings:
    scenarios: int = 60
    # Lognormal sigma of clearing price around its center (0.25 ~ +/-25%).
    price_noise: float = 0.25
    # Share of the gap between our value and the market that other managers bid away.
    bargain_shrink: float = 0.25
    star_counts: tuple[int, ...] = (0, 1, 2, 3)
    # How high to bid on targeted stars: their expected price, their max bid (break-even
    # vs. the best core without them; see strategy.max_bids), or market + star_stretch.
    cap_rules: tuple[str, ...] = ("expected", "max_bid", "stretch")
    star_stretch: float = 0.20
    # Ceiling for everyone else: market + this share, never above our value (None = our value).
    depth_stretch: tuple[float | None, ...] = (0.0, 0.1, 0.2, 0.3, None)
    seed: int = 7


@dataclass
class StrategyResult:
    stars: int
    cap_rule: str
    depth_stretch: float | None
    targets: Plan
    scores: np.ndarray = field(repr=False)
    stars_won: np.ndarray = field(repr=False)
    band_spend: pd.DataFrame = field(repr=False)  # one row per scenario, one column per band
    # Share of scenarios each player ended up on the roster: the most reliable targets.
    buy_rate: pd.Series = field(repr=False)
    caps: pd.Series = field(repr=False)  # max bid per targeted star

    @property
    def key(self) -> str:
        return f"{self.stars}-{self.cap_rule}-{self.depth_stretch}"

    @property
    def star_policy(self) -> str:
        if not self.stars:
            return "No stars"
        limit = {"max_bid": "max bid", "expected": "expected price", "stretch": "market +20%"}[self.cap_rule]
        return f"{self.stars} star{'s' if self.stars > 1 else ''} to {limit}"

    @property
    def depth_policy(self) -> str:
        return "others to our value" if self.depth_stretch is None else f"others to market +{round(self.depth_stretch * 100)}%"

    @property
    def name(self) -> str:
        return f"{self.star_policy}, {self.depth_policy}"


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


def market_of(players: pd.DataFrame) -> pd.Series:
    return players["market_price"].fillna(players["auction_value"])


def depth_caps(players: pd.DataFrame, stretch: float | None) -> pd.Series:
    """Most we'd bid on a non-star: market + stretch, never above our value."""
    value = players["auction_value"]
    capped = value if stretch is None else np.minimum(value, market_of(players) * (1 + stretch))
    return capped.round()


def star_caps(stars: pd.DataFrame, rule: str, bids: dict[str, int], sim: SimSettings) -> pd.Series:
    if rule == "expected":
        return stars["price"]
    if rule == "stretch":
        return (market_of(stars) * (1 + sim.star_stretch)).round()
    return stars["player"].map(bids)


def run_strategy(
    players: pd.DataFrame,
    settings: LeagueSettings,
    prices: pd.DataFrame,
    stars: int,
    cap_rule: str,
    bids: dict[str, int],
    sim: SimSettings,
    depth_stretch: float | None = None,
) -> StrategyResult:
    core_pool = players[players["core"]]
    spec = PlanSpec(f"sim-{stars}", "", "", stars=stars, evenness=0.0, cost="expected")
    # Star targets never include overpriced stars: the market already pays well over our value.
    targets = solve_plan(players[players["star_label"] != "Overpriced star"], settings, spec)
    target_stars = targets.picks[targets.picks["star"]]
    caps = star_caps(target_stars, cap_rule, bids, sim)

    star_idx = [players.index[players["player"] == name][0] for name in target_stars["player"]]
    # Everyone drafted who isn't a star, so end-of-draft bargains are there to fill out a roster.
    depth = players[~players["star"]]
    depth_cap = depth_caps(depth, depth_stretch)
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
        affordable = depth[scenario[depth.index] <= depth_cap]  # we drop out above our ceiling
        roster = None
        while roster is None:
            try:
                roster = solve_roster(
                    pd.concat([affordable, core_pool[core_pool["player"].isin(won)]]), settings,
                    replace(spec, stars=None), core_slots(settings), settings.core_budget,
                    settings.core_position_caps, prices=scenario, must_include=frozenset(won),
                )
            except ValueError:
                if won:
                    won.pop()  # those stars leave no legal roster in budget: stop at one fewer
                elif len(affordable) < len(depth):
                    affordable = depth  # ceilings too tight to fill a lineup: pay what it takes
                else:
                    raise
        scores.append(roster["score"].sum())
        bought.extend(roster["player"])
        won_counts.append(len(won))
        spends.append(roster.groupby([band_of(p, st, settings) for p, st in zip(roster["price"], roster["star"])])["price"].sum())
    band_spend = pd.DataFrame(spends).fillna(0)
    buy_rate = pd.Series(bought).value_counts() / len(prices)
    return StrategyResult(
        stars, cap_rule, depth_stretch, targets, np.array(scores), np.array(won_counts), band_spend, buy_rate,
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
    # Stage 1: how many stars and how high to bid on them (others up to our value).
    results = [
        run_strategy(drafted, settings, prices, stars, rule, bid_map, sim)
        for stars in sim.star_counts
        for rule in (sim.cap_rules if stars else sim.cap_rules[:1])
    ]
    # Stage 2: for the best star policy, how far over market to go on everyone else.
    best = max(results, key=lambda r: r.scores.mean())
    results += [
        run_strategy(drafted, settings, prices, best.stars, best.cap_rule, bid_map, sim, stretch)
        for stretch in sim.depth_stretch if stretch is not None
    ]
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


def range_plan(players: pd.DataFrame, best: StrategyResult, settings: LeagueSettings, sim: SimSettings) -> pd.DataFrame:
    """The winning roster with a price range per player and two backups per slot.

    target: what he usually goes for (market, +premium at $40+). stretch: market +20%,
    capped at walk-away. walk_away: our value, or the star cap for targeted stars.
    """
    plan = pd.concat([best.targets.picks, best.targets.bench], ignore_index=True)
    by_name = players.set_index("player")
    market = market_of(by_name.loc[plan["player"]]).to_numpy()
    value = plan["auction_value"].to_numpy()
    star_cap = plan["player"].map(best.caps)
    walk = star_cap.fillna(pd.Series(value).round()).astype(int).to_numpy()
    target = np.minimum(plan["price"].to_numpy(), walk)
    stretch = np.clip(np.round(market * (1 + sim.star_stretch)), target, walk).astype(int)
    out = plan[["slot", "player", "pos", "score", "star_label", "auction_value"]].assign(
        market_price=market, target=target, stretch=stretch, walk_away=walk,
    )

    # Backups: best-scoring players not on the plan who fit the slot, cost about the same,
    # and are still worth their price. Stars back up stars; spread picks across slots.
    taken = set(plan["player"])
    pool = players[players["drafted"] & ~players["player"].isin(taken)
                   & ~players["star_label"].isin(["Overpriced star", "Hype"])]
    pool = pool.assign(positions=pool["pos"].map(parse_positions), target=bid_price(pool["expected_price"], settings))
    pool = pool[pool["target"] <= pool["auction_value"].round()]
    used: dict[str, int] = {}
    backups = []
    for _, row in out.iterrows():
        fits = pool[pool["positions"].map(lambda p, s=row["slot"]: can_fill(s, p))]
        if row["player"] in best.caps.index:
            fits = fits[fits["star"]] if fits["star"].any() else fits
        else:
            fits = fits[~fits["star"] & fits["target"].between(0.5 * row["target"] - 2, row["stretch"] + 5)]
        ranked = fits.assign(reuse=fits["player"].map(used).fillna(0)).sort_values(["reuse", "score"], ascending=[True, False])
        picks = ranked.head(2)
        for name in picks["player"]:
            used[name] = used.get(name, 0) + 1
        backups.append([
            {"player": b.player, "target": int(b.target), "walk_away": int(round(b.auction_value))}
            for b in picks.itertuples()
        ])
    return out.assign(backups=backups)


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
        "depth_stretch": r.depth_stretch, "star_policy": r.star_policy, "depth_policy": r.depth_policy,
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
            "range_plan": range_plan(players, best, settings, sim),
        },
        "rooms": [
            {"room": room["room"], "note": room["note"], "top": [summarize(r) for r in room["top"]]}
            for room in robustness(players, settings, sim)
        ],
    }
