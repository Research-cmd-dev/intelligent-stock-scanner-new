"""YouTube source subpackage.

Phase 2 ships only the discovery layer (RSS + match rules). Transcript
ingestion (Phase 3) and event emission (Phase 5) join later.

The package is intentionally not registered in :func:`default_sources` —
YouTube items flow through the ``narrative_events`` table (Phase 4), not
through the ``NewsItem`` fetch path.
"""

from __future__ import annotations

from .discovery import (
    ChannelSpec,
    DiscoveryConfig,
    SpeakerSpec,
    VideoCandidate,
    discover_candidates,
    load_config,
    match_entries,
)

__all__ = [
    "ChannelSpec",
    "DiscoveryConfig",
    "SpeakerSpec",
    "VideoCandidate",
    "discover_candidates",
    "load_config",
    "match_entries",
]
