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


def is_star(df: pd.DataFrame, settings: LeagueSettings) -> pd.Series:
    """Top talent by either measure: our value or the market price at or above star_price."""
    return (df["auction_value"] >= settings.star_price) | (df["market_price"] >= settings.star_price)


def expected_price(df: pd.DataFrame, settings: LeagueSettings) -> pd.Series:
    """What to plan on paying: the market price, plus star_premium for players the market
    prices as stars (bidding wars push them past their average). A player we rate as a star
    but the market doesn't (e.g. value $46, market $26) is planned at market."""
    market = df["market_price"].fillna(df["auction_value"])
    return market.where(market < settings.star_price, market * (1 + settings.star_premium))
