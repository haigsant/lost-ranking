from pathlib import Path

import pandas as pd
import pytest

from lost_ranking import LeagueSettings, run
from lost_ranking.positions import parse_positions, slot_priority
from lost_ranking.scarcity import position_tiers
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
    slots = simulate_draft(df, TINY)
    drafted = set(df.loc[slots.notna(), "player"])
    assert {"C1", "C2"} <= drafted
    assert "G5" not in drafted


def test_values_sum_to_budget_and_undrafted_are_zero():
    df = make_players([
        ("G1", "PG", 10), ("G2", "PG", 9), ("G3", "PG", 8), ("G4", "PG", 7),
        ("G5", "PG", 6), ("C1", "C", 2), ("C2", "C", 1), ("C3", "C", 0),
    ])
    out, levels = value_players(df, TINY)
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
