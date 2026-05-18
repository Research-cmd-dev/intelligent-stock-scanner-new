"""Hand-built :class:`NewsItem` fixtures + a fake NewsSource for tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.narrative.sources.base import NewsItem


def _utc(days_ago: int = 0, hours: int = 0) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=days_ago, hours=hours)


def bullish_items(symbol: str = "FAKE") -> list[NewsItem]:
    return [
        NewsItem(
            title=f"{symbol} beats earnings and raises full-year guidance",
            summary="Revenue ahead of estimates; CEO upbeat on momentum.",
            url=f"https://news.test/{symbol}/beat",
            published_utc=_utc(days_ago=0, hours=2),
            provider="polygon",
            publisher="Reuters",
            tickers=(symbol,),
            external_sentiment=0.7,
        ),
        NewsItem(
            title=f"{symbol} awarded multi-year contract worth $1B",
            summary="New partnership accelerates expansion into adjacent markets.",
            url=f"https://news.test/{symbol}/contract",
            published_utc=_utc(days_ago=1),
            provider="yfinance",
            publisher="CNBC",
            tickers=(symbol,),
        ),
        NewsItem(
            title=f"Analyst upgrades {symbol} to Buy on accelerating momentum",
            summary="Price target raised; bullish on AI exposure.",
            url=f"https://news.test/{symbol}/upgrade",
            published_utc=_utc(days_ago=3),
            provider="yfinance",
            publisher="Bloomberg",
            tickers=(symbol,),
        ),
    ]


def bearish_items(symbol: str = "FAKE") -> list[NewsItem]:
    return [
        NewsItem(
            title=f"{symbol} misses estimates, cuts guidance",
            summary="Weak quarter; warning issued on next half.",
            url=f"https://news.test/{symbol}/miss",
            published_utc=_utc(days_ago=0),
            provider="polygon",
            publisher="Reuters",
            tickers=(symbol,),
            external_sentiment=-0.7,
        ),
        NewsItem(
            title=f"{symbol} faces SEC probe over accounting concerns",
            summary="Stock plunges on lawsuit and downgrade.",
            url=f"https://news.test/{symbol}/probe",
            published_utc=_utc(days_ago=2),
            provider="yfinance",
            publisher="WSJ",
            tickers=(symbol,),
        ),
    ]


def stale_items(symbol: str = "FAKE") -> list[NewsItem]:
    """Items older than the default max_age_days, should be dropped."""
    return [
        NewsItem(
            title=f"{symbol} ancient news from a quarter ago",
            summary="Old.",
            url=f"https://news.test/{symbol}/old",
            published_utc=_utc(days_ago=120),
            provider="polygon",
            publisher="Reuters",
            tickers=(symbol,),
        ),
    ]


class FakeSource:
    """In-memory NewsSource that returns a fixed list for any symbol."""

    def __init__(self, items: list[NewsItem], name: str = "fake") -> None:
        self.items = items
        self.name = name

    def fetch(self, symbol: str, *, limit: int = 20) -> list[NewsItem]:
        return list(self.items[:limit])
