"""Environment-backed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    polygon_api_key: str | None
    cache_dir: Path
    log_dir: Path
    log_level: str
    repo_root: Path = field(default=_REPO_ROOT)

    @property
    def has_polygon(self) -> bool:
        return bool(self.polygon_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(_REPO_ROOT / ".env", override=False)

    cache_dir = Path(os.getenv("CACHE_DIR", _REPO_ROOT / "data" / "cache"))
    log_dir = Path(os.getenv("LOG_DIR", _REPO_ROOT / "logs"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        polygon_api_key=os.getenv("POLYGON_API_KEY") or None,
        cache_dir=cache_dir,
        log_dir=log_dir,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
