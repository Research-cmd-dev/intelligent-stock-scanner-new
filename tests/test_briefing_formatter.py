"""Tests for ``markdown_formatter._render_speculative_picks`` (Phase 3.7.3).

Picks are now split into Low Cap / Mid Cap / Above Target buckets based
on the researcher's verified ``bucket`` field, with markers:
  * ✅ verified
  * ❌ failed verification
  * ⚠️ verified bucket disagrees with the aggregator's ``target_bucket``
"""

from __future__ import annotations

from typing import Any

from src.narrative.briefing.markdown_formatter import to_markdown


def _briefing_with_picks(picks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "briefing_date": "2026-05-25",
        "generated_at": "2026-05-25T05:30:00+00:00",
        "episode_count": 1,
        "model_versions": {"haiku": "h", "sonnet": "s"},
        "episodes": [],
        "aggregation": {
            "headline": "x",
            "ticker_rollup": [],
            "themes_today": [],
            "emerging_themes": [],
            "notable_firsts": [],
            "cross_episode_observations": [],
            "speculative_picks": picks,
        },
    }


def _pick(
    ticker: str,
    *,
    target_bucket: str = "low",
    estimated_mcap_billions: float = 0.5,
    verified: bool | None = True,
    bucket: str | None = "low",
    actual_mcap_billions: float | None = None,
    thesis_matches_business: bool = True,
    actual_business_summary: str = "",
    recent_news_supporting: str | None = None,
    recent_news_contradicting: str | None = None,
    verification_notes: str = "",
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ticker": ticker,
        "name": f"{ticker} Inc",
        "estimated_mcap_billions": estimated_mcap_billions,
        "thesis": f"Thesis for {ticker}.",
        "narrative_source": "Source X",
        "tickers_amplified_by": ["NVDA"],
        "conviction": "medium",
        "target_bucket": target_bucket,
    }
    if verified is not None:
        base["verified"] = verified
    if bucket is not None:
        base["bucket"] = bucket
    if actual_mcap_billions is not None:
        base["actual_mcap_billions"] = actual_mcap_billions
    base["thesis_matches_business"] = thesis_matches_business
    base["actual_business_summary"] = actual_business_summary
    base["recent_news_supporting"] = recent_news_supporting
    base["recent_news_contradicting"] = recent_news_contradicting
    base["verification_notes"] = verification_notes
    return base


# --------------------------------------------------------------------- #
# Section presence                                                       #
# --------------------------------------------------------------------- #


def test_empty_picks_omits_section_entirely() -> None:
    md = to_markdown(_briefing_with_picks([]))
    assert "Speculative Picks" not in md
    assert "Low Cap" not in md
    assert "Mid Cap" not in md


def test_low_only_renders_only_low_subsection() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick("SERV", target_bucket="low", bucket="low", actual_mcap_billions=0.4),
    ]))
    assert "### Low Cap ($300M-$2B)" in md
    assert "### Mid Cap" not in md
    assert "Reclassified Above Target" not in md
    assert "SERV" in md


def test_mixed_buckets_render_three_subsections() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick("LOWA", target_bucket="low", bucket="low", actual_mcap_billions=0.4),
        _pick("MIDA", target_bucket="mid", bucket="mid", actual_mcap_billions=5.0),
        _pick("BIGA", target_bucket="low", bucket="large", actual_mcap_billions=30.0),
    ]))
    assert "### Low Cap ($300M-$2B)" in md
    assert "### Mid Cap ($2B-$10B)" in md
    assert "### Reclassified Above Target ($10B+)" in md
    assert "LOWA" in md and "MIDA" in md and "BIGA" in md


# --------------------------------------------------------------------- #
# Markers                                                                #
# --------------------------------------------------------------------- #


def test_verified_pick_shows_checkmark() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick("SERV", verified=True, bucket="low"),
    ]))
    # ✅ marker should appear next to the verified pick
    assert "✅" in md


def test_failed_verification_shows_x_marker() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick(
            "FAKE",
            verified=False,
            bucket="unknown",
            verification_notes="ticker not found",
        ),
    ]))
    assert "❌" in md
    assert "ticker not found" in md


def test_bucket_disagreement_shows_warning_marker() -> None:
    """target_bucket=low but verified bucket=mid → ⚠️ on the line."""
    md = to_markdown(_briefing_with_picks([
        _pick(
            "DISAGREE",
            target_bucket="low",
            bucket="mid",
            actual_mcap_billions=4.0,
        ),
    ]))
    assert "⚠️" in md
    assert "DISAGREE" in md


def test_bucket_match_does_not_show_warning_marker() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick(
            "MATCH",
            target_bucket="low",
            bucket="low",
            actual_mcap_billions=0.5,
        ),
    ]))
    # ✅ but no ⚠️
    assert "✅" in md
    # Find the MATCH line and confirm ⚠️ isn't on it.
    for line in md.splitlines():
        if "MATCH" in line and line.startswith("**"):
            assert "⚠️" not in line


# --------------------------------------------------------------------- #
# Market-cap rendering                                                   #
# --------------------------------------------------------------------- #


