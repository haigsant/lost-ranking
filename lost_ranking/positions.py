"""Position parsing and roster-slot eligibility."""

from __future__ import annotations

# Positions used for scarcity/replacement analysis. G and F are composite slots.
BASE_POSITIONS: tuple[str, ...] = ("PG", "SG", "SF", "PF", "C")

# Which position tokens may fill each slot. None = any player.
SLOT_ELIGIBILITY: dict[str, frozenset[str] | None] = {
    "PG": frozenset({"PG"}),
    "SG": frozenset({"SG"}),
    "SF": frozenset({"SF"}),
    "PF": frozenset({"PF"}),
    "C": frozenset({"C"}),
    "G": frozenset({"PG", "SG", "G"}),
    "F": frozenset({"SF", "PF", "F"}),
    "PF/C": frozenset({"PF", "C"}),
    "UTIL": None,
    "BN": None,
}


def parse_positions(raw: str | float | None) -> frozenset[str]:
    """'PG/SG/G' -> {'PG', 'SG', 'G'}. Blank/NaN -> empty set."""
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(p.strip().upper() for p in raw.split("/") if p.strip())


def base_positions(positions: frozenset[str]) -> tuple[str, ...]:
    """Base positions a player qualifies at, in canonical order."""
    return tuple(p for p in BASE_POSITIONS if p in positions)


def can_fill(slot: str, positions: frozenset[str]) -> bool:
    allowed = SLOT_ELIGIBILITY[slot]
    return allowed is None or bool(allowed & positions)


def slot_priority(slots: list[str]) -> list[str]:
    """Order slots most-restrictive first so flexible slots stay open longest."""

    def restrictiveness(slot: str) -> tuple[int, int]:
        allowed = SLOT_ELIGIBILITY[slot]
        # Bench after UTIL so starters are filled before depth.
        if allowed is None:
            return (1, 1 if slot == "BN" else 0)
        return (0, len(allowed))

    return sorted(slots, key=restrictiveness)
