"""League settings that drive every valuation step."""

from __future__ import annotations

from dataclasses import dataclass, field

# Yahoo default 9-cat roster: 10 starters + 3 bench = 13 per team.
DEFAULT_ROSTER: dict[str, int] = {
    "PG": 1,
    "SG": 1,
    "G": 1,
    "SF": 1,
    "PF": 1,
    "F": 1,
    "C": 2,
    "UTIL": 2,
    "BN": 3,
}


@dataclass(frozen=True)
class LeagueSettings:
    teams: int = 10
    budget_per_team: int = 200
    min_bid: int = 1
    roster: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_ROSTER))
    # Replacement level = mean score of the best N undrafted players (smooths noise).
    replacement_depth: int = 3
    # A gap to the next player is a "cliff" when it exceeds
    # median gap + cliff_z * robust std (MAD) within the position.
    cliff_z: float = 3.0
    # ...and is at least this large in raw score units (ignores tiny noise).
    cliff_min_gap: float = 0.3

    @property
    def roster_size(self) -> int:
        return sum(self.roster.values())

    @property
    def draft_pool_size(self) -> int:
        return self.teams * self.roster_size

    @property
    def total_budget(self) -> int:
        return self.teams * self.budget_per_team

    def league_slots(self) -> dict[str, int]:
        """Slot counts across the whole league."""
        return {slot: n * self.teams for slot, n in self.roster.items()}