def test_actual_mcap_preferred_over_estimate() -> None:
    """When the researcher returns an actual mcap, it overrides the estimate."""
    md = to_markdown(_briefing_with_picks([
        _pick(
            "REAL",
            estimated_mcap_billions=10.0,
            actual_mcap_billions=0.5,
            bucket="low",
        ),
    ]))
    assert "actual $500M" in md or "actual $0.5B" in md
    # Estimated should not appear for this pick (we only check the header line).
    for line in md.splitlines():
        if "REAL" in line and "actual" in line:
            assert "est." not in line


def test_estimate_used_when_actual_missing() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick(
            "ESTONLY",
            estimated_mcap_billions=1.2,
            actual_mcap_billions=None,
            bucket="low",
            verified=False,
        ),
    ]))
    assert "est." in md
    assert "$1.2B" in md


def test_unknown_mcap_renders_placeholder() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick(
            "NOMCAP",
            estimated_mcap_billions=None,  # type: ignore[arg-type]
            actual_mcap_billions=None,
            bucket="unknown",
            verified=False,
        ),
    ]))
    assert "mcap unknown" in md


# --------------------------------------------------------------------- #
# Caps                                                                   #
# --------------------------------------------------------------------- #


def test_per_bucket_render_cap_at_five() -> None:
    seven_low = [
        _pick(f"L{i}", target_bucket="low", bucket="low", actual_mcap_billions=0.5)
        for i in range(7)
    ]
    md = to_markdown(_briefing_with_picks(seven_low))
    # First 5 render, 6th and 7th drop.
    for i in range(5):
        assert f"L{i}" in md
    assert "L5" not in md
    assert "L6" not in md


# --------------------------------------------------------------------- #
# Unverified fallback bucketing                                          #
# --------------------------------------------------------------------- #


def test_unverified_pick_falls_back_to_target_bucket() -> None:
    """If verification failed, place the pick in whichever subsection the
    aggregator originally targeted, so the user still sees it."""
    md = to_markdown(_briefing_with_picks([
        _pick(
            "FAILMID",
            target_bucket="mid",
            bucket="unknown",
            actual_mcap_billions=None,
            verified=False,
        ),
    ]))
    assert "### Mid Cap" in md
    assert "FAILMID" in md
    assert "❌" in md


# --------------------------------------------------------------------- #
# Body fields                                                            #
# --------------------------------------------------------------------- #


def test_actual_business_summary_renders_when_present() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick(
            "BIZ",
            bucket="low",
            actual_business_summary="Robotaxi technology developer.",
        ),
    ]))
    assert "Actual business:" in md
    assert "Robotaxi technology developer." in md


def test_supporting_and_contradicting_news_render() -> None:
    md = to_markdown(_briefing_with_picks([
        _pick(
            "NEWS",
            bucket="low",
            recent_news_supporting="Partnership announced Tuesday.",
            recent_news_contradicting="DOJ probe disclosed.",
        ),
    ]))
    assert "Supporting news:" in md
    assert "Partnership announced Tuesday." in md
    assert "Contradicting:" in md
    assert "DOJ probe disclosed." in md


# --------------------------------------------------------------------- #
# Delisted-stock marker (regression: CIVI on 2026-05-26)                #
# --------------------------------------------------------------------- #


def _find_pick_line(md: str, ticker: str) -> str:
    for line in md.splitlines():
        if line.startswith(f"**{ticker}"):
            return line
    raise AssertionError(f"{ticker} pick line not found in markdown")


def test_delisted_stock_flips_check_to_x_despite_verified_true() -> None:
    """Regression: CIVI on 2026-05-26 was rendered ✅ despite the verifier
    surfacing that SM Energy completed an all-stock merger and CIVI was
    delisted on Jan 30, 2026."""
    md = to_markdown(_briefing_with_picks([
        _pick(
            "CIVI",
            target_bucket="mid",
            bucket="mid",
            actual_mcap_billions=2.3,
            verified=True,
            recent_news_contradicting=(
                "SM Energy completed an all-stock merger with Civitas on "
                "January 30, 2026, delisting CIVI."
            ),
        ),
    ]))
    line = _find_pick_line(md, "CIVI")
    assert "❌" in line
    assert "✅" not in line


def test_acquired_company_flips_check_to_x_despite_verified_true() -> None:
    """Same logic for outright acquisitions (ARX/Archaea case)."""
    md = to_markdown(_briefing_with_picks([
        _pick(
            "ARX",
            target_bucket="low",
            bucket="low",
            actual_mcap_billions=1.1,
            verified=True,
            recent_news_contradicting=(
                "Archaea Energy was acquired by bp in December 2022 and is "
                "now a wholly-owned subsidiary."
            ),
        ),
    ]))
    line = _find_pick_line(md, "ARX")
    assert "❌" in line
    assert "✅" not in line


def test_verified_pick_with_normal_contradicting_news_still_shows_check() -> None:
    """Baseline: verified pick with non-delisting contradicting news keeps ✅."""
    md = to_markdown(_briefing_with_picks([
        _pick(
            "AEHR",
            bucket="low",
            actual_mcap_billions=0.5,
            verified=True,
            recent_news_contradicting=(
                "Insider selling of ~$35M net over 12 months raises some "
                "shareholder alignment concerns."
            ),
        ),
    ]))
    line = _find_pick_line(md, "AEHR")
    assert "✅" in line
    assert "❌" not in line
