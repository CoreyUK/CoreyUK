"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so a plain `python -m app` picks up local settings."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(slots=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 8000

    cache_ttl_seconds: int = 900
    cache_db_path: str = "data/cache.sqlite3"

    retailer_timeout_seconds: float = 12.0
    global_max_concurrency: int = 16
    per_host_max_concurrency: int = 2
    per_host_min_interval_seconds: float = 1.0
    max_results_per_retailer: int = 40

    api_rate_limit_per_minute: int = 30
    force_refresh_min_interval_seconds: int = 60
    max_query_length: int = 80

    enabled_retailers: list[str] = field(default_factory=lambda: ["all"])
    debug_dump_dir: str = ""

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(Path(".env"))
        retailers = [r.strip().lower() for r in _env("ENABLED_RETAILERS", "all").split(",") if r.strip()]
        return cls(
            host=_env("HOST", "0.0.0.0"),
            port=int(_env("PORT", "8000")),
            cache_ttl_seconds=int(_env("CACHE_TTL_SECONDS", "900")),
            cache_db_path=_env("CACHE_DB_PATH", "data/cache.sqlite3"),
            retailer_timeout_seconds=float(_env("RETAILER_TIMEOUT_SECONDS", "12")),
            global_max_concurrency=int(_env("GLOBAL_MAX_CONCURRENCY", "16")),
            per_host_max_concurrency=int(_env("PER_HOST_MAX_CONCURRENCY", "2")),
            per_host_min_interval_seconds=float(_env("PER_HOST_MIN_INTERVAL_SECONDS", "1.0")),
            max_results_per_retailer=int(_env("MAX_RESULTS_PER_RETAILER", "40")),
            api_rate_limit_per_minute=int(_env("API_RATE_LIMIT_PER_MINUTE", "30")),
            force_refresh_min_interval_seconds=int(_env("FORCE_REFRESH_MIN_INTERVAL_SECONDS", "60")),
            max_query_length=int(_env("MAX_QUERY_LENGTH", "80")),
            enabled_retailers=retailers or ["all"],
            debug_dump_dir=_env("DEBUG_DUMP_DIR", ""),
            user_agent=_env("USER_AGENT", cls.user_agent),
        )

    def retailer_enabled(self, retailer_id: str) -> bool:
        return "all" in self.enabled_retailers or retailer_id in self.enabled_retailers
