"""Command line entry point: python -m lost_ranking <rankings.csv>"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import LeagueSettings
from .loaders import SOURCE_COLUMN_MAPS
from .pipeline import run
from .report import write_board

MONEY_COLUMNS = ["auction_value", "field_value", "scarcity_premium"]
SCORE_COLUMNS = ["score", "gap_to_next", "field_vorp", "pos_vorp", "pos_replacement"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = LeagueSettings()
    p = argparse.ArgumentParser(description="Auction dollar values from a ranking score.")
    p.add_argument("csv", type=Path, help="ranking CSV to value")
    p.add_argument("-o", "--output", type=Path, help="output CSV (default: output/<name>_values.csv)")
    p.add_argument("--source", default="dynatyze", choices=sorted(SOURCE_COLUMN_MAPS))
    p.add_argument("--teams", type=int, default=defaults.teams)
    p.add_argument("--budget", type=int, default=defaults.budget_per_team)
    p.add_argument("--top", type=int, default=30, help="rows to print (0 = none)")
    p.add_argument("--no-html", action="store_true", help="skip the HTML auction board")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    settings = LeagueSettings(teams=args.teams, budget_per_team=args.budget)
    result = run(args.csv, settings, args.source)

    players = result.players.copy()
    players[MONEY_COLUMNS] = players[MONEY_COLUMNS].round(1)
    players[SCORE_COLUMNS] = players[SCORE_COLUMNS].round(2)

    output = args.output or Path("output") / f"{args.csv.stem}_values.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    players.to_csv(output, index=False)

    drafted = players[players["drafted"]]
    print(
        f"{settings.teams} teams x ${settings.budget_per_team} = ${settings.total_budget} | "
        f"{len(drafted)} drafted of {len(players)} | "
        f"field replacement score {result.replacement['ALL']:.2f} | "
        f"sum of values ${drafted['auction_value'].sum():.0f}"
    )
    with pd.option_context("display.width", 200, "display.max_colwidth", 70):
        print("\nPosition scarcity:")
        print(result.positions.to_string(index=False))
        if args.top:
            cols = ["overall_rank", "player", "pos", "score", "auction_value",
                    "field_value", "scarcity_premium", "scarce_pos", "pos_tier", "scarcity_note"]
            print(f"\nTop {args.top}:")
            print(players[cols].head(args.top).to_string(index=False))
    print(f"\nWrote {output}")
    if not args.no_html:
        print(f"Wrote {write_board(result, output.with_name(f'{args.csv.stem}_board.html'))}")


if __name__ == "__main__":
    main()
