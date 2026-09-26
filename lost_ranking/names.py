"""Player-name keys for joining sources that spell names differently."""

from __future__ import annotations

import re
import unicodedata

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def name_key(name: str) -> str:
    """'Nikola Jokić' and 'Nikola Jokic' -> 'nikolajokic'; drops Jr./III and punctuation."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z ]", "", ascii_name.lower().replace("-", " ")).split()
    return "".join(w for w in words if w not in SUFFIXES)
