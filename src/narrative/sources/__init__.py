"""News sources registry.

Default sources are constructed lazily by :func:`default_sources` so
importing this package never hits the network or reads settings.
"""

from __future__ import annotations

from .base import NewsItem, NewsSource, utc_now
from .polygon_news import PolygonNewsSource
from .x_accounts import HIGH_QUALITY_X_ACCOUNTS
from .x_news import XAccountsNewsSource
from .yfinance_news import YFinanceNewsSource

__all__ = [
    "NewsItem",
    "NewsSource",
    "PolygonNewsSource",
    "YFinanceNewsSource",
    "XAccountsNewsSource",
    "HIGH_QUALITY_X_ACCOUNTS",
    "default_sources",
    "utc_now",
]


def default_sources() -> list[NewsSource]:
    """Build the canonical source list.

    Polygon first (highest quality, has external sentiment), yfinance second,
    and the optional X high-quality accounts source third when a bearer token
    is configured. X is deliberately last so traditional news wins on dedup.
    """
    from src.config import get_settings

    srcs: list[NewsSource] = [PolygonNewsSource(), YFinanceNewsSource()]

    settings = get_settings()
    if settings.has_x:
        srcs.append(XAccountsNewsSource())

    return srcs
