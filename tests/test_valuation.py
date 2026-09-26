from pathlib import Path

import pandas as pd
import pytest

from lost_ranking import LeagueSettings, run
from lost_ranking.positions import parse_positions, slot_priority
from lost_ranking.scarcity import position_tiers
from lost_ranking.strategy import PLAN_SPECS, build_strategy
from lost_ranking.valuation import simulate_draft, value_players

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "raw" / "dynatyze_redraft_2026-09-25.csv"
TINY = LeagueSettings(teams=2, roster={"G": 1, "C": 1, "UTIL": 1}, replacement_depth=1)


def make_players(rows):
    df = pd.DataFrame(rows, columns=["player", "pos", "score"])
    df["positions"] = df["pos"].map(parse_positions)
    return df.sort_values("score", ascending=False, ignore_index=True)


def c_tiers(df, settings=LeagueSettings()):
    df = df.assign(drafted=True)
    tiers = position_tiers(df, settings)
    return tiers[tiers["pos"] == "C"].reset_index(drop=True)


def test_parse_positions():
    assert parse_positions("PG/SG/G") == {"PG", "SG", "G"}
    assert parse_positions(float("nan")) == frozenset()


def test_slot_priority_restrictive_first():
    assert slot_priority(["BN", "UTIL", "G", "C"]) == ["C", "G", "UTIL", "BN"]


def test_draft_reserves_scarce_position():
    # Four guards outscore both centers, but the C slots must still be filled.
    df = make_players([
        ("G1", "PG", 10), ("G2", "PG", 9), ("G3", "PG", 8), ("G4", "PG", 7),
        ("G5", "PG", 6), ("C1", "C", 2), ("C2", "C", 1),
    ])
    slots = simulate_draft(df, TINY.league_slots())
    drafted = set(df.loc[slots.notna(), "player"])
    assert {"C1", "C2"} <= drafted
    assert "G5" not in drafted


def test_values_sum_to_budget_and_undrafted_are_zero():
    df = make_players([
        ("G1", "PG", 10), ("G2", "PG", 9), ("G3", "PG", 8), ("G4", "PG", 7),
        ("G5", "PG", 6), ("C1", "C", 2), ("C2", "C", 1), ("C3", "C", 0),
    ])
    out, levels, _ = value_players(df, TINY)
    assert out["auction_value"].sum() == pytest.approx(TINY.total_budget)
    assert out["field_value"].sum() == pytest.approx(TINY.total_budget)
    assert (out.loc[~out["drafted"], "auction_value"] == 0).all()
    assert (out.loc[out["drafted"], "auction_value"] >= TINY.min_bid).all()
    # Centers are scarce: waiver C is far worse than waiver G, so C1 earns a premium.
    assert levels["C"] < levels["PG"]
    assert out.set_index("player").loc["C1", "scarcity_premium"] > 0


def test_cliff_detected():
    df = make_players([(f"C{i}", "C", s) for i, s in enumerate([9.0, 8.9, 8.8, 5.0, 4.9, 4.8, 4.7])])
    tiers = c_tiers(df)
    assert tiers.loc[tiers["cliff_after"], "pos_rank"].tolist() == [3]
    assert tiers["pos_tier"].tolist() == [1, 1, 1, 2, 2, 2, 2]


def test_sample_csv_end_to_end():
    settings = LeagueSettings()
    result = run(SAMPLE, settings)
    players = result.players
    drafted = players[players["drafted"]]
    assert len(drafted) == settings.draft_pool_size
    assert drafted["auction_value"].sum() == pytest.approx(settings.total_budget)
    assert players.iloc[0]["player"] == "Nikola Jokić"
    assert set(result.positions["pos"]) == {"PG", "SG", "SF", "PF", "C"}
    # Field tiers cover every player; position tiers only cover eligible players.
    field = result.tiers[result.tiers["pos"] == "ALL"]
    assert len(field) == len(players)
    assert players["field_tier"].notna().all()


def test_minor_tier_breaks_below_major_threshold():
    # One huge drop at the top, then small but clear local drops further down.
    scores = [10, 5, 4.9, 4.8, 4.55, 4.45, 4.35, 4.1, 4.0, 3.9]
    df = make_players([(f"C{i}", "C", s) for i, s in enumerate(scores)])
    tiers = c_tiers(df)
    strength = dict(zip(tiers["pos_rank"], tiers["cliff_strength"]))
    assert strength[1] == "major"
    assert strength[4] == "minor" and strength[7] == "minor"
    assert tiers["pos_tier"].max() == 4


def test_long_tiers_are_split():
    df = make_players([(f"C{i}", "C", 10 - 0.1 * i) for i in range(20)])
    tiers = c_tiers(df, LeagueSettings(max_tier_size=8))
    assert tiers.groupby("pos_tier").size().max() <= 8


