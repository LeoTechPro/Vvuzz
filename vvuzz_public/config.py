from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    environment: str
    base_path: str
    cache_hmac_key: bytes
    ingest_token: str
    session_cookie: str = "vvuzz_manage"
    session_ttl_seconds: int = 8 * 60 * 60
    session_idle_seconds: int = 60 * 60
    lookup_timing_floor_ms: int = 80
    lookup_limit: int = 50
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("VVUZZ_ENV", "development").strip().lower()
        raw_key = os.getenv("VVUZZ_CACHE_HMAC_KEY", "")
        if not raw_key and environment == "production":
            raise RuntimeError("VVUZZ_CACHE_HMAC_KEY is required in production")
        key = raw_key.encode("utf-8") if raw_key else secrets.token_bytes(32)
        base_path = os.getenv("VVUZZ_BASE_PATH", "/beta").rstrip("/")
        if base_path == "/":
            base_path = ""
        return cls(
            db_path=Path(os.getenv("VVUZZ_DB_PATH", ROOT / "vuzonline_2025_norm.sqlite")),
            environment=environment,
            base_path=base_path,
            cache_hmac_key=key,
            ingest_token=os.getenv("VVUZZ_INGEST_TOKEN", ""),
        )
