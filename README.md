# lost-ranking

Auction dollar values for fantasy basketball from a ranking source's score.
Default league: 10 teams, $200 budget, 9-cat roto, roster
PG, SG, SF, PF, C, G, PF/C, UTIL + 7 BN = 15 per team (150 drafted; IR not counted),
with an 824-game season cap across all roster spots.

## Run

```bash
pip install -e ".[dev]"
python -m lost_ranking data/raw/dynatyze_redraft_2026-09-25.csv
# options: --teams 12 --budget 200 --top 50 -o output/my_values.csv
pytest
```

Writes `output/<input>_values.csv` and `output/<input>_board.html` (an interactive auction board
with a Values page and a Strategy page; open it in a browser), and prints a position scarcity summary.

## How the dollars are set

1. **Games cap -> core.** 824 games is about 10 full seasons (824 / 82), so only
   about 10 players per team produce stats that count: the **core** (8 starters
   + 2 bench). The other 5 roster spots are long shots.
2. **Simulate the draft** twice, best score first, each player into the most
   restrictive open slot they fit (C before PF/C before UTIL before BN):
   the full rosters (who gets drafted) and the core only (whose games count).
3. **Replacement level.** The average score of the best 3 players outside the
   core, for the whole field and for each position (PG/SG/SF/PF/C).
4. **Value over replacement (VORP).** `score - replacement`.
5. **Dollars.** Two pools per team: $10 for the 5 long shots and $190 for the core.
   Long shots get $1 plus a share of the rest by score (about $1-3 each).
   Core players start at the top long-shot price and split the rest of the core
   pool by positive VORP. Drafted values always add up to $2000.

## Output fields

| Field | Meaning |
|---|---|
| `auction_value` | Recommended price (same as `pos_value`) |
| `field_value` | Price vs. the best undrafted player at any position |
| `scarcity_premium` | `auction_value - field_value`: what position scarcity adds or removes |
| `field_tier` | Tier across the whole field (all positions), using the same tier rules |
| `scarce_pos` | The player's eligible position with the worst replacement (where eligibility helps most) |
| `pos_rank` / `pos_tier` | Rank and tier within `scarce_pos`. A new tier starts after each cliff |
| `cliff_strength` | `major`, `minor` (tier break) or blank for the drop after this player |
| `left_in_tier` | Players left in the same tier below this one |
| `gap_to_next` | Score drop to the next player at `scarce_pos` |
| `cliff_after` | True when that drop is an outlier (a cliff) |
| `scarcity_note` | Readable flag, e.g. "Last C before cliff: -6.02 to Karl-Anthony Towns" |
| `field_vorp`, `pos_vorp`, `pos_replacement` | The numbers behind the dollars |
| `drafted`, `core`, `draft_slot` | Rostered at all / in the core whose games count / which slot |

### Tiers and cliffs

Each position's eligible players are listed best to worst, and a tier ends
after any player where the drop to the next one stands out:

- **Tier break (minor):** the drop is at least 2x the median drop among the
  5 gaps on either side, and at least 0.1 score points. Because it compares to
  nearby gaps, the list keeps splitting into groups further down, where every
  drop is smaller.
- **Major cliff:** the drop is large for the position as a whole
  (`median + 3 x robust std` of all its draft-pool gaps, and at least 0.3).
- Any tier longer than 8 players is split at its largest internal gap.

All thresholds live in `LeagueSettings`.

## Strategy page

- **Game plan:** core size, core budget, long-shot budget, and the rules that follow from them.
- **Roster planner:** your lineup slots, prefilled from a sample plan. Add, remove and
  reprice players; it tracks money left, max bid, max core bid (keeping the long-shot
  money), and what the average open core spot buys.
- **Sample plans:** one superstar, two stars, and balanced, each solved for the best
  10-player core within $190. At fair prices they project about the same total, so the
  edge is buying under value.
- **Where to spend:** field tiers marked Pay up (a major cliff follows), Deep: wait
  (five or more near-equal players) or Fair price.
- **Positions** and **long shots**.

## Layout

```
lost_ranking/
  config.py     LeagueSettings: teams, budget, roster, tuning knobs
  positions.py  parse "PG/SG/G", slot eligibility, slot order
  loaders.py    source CSV -> standard columns (add new sources here)
  valuation.py  draft simulation, replacement levels, VORP, dollars
  scarcity.py   position tiers, cliffs, position summary
  pipeline.py   load -> value -> scarcity -> output tables
  strategy.py   core budget, sample roster plans (integer program), tier buying guide
  report.py     assembles the HTML board from templates/
  templates/    base.html + common.js, then <page>.html/.js/.css per page (values, strategy)
  cli.py        command line
```

To add a ranking source, add a column map to `SOURCE_COLUMN_MAPS` in `loaders.py`
that maps its columns to `player`, `pos`, `team`, `score`.
