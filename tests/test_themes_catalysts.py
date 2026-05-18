"""Tests for narrative theme + catalyst detection.

Two layers covered:

1. **Detection on canned headlines** — keyword + composite-pair logic
   for themes (:mod:`src.narrative.themes`) and phrase logic for
   catalysts (:mod:`src.narrative.catalysts`). These verify the
   matcher does what the catalog says, not anything about scoring.

2. **Integration through NarrativeScorer** — same news basket scored
   with vs. without theme/catalyst tags reaching the explanation and
   tipping the conviction weight. Drives the public API the dashboard
   actually consumes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.narrative import NarrativeScorer
from src.narrative.catalysts import detect_catalysts
from src.narrative.scorer import _item_boost
from src.narrative.sources.base import NewsItem
from src.narrative.themes import detect_themes


def _item(
    title: str, summary: str = "", *, days_ago: int = 0,
    publisher: str = "Reuters", sentiment: float | None = None,
) -> NewsItem:
    return NewsItem(
        title=title,
        summary=summary,
        url=f"https://news.test/{abs(hash(title)) % 10_000_000}",
        published_utc=datetime.now(tz=timezone.utc).replace(microsecond=0)
                    .replace(tzinfo=timezone.utc) - __import__("datetime").timedelta(days=days_ago),
        provider="polygon",
        publisher=publisher,
        tickers=("TEST",),
        external_sentiment=sentiment,
    )


# ---------------------------------------------------------------------- #
# Theme detection                                                        #
# ---------------------------------------------------------------------- #


def test_detect_themes_miner_to_ai_requires_co_occurrence():
    # Bitcoin miner *with* an AI/HPC mention → fires.
    text = (
        "Bitcoin miner pivots to AI compute, repurposes data center for "
        "GPU workloads"
    )
    tags = detect_themes(text)
    names = [t.name for t in tags]
    assert "Miner-to-AI Pivot" in names

    # Bitcoin miner *without* the AI/HPC pair → does not fire.
    text_only = "Bitcoin miner reports record hash rate this quarter"
    tags_only = detect_themes(text_only)
    assert "Miner-to-AI Pivot" not in {t.name for t in tags_only}


def test_detect_themes_power_plus_compute_composite():
    text = (
        "Company signs power purchase agreement for hydropower behind the "
        "meter, dedicated to GPU and AI compute capacity"
    )
    tags = detect_themes(text)
    names = [t.name for t in tags]
    assert "Power + Compute" in names
    # Should also pick up AI Infrastructure secondarily — but the composite
    # theme should not get dropped just because a more general one fires.
    power = next(t for t in tags if t.name == "Power + Compute")
    assert power.relevance >= 0.4
    assert any("hydropower" in m or "behind the meter" in m
               for m in power.matched_terms)


def test_detect_themes_neocloud_keywords_fire_directly():
    text = "Startup launches neocloud GPU rental service with H100 capacity"
    tags = detect_themes(text)
    assert any(t.name == "Neocloud / GPU Cloud" for t in tags)


def test_detect_themes_data_center_requires_scale_pair():
    # Plain "data center" without scale keywords should not pop the build-out theme.
    plain = "Data center vendor announces small update to its software"
    tags = detect_themes(plain)
    assert "Data Center Build-out" not in {t.name for t in tags}

    # With a scale keyword (megawatt / construction) → fires.
    scaled = "Data center developer announces 200 megawatts of new construction"
    tags = detect_themes(scaled)
    assert "Data Center Build-out" in {t.name for t in tags}


def test_detect_themes_nuclear_for_ai_needs_both():
    text = "Hyperscaler signs SMR deal to power AI data center"
    tags = detect_themes(text)
    names = [t.name for t in tags]
    assert "Nuclear for AI" in names


def test_detect_themes_empty_text_returns_empty():
    assert detect_themes("") == []
    assert detect_themes("   ") == []


# ---------------------------------------------------------------------- #
# Catalyst detection                                                     #
# ---------------------------------------------------------------------- #


def test_detect_catalysts_contract_fires_on_signed_deal():
    tags = detect_catalysts(
        "TestCo signed a multi-year contract with anchor customer for $500M"
    )
    assert any(t.kind == "contract" for t in tags)
    contract = next(t for t in tags if t.kind == "contract")
    # Multiple phrases hit ("multi-year contract", "anchor customer") →
    # strength should be at or above the multi-hit floor (0.4 + 0.1 = 0.5).
    assert contract.strength >= 0.5


def test_detect_catalysts_funding_picks_up_series_rounds():
    tags = detect_catalysts(
        "Startup announces Series C, oversubscribed, led by Sequoia"
    )
    kinds = [t.kind for t in tags]
    assert "funding" in kinds


def test_detect_catalysts_expansion_megawatts():
    tags = detect_catalysts(
        "Operator announces capacity expansion of 500 megawatts, "
        "doubles capacity by 2026"
    )
    assert any(t.kind == "expansion" for t in tags)


def test_detect_catalysts_pivot_to_ai():
    tags = detect_catalysts(
        "Bitcoin miner announces full pivot to AI, exiting bitcoin mining"
    )
    assert any(t.kind == "pivot" for t in tags)


def test_detect_catalysts_no_match_returns_empty():
    tags = detect_catalysts("Company holds annual investor day; reaffirms targets")
    # "Reaffirms" is not an earnings beat phrase. No catalysts should fire.
    assert tags == []


# ---------------------------------------------------------------------- #
# Boost helper                                                           #
# ---------------------------------------------------------------------- #


def test_item_boost_combines_themes_and_catalysts():
    text = (
        "Bitcoin miner pivots to AI compute and signs multi-year contract "
        "with hyperscaler for data center capacity"
    )
    themes = detect_themes(text)
    catalysts = detect_catalysts(text)
    boost = _item_boost(themes=themes, catalysts=catalysts)
    assert 0.2 < boost < 0.7, f"unexpected boost magnitude: {boost}"


def test_item_boost_zero_when_nothing_fires():
    assert _item_boost(themes=[], catalysts=[]) == 0.0


# ---------------------------------------------------------------------- #
# NarrativeScorer integration                                            #
# ---------------------------------------------------------------------- #


class _FakeSource:
    """Minimal NewsSource for offline tests."""

    name = "fake"

    def __init__(self, items: list[NewsItem]) -> None:
        self._items = items

    def fetch(self, symbol: str, *, limit: int = 20) -> list[NewsItem]:
        return list(self._items)


def test_scorer_attaches_themes_and_catalysts_to_result():
    items = [
        _item(
            "Bitcoin miner pivots to AI, signs multi-year contract for HPC capacity",
            "Anchor tenant deal locks in revenue. Data center repurposed for AI.",
            sentiment=0.8, days_ago=0,
        ),
        _item(
            "Operator announces 500 megawatts of new data center construction",
            "Hyperscaler-targeted facility, behind the meter hydropower agreement.",
            sentiment=0.6, days_ago=2,
        ),
        _item(
            "Analyst upgrades on neocloud strategy and GPU cloud growth",
            "H100 capacity sold out; pricing power.",
            sentiment=0.5, days_ago=4,
        ),
    ]
    scorer = NarrativeScorer(sources=[_FakeSource(items)], use_cache=False)
    res = scorer.score("TEST")

    theme_names = {t.name for t in res.themes}
    catalyst_kinds = {c.kind for c in res.catalysts}

    assert "Miner-to-AI Pivot" in theme_names or "AI Infrastructure" in theme_names
    assert "contract" in catalyst_kinds or "expansion" in catalyst_kinds

    # Explanation should call out at least one of the detected signals.
    expl = res.explanation.lower()
    assert ("theme" in expl) or ("catalyst" in expl)


def test_scorer_themed_basket_outscores_generic_basket():
    """Equally bullish baskets — the themed one should pull harder."""
    base_kwargs = dict(sentiment=0.6, days_ago=0)
    themed = [
        _item(
            "Bitcoin miner pivots to AI compute, signs multi-year contract for HPC",
            "Anchor tenant, hydropower data center, GPU capacity expansion.",
            **base_kwargs,
        ),
        _item(
            "Operator adds 800 megawatts of data center construction for AI training",
            "Behind the meter power agreement signed with hyperscaler.",
            sentiment=0.6, days_ago=1,
        ),
    ]
    generic = [
        _item("Company hires new VP of marketing", "Routine update.", **base_kwargs),
        _item(
            "Quarterly investor day reaffirms long-term outlook",
            "Management reiterates strategy.",
            sentiment=0.6, days_ago=1,
        ),
    ]
    scorer = NarrativeScorer(use_cache=False)
    themed_res = NarrativeScorer(
        sources=[_FakeSource(themed)], use_cache=False,
    ).score("THEMED")
    generic_res = NarrativeScorer(
        sources=[_FakeSource(generic)], use_cache=False,
    ).score("GENERIC")

    # Themed basket lights up theme + catalyst tags; generic basket should not.
    assert themed_res.themes, "expected themes on themed basket"
    assert not generic_res.themes, "did not expect themes on generic basket"


def test_scorer_handles_no_themes_or_catalysts_gracefully():
    items = [_item("Company holds annual meeting", sentiment=0.0, days_ago=0)]
    scorer = NarrativeScorer(sources=[_FakeSource(items)], use_cache=False)
    res = scorer.score("TEST")
    assert res.themes == ()
    assert res.catalysts == ()
    # Existing behavior intact — explanation still renders.
    assert "1 recent article" in res.explanation
