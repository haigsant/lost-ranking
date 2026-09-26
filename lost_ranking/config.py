"""League settings that drive every valuation step."""

from __future__ import annotations

from dataclasses import dataclass, field

# League roster: 8 starters + 7 bench = 15 per team (IR spots don't count).
DEFAULT_ROSTER: dict[str, int] = {
    "PG": 1,
    "SG": 1,
    "SF": 1,
    "PF": 1,
    "C": 1,
    "G": 1,
    "PF/C": 1,
    "UTIL": 1,
    "BN": 7,
}
BENCH = "BN"


@dataclass(frozen=True)
class LeagueSettings:
    teams: int = 10
    budget_per_team: int = 200
    min_bid: int = 1
    roster: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_ROSTER))
    # Season games limit across all roster spots. Only about games_cap / games_per_player
    # players per team actually produce stats (the "core"); the rest of the roster
    # is depth worth the minimum bid. None = no cap, every roster spot counts.
    games_cap: int | None = 824
    games_per_player: int = 82
    # Per-team dollars for the roster spots outside the core (long shots / depth).
    bench_budget: int = 10
    # Strategy plans: most core players eligible at a position. The score is one
    # number, so this keeps plans from stacking big men (rebounds and blocks up,
    # FT% and 3PM down).
    core_position_caps: dict[str, int] = field(default_factory=lambda: {"C": 3})
    # Market behavior: players the market prices at star_price or more rarely go for
    # their average; plan on star_premium above it. Everyone else is planned at market.
    star_price: int = 40
    star_premium: float = 0.10
    # Market-price bands for finding steals: (label, low, high); high None = no cap.
    price_bands: tuple[tuple[str, int, int | None], ...] = (
        ("Core buys", 20, 40),
        ("Steal zone", 10, 20),
        ("Under $10", 0, 10),
    )
    # Replacement level = mean score of the best N undrafted players (smooths noise).
    replacement_depth: int = 3
    # Starter scarcity: how much a position's weaker starters (e.g. the 15th-best C vs the
    # 15th-best PG) add to or take from its players' value. 0 = off, 1 = full score gap.
    starter_scarcity_weight: float = 1.0
    # Tier breaks (minor cliffs): a gap to the next player at the position that is
    # at least tier_ratio x the median gap among its neighbors (tier_window on each
    # side), so smaller drops still split groups further down the list.
    tier_ratio: float = 2.0
    tier_window: int = 5
    tier_min_gap: float = 0.1
    # Backstop: a tier longer than this is split at its largest internal gap.
    max_tier_size: int = 8
    # Major cliffs: a gap above median gap + cliff_z * robust std (MAD) across the
    # whole position's draft pool, and at least cliff_min_gap in score units.
    cliff_z: float = 3.0
    cliff_min_gap: float = 0.3

    @property
    def roster_size(self) -> int:
        return sum(self.roster.values())

    @property
    def starters(self) -> int:
        return self.roster_size - self.roster.get(BENCH, 0)

    @property
    def core_size(self) -> int:
        """Players per team whose games count toward the cap."""
        if self.games_cap is None:
            return self.roster_size
        return max(self.starters, min(self.roster_size, round(self.games_cap / self.games_per_player)))

    @property
    def draft_pool_size(self) -> int:
        return self.teams * self.roster_size

    @property
    def core_pool_size(self) -> int:
        return self.teams * self.core_size

    @property
    def total_budget(self) -> int:
        return self.teams * self.budget_per_team

    @property
    def bench_spots(self) -> int:
        return self.roster_size - self.core_size

    @property
    def team_bench_budget(self) -> int:
        """Per-team bench money: at least the minimum bid per spot, zero if there's no bench."""
        return max(self.bench_budget, self.min_bid * self.bench_spots) if self.bench_spots else 0

    @property
    def core_budget(self) -> int:
        """Per-team dollars for the core."""
        return self.budget_per_team - self.team_bench_budget

    def core_roster(self) -> dict[str, int]:
        """Per-team slots for the core: every starter slot plus enough bench to reach core_size."""
        return {**self.roster, BENCH: self.core_size - self.starters}

    def league_slots(self, roster: dict[str, int] | None = None) -> dict[str, int]:
        """Slot counts across the whole league (full roster by default)."""
        return {slot: n * self.teams for slot, n in (roster or self.roster).items() if n}
