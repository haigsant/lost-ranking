# lost-ranking

Auction dollar values for fantasy basketball from a ranking source's score.
Default league: 10 teams, $200 budget, 9-cat roto, Yahoo roster
(PG, SG, G, SF, PF, F, C, C, UTIL, UTIL + 3 BN = 13 per team, 130 drafted).

## Run

```bash
pip install -e ".[dev]"
python -m lost_ranking data/raw/dynatyze_redraft_2026-09-25.csv
# options: --teams 12 --budget 200 --top 50 -o output/my_values.csv
pytest
```

Writes `output/<input>_values.csv` and `output/<input>_board.html` (an interactive auction board
with position tiers; open it in a browser), and prints a position scarcity summary.

## How the dollars are set

1. **Simulate the draft.** Walk players best score first, and put each one in the
   most restrictive open slot they fit (C before F before UTIL before BN).
   This fills every roster spot in the league and tells us who is draftable.
2. **Replacement level.** The average score of the best 3 undrafted players,
   for the whole field and for each position (PG/SG/SF/PF/C).
3. **Value over replacement (VORP).** `score - replacement`.
4. **Dollars.** Every drafted player costs at least $1. The remaining
   `$2000 - 130 x $1 = $1870` is split in proportion to positive VORP.
   Undrafted players are $0. Drafted values always add up to $2000.

## Output fields

| Field | Meaning |
|---|---|
| `auction_value` | Recommended price (same as `pos_value`) |
| `field_value` | Price vs. the best undrafted player at any position |
| `scarcity_premium` | `auction_value - field_value`: what position scarcity adds or removes |
| `scarce_pos` | The player's eligible position with the worst replacement (where eligibility helps most) |
| `pos_rank` / `pos_tier` | Rank and tier within `scarce_pos`. A new tier starts after each cliff |
| `cliff_strength` | `major`, `minor` (tier break) or blank for the drop after this player |
| `left_in_tier` | Players left in the same tier below this one |
| `gap_to_next` | Score drop to the next player at `scarce_pos` |
| `cliff_after` | True when that drop is an outlier (a cliff) |
| `scarcity_note` | Readable flag, e.g. "Last C before cliff: -6.02 to Karl-Anthony Towns" |
| `field_vorp`, `pos_vorp`, `pos_replacement` | The numbers behind the dollars |
| `drafted`, `draft_slot` | Whether and where the simulated draft rostered them |

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

## Layout

```
lost_ranking/
  config.py     LeagueSettings: teams, budget, roster, tuning knobs
  positions.py  parse "PG/SG/G", slot eligibility, slot order
  loaders.py    source CSV -> standard columns (add new sources here)
  valuation.py  draft simulation, replacement levels, VORP, dollars
  scarcity.py   position tiers, cliffs, position summary
  pipeline.py   load -> value -> scarcity -> output tables
  report.py     HTML auction board (template in templates/board.html)
  cli.py        command line
```

To add a ranking source, add a column map to `SOURCE_COLUMN_MAPS` in `loaders.py`
that maps its columns to `player`, `pos`, `team`, `score`.
