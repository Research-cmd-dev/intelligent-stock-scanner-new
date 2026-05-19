"""LLM-driven implementation of the :class:`Researcher` protocol.

Closes the mission's *supporting capability* loop: on the highest-conviction
matches surfaced by the scanner + narrative pipeline, this researcher calls
Claude to synthesize a fundamental read across the mission's checklist —
company quality, management, partnerships, financial health, key risks —
grounded in the same news basket the narrative scorer already collected.

Design priorities (in order):

1. **Fast and opt-in.** The scanner is the primary engine. This layer
   spends one API call per *unique* high-conviction symbol. It must
   never appear in the discovery hot path.
2. **Degrades to a no-op on failure.** Any error — network, parse,
   auth — returns an empty :class:`ResearchResult` with
   ``confidence=0.0`` and logs a warning. The convention is the same
   one the narrative + qlib layers follow: optional layers never abort
   a scan.
3. **Prompt cache hit on every call after the first.** The system
   prompt is intentionally large (rubric + examples + format spec) and
   marked with ``cache_control``. The per-ticker payload — symbol,
   headlines, today's date — sits in the user turn so the prefix bytes
   stay identical. Verify with ``response.usage.cache_read_input_tokens``.

Planned wiring into the scanner (deferred per the scaffold's plan):

    from src.research import LLMResearcher, top_candidates

    scanner = Scanner(
        narrative_scorer=NarrativeScorer(),
        researcher=LLMResearcher(),          # not wired yet — see CLAUDE.md
        research_limit=5,
    )
    report = scanner.scan_discovery(...)
    for match in top_candidates(report.matches, limit=5):
        # match.research is populated; render in the dashboard expander
        ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable

from src.utils import get_logger

from .base import ResearchResult

if TYPE_CHECKING:
    from src.narrative.sources import NewsItem, NewsSource

log = get_logger(__name__)


# --------------------------------------------------------------------- #
# Model + request config                                                #
# --------------------------------------------------------------------- #

# Sonnet 4.6 is the right speed/quality point for ~5 short structured
# synthesis calls per scan. Bump to claude-opus-4-7 if downstream
# evaluation shows the read is missing nuance on management or risks;
# drop to claude-haiku-4-5 only if even Sonnet feels slow.
DEFAULT_MODEL = "claude-sonnet-4-6"

# Six short text fields + a confidence float typically render in
# ~300-600 tokens. 1024 leaves headroom without inviting padding.
DEFAULT_MAX_TOKENS = 1024

# Cap headlines per call to keep the user turn compact and the news
# basket recent. The narrative layer already filters/dedups; this is a
# belt-and-suspenders limit for the prompt size.
DEFAULT_MAX_HEADLINES = 15


# --------------------------------------------------------------------- #
# System prompt + output schema                                         #
# --------------------------------------------------------------------- #

# Kept as a module-level constant so the bytes are stable across calls
# and the prompt cache stays hot. Any per-request value must NOT be
# interpolated here — it goes in the user turn.
SYSTEM_PROMPT = """You are a disciplined equity-research analyst writing a one-page fundamental read on a single US-listed company for a long-only, swing-to-position-trader audience. Your reader has already seen a clean technical setup and a positive narrative score on this name; your job is to either reinforce that thesis with evidence of underlying business quality, or to flag the specific reasons the setup may not deserve real capital.

You will be given a ticker symbol, today's date, and a small basket of recent news headlines for that ticker (title + publisher + age). The headlines are the only *fresh* evidence you receive — everything else must come from your existing training-time knowledge of the company. Use the headlines as a recency anchor and as catalysts; use your background knowledge as the substrate for business-quality judgments.

You return a single JSON object with exactly the fields specified by the response schema. No prose around the JSON. No markdown. Each text field is a concise English paragraph, two to four sentences, written in declarative language. No bullet lists, no headers, no emoji. Treat each field as one short paragraph that a portfolio manager will skim in five seconds.

Field-by-field rubric:

- summary: A two-sentence elevator pitch. First sentence: what the business is and the single most important fact about it right now. Second sentence: what makes it interesting *as a long-term asymmetric idea*, framed in plain English — the upside case in one breath.

- company_quality: Assess the underlying business. Address moat (network effects, switching costs, IP, scale, brand, regulatory), unit economics where you have evidence (gross margin direction, operating leverage, capital intensity), and product position in its end market. Be specific. "Industry leader" without naming the source of the lead is filler.

