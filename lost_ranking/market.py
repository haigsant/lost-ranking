"""Market prices: join an average-auction-price list and estimate what players will cost.

Our values are fair prices from one ranking. The market (e.g. ESPN average
auction prices) shows what people actually pay, hype and scarcity included.
"""

from __future__ import annotations

import pandas as pd

from .config import LeagueSettings
from .names import name_key


def add_market(df: pd.DataFrame, market: pd.DataFrame, settings: LeagueSettings) -> pd.DataFrame:
    """Join market prices by name and add market_gap and expected_price.

    Market lists only include players who go for at least the minimum, so a drafted
    player missing from the list is priced at min_bid (market_listed = False).
    """
    df = df.assign(name_key=df["player"].map(name_key)).merge(market, on="name_key", how="left").drop(columns="name_key")
    df["market_listed"] = df["market_price"].notna()
    df["market_price"] = df["market_price"].mask(~df["market_listed"] & df["drafted"], float(settings.min_bid))
    df["market_gap"] = df["auction_value"] - df["market_price"]  # > 0: market pays less than our value
    df["expected_price"] = expected_price(df, settings)
    return df


def is_market_star(df: pd.DataFrame, settings: LeagueSettings) -> pd.Series:
    """Priced like a star by the market (its bidding, not our tiers)."""
    return df["market_price"].fillna(df["auction_value"]) >= settings.market_star_price


STAR_LABELS = ("Star", "Underpriced star", "Overpriced star", "Hype")


def star_labels(df: pd.DataFrame, settings: LeagueSettings) -> pd.Series:
    """Star (by our tiers) vs the market, or "Hype" for a non-star the market prices as one.

    Without market prices, stars are just "Star".
    """
    labels = pd.Series("", index=df.index).mask(df["star"], "Star")
    if "market_price" not in df.columns:
        return labels
    gap = df["auction_value"] - df["market_price"]
    labels = labels.mask(df["star"] & (gap >= settings.star_gap), "Underpriced star")
    labels = labels.mask(df["star"] & (gap <= -settings.star_gap), "Overpriced star")
    return labels.mask(~df["star"] & df["drafted"] & is_market_star(df, settings), "Hype")


def expected_price(df: pd.DataFrame, settings: LeagueSettings, bargain_shrink: float = 0.0) -> pd.Series:
    """What to plan on paying: the market price, plus star_premium for anyone the market
    prices at market_star_price or more (bidding wars push them past their average).
    A star the market underprices (e.g. value $44, market $26) is planned at market.

    bargain_shrink > 0 also assumes the room bids away that share of every bargain
    (the gap between our value and a lower market price).
    """
    market = df["market_price"].fillna(df["auction_value"])
    price = market + bargain_shrink * (df["auction_value"] - market).clip(lower=0)
    return price.where(~is_market_star(df, settings), price * (1 + settings.star_premium))