def test_games_cap_core_and_bench_pools():
    settings = LeagueSettings()
    players = run(SAMPLE, settings).players
    core = players[players["core"]]
    bench = players[players["drafted"] & ~players["core"]]
    assert settings.core_size == 10  # 824 games / 82
    assert len(core) == settings.core_pool_size
    assert bench["auction_value"].sum() == pytest.approx(settings.teams * settings.bench_budget)
    assert core["auction_value"].sum() == pytest.approx(settings.teams * settings.core_budget)
    assert bench["auction_value"].max() <= core["auction_value"].min()


def test_strategy_plans_fit_budget_and_slots():
    settings = LeagueSettings()
    result = run(SAMPLE, settings)
    strategy = build_strategy(result.players, result.tiers, settings)
    assert len(strategy["plans"]) == len(PLAN_SPECS)
    for plan in strategy["plans"]:
        picks = plan.picks
        assert len(picks) == settings.core_size
        assert picks["player"].is_unique
        assert plan.spend <= settings.core_budget
        assert sorted(picks["slot"]) == sorted(strategy["core_slots"])
        assert (picks["price"] >= plan.spec.star_price).sum() == plan.spec.stars
        for pos, cap in settings.core_position_caps.items():
            assert picks["pos"].str.split("/").map(lambda p, pos=pos: pos in p).sum() <= cap


MARKET = Path(__file__).resolve().parent / "fixtures" / "espn_auction_sample.csv"


def test_name_key_matches_accents_and_suffixes():
    from lost_ranking.names import name_key

    assert name_key("Nikola Jokić") == name_key("Nikola Jokic")
    assert name_key("Jaren Jackson Jr.") == name_key("Jaren Jackson")
    assert name_key("Shai Gilgeous-Alexander") == "shaigilgeousalexander"


def test_market_prices_join_and_market_plan():
    settings = LeagueSettings()
    result = run(SAMPLE, settings, market_path=MARKET)
    p = result.players.set_index("player")
    assert p.loc["Nikola Jokić", "market_price"] == 70.0
    assert p.loc["Kristaps Porziņģis", "market_price"] == 2.0
    assert p.loc["Jaren Jackson Jr.", "market_gap"] == pytest.approx(p.loc["Jaren Jackson Jr.", "auction_value"] - 30.0)
    assert p["market_listed"].sum() == 5  # "Nobody Real" has no match
    # Drafted players missing from the market list go for the minimum there.
    assert p.loc["Nikola Vučević", "market_price"] == settings.min_bid
    assert not p.loc["Nikola Vučević", "market_listed"]

    strategy = build_strategy(result.players, result.tiers, settings)
    market_plan = next(pl for pl in strategy["plans"] if pl.spec.cost == "market")
    assert market_plan.spend <= settings.core_budget
    assert market_plan.worth > market_plan.spend
    assert strategy["market"]["bargains"].iloc[0]["market_gap"] > 0


def test_expected_price_premium_only_for_market_stars():
    from lost_ranking.market import expected_price

    settings = LeagueSettings()  # star_price 40, premium 10%
    df = pd.DataFrame({"auction_value": [80.0, 46.0, 12.0, 20.0], "market_price": [70.0, 26.0, 8.0, None]})
    assert expected_price(df, settings).tolist() == pytest.approx([77.0, 26.0, 8.0, 20.0])


def test_starter_scarcity_favors_thin_positions():
    from dataclasses import replace

    settings = LeagueSettings()
    base = run(SAMPLE, replace(settings, starter_scarcity_weight=0.0)).players.set_index("player")
    scarce = run(SAMPLE, settings)
    assert scarce.starter_levels["C"] < scarce.starter_levels["PG"]
    p = scarce.players.set_index("player")
    assert p.loc["Jalen Duren", "auction_value"] > base.loc["Jalen Duren", "auction_value"]  # C only
    assert p.loc["Jalen Brunson", "auction_value"] < base.loc["Jalen Brunson", "auction_value"]  # PG only
    assert p.loc[p["core"], "auction_value"].sum() == pytest.approx(settings.teams * settings.core_budget)


def test_plan_bench_fits_bench_budget():
    settings = LeagueSettings()
    result = run(SAMPLE, settings, market_path=MARKET)
    for plan in build_strategy(result.players, result.tiers, settings)["plans"]:
        assert len(plan.bench) == settings.bench_spots
        assert plan.bench_spend <= settings.team_bench_budget
        assert not set(plan.bench["player"]) & set(plan.picks["player"])


def test_optimizer_scores_strategy_family():
    from lost_ranking.simulate import SimSettings, optimize

    settings = LeagueSettings()
    result = run(SAMPLE, settings, market_path=MARKET)
    sim = SimSettings(scenarios=4, star_counts=(0, 1), overpay=(0.0, 0.2))
    results = optimize(result.players, settings, sim)
    assert [r.key for r in results] and len(results) == 3  # 0 stars once, 1 star x 2 overpay levels
    means = [r.scores.mean() for r in results]
    assert means == sorted(means, reverse=True)
    for r in results:
        assert len(r.scores) == sim.scenarios
        assert (r.stars_won <= r.stars).all()
        assert (r.band_spend.sum(axis=1) <= settings.core_budget).all()
    assert optimize(run(SAMPLE, settings).players, settings, sim) is None  # needs market prices