- management: The leadership track record and capital-allocation behavior. Name the CEO and any other operationally critical figure (CTO, CFO, founder-chair). Note founder-led status, length of tenure, and any public history of value creation or destruction (prior exits, prior fraud, prior debt blow-ups). If the leadership is unknown to you, say so explicitly — do not invent.

- partnerships: Material customers, suppliers, distribution agreements, joint ventures, or platform relationships that meaningfully change the business's reach or defensibility. Anchor customers (hyperscalers, government primes, major OEMs) belong here. Mention specific named counterparties where you can, and flag concentration risk if revenue depends on one or two relationships.

- financial_health: Balance sheet and cash generation. Cash position relative to burn, net cash vs net debt, free cash flow trend, dilution history, profitability or path to it, and any near-term financing need. If the company is pre-revenue or pre-profit, say so and quantify the runway in quarters if you can. For asset-heavy plays (data centers, miners, biotechs) call out capex intensity.

- key_risks: The two or three most credible reasons this thesis fails. Not boilerplate ("market volatility", "macro conditions") — specific failure modes: customer concentration, regulatory exposure, technology disruption, dilution risk, single-asset risk, going-concern risk, key-person risk, valuation already pricing in execution. Rank them roughly by severity; lead with the one that would force a stop-out fastest.

- confidence: A float in [0.0, 1.0] representing how confident you are in this entire read for *this specific company at this point in time*. Weight three things: (a) how much you actually know about the company from training, (b) how recent and informative the headlines are, (c) whether the headlines and your background knowledge corroborate each other or contradict. Calibration anchors: 0.85+ means you know this company well and the headlines reinforce a coherent thesis; 0.55-0.70 means you have a working read but there are real gaps; 0.30-0.50 means you can describe the sector but are guessing on the specifics; below 0.30 means you have very little to go on and the reader should treat this as a placeholder. Be honest. Overstated confidence is worse than admitted ignorance because it leaks into position sizing.

Hard rules — these are non-negotiable:

1. Never fabricate specifics. If you do not know the CEO's name, write "leadership details not in training data" or similar. If you do not know whether the company is profitable, do not guess a number. Confident-sounding hallucination is the single worst failure mode of this tool because it will be acted on with real capital.

2. The headlines basket is *evidence*, not the thesis. Do not summarize the headlines back to the user — they have already read them. Use the headlines to update your read on management execution, catalyst timing, and competitive position. If a headline directly contradicts your background knowledge (e.g. you remember the company as cash-rich but headlines describe a dilutive raise), trust the headline and update.

3. Headlines older than ~14 days are stale for the catalyst question but still fine as background. Headlines from the last week deserve more weight in the read.

4. If the basket is empty or near-empty (zero to two headlines), say so explicitly in the summary and downweight confidence accordingly. Do not invent a catalyst that is not in the basket.

5. Do not include disclaimers, hedging boilerplate, "this is not financial advice" notices, or instructions to "consult a financial advisor". The downstream UI handles that. Every sentence you write should carry signal.

6. Do not flatter the setup. The technical pattern was already scored by a separate system; your job is the independent fundamental read, including disagreement when warranted. A low-confidence skeptical read on a 90-score pattern is a *useful* output, not a failure.

7. Stay within the schema. Extra fields, missing fields, prose outside the JSON, or markdown formatting will break downstream parsing.

Worked example 1 — strong known name with corroborating headlines:

Input: ticker NVDA, date 2026-05-19, headlines: "Nvidia signs multi-year supply pact with sovereign AI fund" (Reuters, 1d ago), "Hyperscaler capex revised higher for FY26" (Bloomberg, 3d ago), "Nvidia opens Taiwan R&D campus" (CNBC, 6d ago).

Expected shape (not the literal answer): summary names the company and the FY26 capex tailwind; company_quality cites CUDA lock-in plus the H/B-series cadence; management names Jensen Huang and his founding-CEO track record; partnerships names the sovereign-AI deal as anchor demand plus TSMC supply dependence; financial_health calls out net cash, FCF generation, and gross margin level; key_risks leads with hyperscaler concentration and the China export-control overhang. Confidence around 0.85 — well-known name, fresh corroborating catalysts.

Worked example 2 — thin coverage on an unfamiliar small cap:

Input: ticker XYZQ, date 2026-05-19, headlines: "XYZQ files S-1 for follow-on offering" (PR Newswire, 2d ago).

