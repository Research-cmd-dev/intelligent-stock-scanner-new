"""News sources registry.

Default sources are constructed lazily by :func:`default_sources` so
importing this package never hits the network or reads settings.
"""

from __future__ import annotations

from .base import NewsItem, NewsSource, utc_now
from .polygon_news import PolygonNewsSource
from .yfinance_news import YFinanceNewsSource

__all__ = [
    "NewsItem",
    "NewsSource",
    "PolygonNewsSource",
    "YFinanceNewsSource",
    "default_sources",
    "utc_now",
]


def default_sources() -> list[NewsSource]:
    """Build the canonical source list: Polygon first, yfinance second.

    Order matters: when two sources surface the same article (same URL),
    the first one's record wins, so we want the higher-quality feed
    (Polygon's curated insights) to dominate.
    """
    return [PolygonNewsSource(), YFinanceNewsSource()]
