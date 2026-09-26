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
# add market prices (ESPN average auction price CSV from the espn-auction-values skill):
#   --market data/raw/espn_auction_values_2026-09-25.csv   (adds the optimizer; --scenarios 60)
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
   **Starter scarcity:** each position's "last starter" is its Nth-best eligible player,
   where N is the league's starting spots for it (G and PF/C split between their
   positions). Centers run out first (the 15th-best C scores 1.23 vs 2.26 at PG), so
   centers gain value and guards give some up. Tune with `starter_scarcity_weight`.
4. **Value over replacement (VORP).** `score - replacement`.
5. **Dollars.** Two pools per team: $10 for the 5 long shots and $190 for the core.
   Long shots get $1 plus a share of the rest by score (about $1-3 each).
   Core players start at the top long-shot price and split the rest of the core
   pool by positive VORP. Drafted values always add up to $2000.

## Output fields

| Field | Meaning |
|---|---|
| `auction_value` | Recommended price (same as `pos_value`) |
| `market_price`, `market_trend` | Average auction price and its trend from the market file (with `--market`) |
| `market_listed` | False when a drafted player isn't in the market list; he's priced at the $1 minimum |
| `market_gap` | `auction_value - market_price`: positive means the market pays less than our value |
| `expected_price` | What to plan on paying: market, +10% for market-priced stars |
| `pos_edge` | Score bonus from position scarcity (bench and starter depth) at `scarce_pos` |
| `field_value` | Price vs. the best undrafted player at any position |
| `scarcity_premium` | `auction_value - field_value`: what position scarcity adds or removes |
| `field_tier` | Tier across the whole field (all positions), using the same tier rules |
| `star` | In a star tier: the top field tiers, each ending in a major cliff (`max_star_tiers`) |
| `star_label` | `Star`, `Underpriced star` / `Overpriced star` (market $8+ off our value, `star_gap`), or `Hype` (market $40+ but not a star) |
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
- **Optimized plan** (with `--market`): the hypothesis "pay up for a few stars, steal the
  rest" turned into a family of strategies in two stages: (1) 0-3 stars targeted (never overpriced ones) x star cap
  (expected price, break-even max bid, or market +20%), (2) for the best of those, the ceiling on
  everyone else (market +0/10/20/30%, or up to our value) and scored over simulated auctions (`simulate.py`). Clearing prices
  swing around market (`price_noise`), other managers bid away part of each bargain
  (`bargain_shrink`), and a lost star's money flows back to depth. Shows every strategy's
  average and 10th-90th percentile core score, star max bids, the budget split by price
  band, a target list ranked by how often each player made the best roster, and a re-run
  in tougher and easier rooms. `--scenarios N` sets the auction count (0 skips; ~1 min at 60).
- **Draft plan with price ranges:** the winning roster with target (usual price), stretch
  (market +20%) and walk-away (our value, or the star cap) per player, two backups per slot at
  about the same money, and what stretching costs against the budget.
- **Who to overpay** (with `--market`): a max bid for every star and hype player, the most
  you can pay and still get a better core than the best one without him (room bidding like
  the simulation). The optimizer caps targeted stars at their expected price or max bid.
- **Pay up for stars, steal the rest** (with `--market`): the plan to draft from. Anyone the
  market prices at $40+ costs 10% over average (`star_price`, `star_premium`); everyone
  else costs the market price. Every plan also gets the best 5-man bench $10 buys.
- **Where the steals are** (with `--market`): stars and what to plan on paying, the
  $10–20 steal zone, under-$10 wins, and overpriced players to let go (`price_bands`).
- **Sample plans:** one superstar, two stars, balanced, and max score (the ceiling), each
  solved for the best 10-player core within $190. At fair prices they project about the
  same total, so the edge is buying under value. With `--market`, a fifth plan buys at
  market prices, and a Market bargains section lists the biggest gaps between our value
  and the market.
- **Where to spend:** field tiers marked Pay up (a major cliff follows), Deep: wait
  (five or more near-equal players) or Fair price.
- **Positions** and **long shots**.

## Layout

```
lost_ranking/
  config.py     LeagueSettings: teams, budget, roster, tuning knobs
  positions.py  parse "PG/SG/G", slot eligibility, slot order
  loaders.py    source CSV -> standard columns (add ranking or market sources here)
  names.py      name keys for joining sources (accents, Jr./III, punctuation)
  market.py     join market prices; expected price (stars at a premium)
  simulate.py   strategy optimizer: simulated auctions over the stars/overpay family
  valuation.py  draft simulation, replacement levels, VORP, dollars
  scarcity.py   position tiers, cliffs, position summary
  pipeline.py   load -> value -> scarcity -> output tables
  strategy.py   core budget, roster plans (integer program with lineup-feasibility rows), tier guide
  report.py     assembles the HTML board from templates/
  templates/    base.html + common.js, then <page>.html/.js/.css per page (values, strategy)
  cli.py        command line
```

To add a ranking source, add a column map to `SOURCE_COLUMN_MAPS` in `loaders.py`
that maps its columns to `player`, `pos`, `team`, `score`.