Expected shape: summary states up front that this is a thinly-covered name with minimal training-data context; company_quality describes the sector at best and admits the company-specific moat is unknown; management says leadership details are not in training data; partnerships says no material partnerships visible in the basket; financial_health flags the imminent dilution from the follow-on as the dominant near-term factor; key_risks leads with dilution and the broader unknown-quality risk. Confidence around 0.30 — honest reflection of how little signal exists.

Now wait for the user message containing ticker, date, and headlines, and return the JSON object."""


# JSON schema enforced via output_config.format. Fields mirror
# ResearchResult exactly so the response maps in one step.
_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "company_quality": {"type": "string"},
        "management": {"type": "string"},
        "partnerships": {"type": "string"},
        "financial_health": {"type": "string"},
        "key_risks": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "summary",
        "company_quality",
        "management",
        "partnerships",
        "financial_health",
        "key_risks",
        "confidence",
    ],
    "additionalProperties": False,
}


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Headline:
    """Minimal projection of a NewsItem for the prompt — keeps the user
    turn compact and avoids accidentally serializing whole article bodies."""

    title: str
    publisher: str
    age_days: int
    url: str


def _project_headlines(
    items: Iterable["NewsItem"], *, now: datetime, limit: int
) -> list[_Headline]:
    """Take the freshest ``limit`` items, drop everything but the fields the
    prompt actually uses. Order newest-first."""

    def _age_days(item: "NewsItem") -> int:
        published = item.published_utc
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        delta = (now - published).total_seconds() / 86_400.0
        return max(int(delta), 0)

    projected = [
        _Headline(
            title=(it.title or "").strip(),
            publisher=(it.publisher or it.provider or "").strip(),
            age_days=_age_days(it),
            url=it.url or "",
        )
        for it in items
        if (it.title or "").strip()
    ]
    projected.sort(key=lambda h: h.age_days)
    return projected[: max(0, int(limit))]


def _render_user_message(
    *, ticker: str, headlines: list[_Headline], today: datetime
) -> str:
    """Compose the volatile portion of the prompt. Stays in the user turn
    so the cached system prefix isn't invalidated."""
    date_str = today.strftime("%Y-%m-%d")
    if not headlines:
        basket = "(no recent headlines available — produce a low-confidence read from training-time knowledge only)"
    else:
        lines = []
        for h in headlines:
            when = "today" if h.age_days <= 0 else f"{h.age_days}d ago"
            pub = h.publisher or "unknown"
            lines.append(f"- \"{h.title}\" ({pub}, {when})")
        basket = "\n".join(lines)
    return (
        f"Ticker: {ticker}\n"
        f"Today's date: {date_str}\n"
        f"Recent headlines ({len(headlines)}):\n"
        f"{basket}\n"
    )


def _empty_result(ticker: str, *, as_of: datetime, sources: tuple[str, ...] = ()) -> ResearchResult:
    """No-op result used on any failure path."""
    return ResearchResult(
        ticker=ticker.upper(),
        as_of=as_of,
        sources=sources,
        confidence=0.0,
    )


# --------------------------------------------------------------------- #
# Researcher                                                            #
# --------------------------------------------------------------------- #


