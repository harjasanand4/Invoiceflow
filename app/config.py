"""Application settings, read from environment variables (and a .env file if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'invoiceflow.db'}"
        )
    )

    # Where uploaded PDFs are stored: "local" (a folder) or "s3".
    storage_backend: str = field(default_factory=lambda: os.getenv("STORAGE_BACKEND", "local"))
    storage_dir: str = field(
        default_factory=lambda: os.getenv("STORAGE_DIR", str(BASE_DIR / "instance" / "files"))
    )
    s3_bucket: str | None = field(default_factory=lambda: os.getenv("S3_BUCKET"))
    s3_prefix: str = field(default_factory=lambda: os.getenv("S3_PREFIX", "documents/"))

    # Which extractor to use: "rules" (free, offline), "llm", or "hybrid" (rules first, LLM fallback).
    extractor: str = field(default_factory=lambda: os.getenv("EXTRACTOR", "rules"))
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    )
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    # Any OpenAI-style API, e.g. Ollama running locally: http://localhost:11434/v1
    openai_compatible_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    )
    openai_compatible_model: str = field(default_factory=lambda: os.getenv("OPENAI_COMPATIBLE_MODEL", "qwen2.5:7b"))
    # Minimum seconds between LLM calls. 0 = no limit. Free tiers allow only a few calls per
    # minute: e.g. 6 keeps you under 10 requests/minute. Rate-limit errors are also retried with backoff.
    llm_min_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("LLM_MIN_INTERVAL_SECONDS", "0"))
    )

    # Any field below this confidence sends the document to human review.
    review_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "0.85"))
    )
    # Retries before a document is marked failed.
    max_attempts: int = field(default_factory=lambda: int(os.getenv("MAX_ATTEMPTS", "3")))
    # Process synchronously on upload (handy in dev). In production, leave off and run the worker.
    process_on_upload: bool = field(default_factory=lambda: _bool("PROCESS_ON_UPLOAD", True))
    # Allowed difference when comparing money amounts.
    money_tolerance: float = field(default_factory=lambda: float(os.getenv("MONEY_TOLERANCE", "0.02")))
    # Invoice total may exceed its PO amount by this fraction before it is flagged.
    po_tolerance: float = field(default_factory=lambda: float(os.getenv("PO_TOLERANCE", "0.02")))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "10")))
    # Password-protect the whole app (UI + API) with HTTP basic auth. Set both for any public deploy.
    basic_auth_user: str | None = field(default_factory=lambda: os.getenv("BASIC_AUTH_USER"))
    basic_auth_password: str | None = field(default_factory=lambda: os.getenv("BASIC_AUTH_PASSWORD"))