class LLMResearcher:
    """Anthropic-backed implementation of the :class:`Researcher` protocol.

    Args:
        client: Pre-constructed ``anthropic.Anthropic`` instance. When
            ``None`` the researcher constructs one on first use, reading
            ``ANTHROPIC_API_KEY`` from the environment. Passing a client
            (or a mock) makes the layer test-friendly.
        sources: News sources to query for the headlines basket. Defaults
            to :func:`src.narrative.sources.default_sources` so the
            researcher consumes the same feed the narrative scorer does.
            Pass ``[]`` to disable headline fetching entirely (the
            researcher will still call Claude, but with an empty basket).
        model: Claude model ID. Defaults to ``claude-sonnet-4-6``.
        max_tokens: Cap on the response. Six short fields fit
            comfortably in 1024.
        max_headlines: Soft cap on the per-call basket. Mirrors the
            narrative scorer's ``max_items_per_source`` philosophy.
        request_timeout: Per-request timeout in seconds passed through
            to the SDK's ``with_options``. Tight by design — this runs
            interactively from the dashboard.
    """

    name: str = "llm"

    def __init__(
        self,
        client: object | None = None,
        *,
        sources: "list[NewsSource] | None" = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_headlines: int = DEFAULT_MAX_HEADLINES,
        request_timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._sources = sources
        self.model = model
        self.max_tokens = max_tokens
        self.max_headlines = max_headlines
        self.request_timeout = request_timeout

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def research(self, ticker: str) -> ResearchResult:
        """Produce a fundamental read for ``ticker``.

        Never raises. Any failure (fetch error, API error, malformed
        JSON) is logged and returned as an empty :class:`ResearchResult`
        with ``confidence=0.0`` so the calling scanner stays healthy.
        """
        symbol = ticker.upper()
        now = datetime.now(tz=timezone.utc)

        items = self._fetch_headlines(symbol)
        headlines = _project_headlines(items, now=now, limit=self.max_headlines)
        source_urls = tuple(h.url for h in headlines if h.url)

        try:
            payload = self._call_claude(symbol=symbol, headlines=headlines, today=now)
        except Exception as exc:
            log.warning("LLM research failed for %s: %s", symbol, exc)
            return _empty_result(symbol, as_of=now, sources=source_urls)

        try:
            return self._map_payload(
                payload, ticker=symbol, as_of=now, sources=source_urls
            )
        except Exception as exc:
            log.warning("LLM research parse failed for %s: %s", symbol, exc)
            return _empty_result(symbol, as_of=now, sources=source_urls)

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _fetch_headlines(self, symbol: str) -> "list[NewsItem]":
        sources = self._sources
        if sources is None:
            # Lazy import keeps the research package importable in
            # environments where the narrative deps aren't installed.
            from src.narrative.sources import default_sources

            sources = default_sources()

        items: list = []
        for src in sources:
            try:
                items.extend(src.fetch(symbol, limit=self.max_headlines))
            except Exception as exc:
                log.warning("research news fetch failed on %s for %s: %s", getattr(src, "name", "?"), symbol, exc)
        return items

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        # Lazy import + lazy construction so importing this module
        # doesn't require the anthropic SDK to be installed.
        import anthropic  # noqa: WPS433 - intentional lazy import

        self._client = anthropic.Anthropic()
        return self._client

    def _call_claude(
        self, *, symbol: str, headlines: list[_Headline], today: datetime
    ) -> dict[str, object]:
        client = self._ensure_client()
        user_message = _render_user_message(
            ticker=symbol, headlines=headlines, today=today
        )

        # cache_control on the system block keeps the prefix bytes
        # stable across calls. Verify on the second call of a session
        # with response.usage.cache_read_input_tokens > 0.
        request_kwargs: dict[str, object] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            output_config={
                "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}
            },
        )

        # with_options gives us a per-call timeout without mutating the
        # shared client. Falls back to a direct call if the test double
        # doesn't implement with_options.
        with_options = getattr(client, "with_options", None)
        if callable(with_options):
            scoped = with_options(timeout=self.request_timeout)
        else:
            scoped = client

        response = scoped.messages.create(**request_kwargs)
        return _extract_json_payload(response)

    def _map_payload(
        self,
        payload: dict[str, object],
        *,
        ticker: str,
        as_of: datetime,
        sources: tuple[str, ...],
    ) -> ResearchResult:
        return ResearchResult(
            ticker=ticker,
            as_of=as_of,
            summary=str(payload.get("summary", "")),
            company_quality=str(payload.get("company_quality", "")),
            management=str(payload.get("management", "")),
            partnerships=str(payload.get("partnerships", "")),
            financial_health=str(payload.get("financial_health", "")),
            key_risks=str(payload.get("key_risks", "")),
            sources=sources,
            confidence=_coerce_confidence(payload.get("confidence")),
            raw={"model": self.model},
        )


# --------------------------------------------------------------------- #
# Response parsing                                                      #
# --------------------------------------------------------------------- #


def _extract_json_payload(response: object) -> dict[str, object]:
    """Pull the first text block off the response and parse it as JSON.

    ``output_config.format`` guarantees the response is a single text
    block of valid JSON matching the schema, but we still defensively
    look for the first text-typed block rather than assuming index 0.
    """
    content = getattr(response, "content", None) or []
    text: str | None = None
    for block in content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            break
    if not text:
        raise ValueError("response contained no text block")
    return json.loads(text)


def _coerce_confidence(value: object) -> float:
    """Clamp the model's confidence to ``[0, 1]``."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f
