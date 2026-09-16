from functools import lru_cache
from typing import Optional

import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

DEFAULT_RESEARCH_DB_PATH = "runtime/db/research.db"
LEGACY_RESEARCH_DB_PATH = "research.db"


def _env_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val is not None else default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_research_db_path() -> str:
    explicit = os.getenv("RESEARCH_DB_PATH")
    if explicit is not None and explicit.strip():
        return explicit.strip()

    runtime_path = DEFAULT_RESEARCH_DB_PATH
    legacy_path = LEGACY_RESEARCH_DB_PATH

    if os.path.exists(runtime_path):
        return runtime_path
    if os.path.exists(legacy_path):
        try:
            parent = os.path.dirname(runtime_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.replace(legacy_path, runtime_path)
            return runtime_path
        except OSError:
            return legacy_path
    return runtime_path


class Settings(BaseModel):
    """Application settings loaded from environment or .env file."""

    gamma_base_url: str = Field("https://gamma-api.polymarket.com")
    clob_base_url: str = Field("https://clob.polymarket.com")

    db_path: str = Field(DEFAULT_RESEARCH_DB_PATH)

    rate_limit_per_sec: float = Field(2.0)
    max_concurrency: int = Field(5)
    cache_ttl_seconds: int = Field(300)
    orderbook_top_n: int = Field(20)
    batch_size_token_ids: int = Field(50)

    w_rule: float = Field(0.6)
    w_friction: float = Field(0.4)
    spread_pct_cap: float = Field(0.10)
    vol_score_threshold: float = Field(50000.0)
    depth_score_threshold: float = Field(20000.0)

    llm_base_url: Optional[str] = Field(None)
    llm_api_key: Optional[str] = Field(None)
    llm_model: Optional[str] = Field(None)
    llm_provider: Optional[str] = Field(None)
    iflow_base_url: Optional[str] = Field(None)
    iflow_api_key: Optional[str] = Field(None)
    iflow_model: Optional[str] = Field(None)
    prompt_version: str = Field("v1")

    archive_books: bool = Field(False)
    archive_dir: str = Field("archive/books")

    telegram_bot_token: Optional[str] = Field(None)
    telegram_chat_id: Optional[str] = Field(None)
    telegram_api_base_url: str = Field("https://api.telegram.org")

    @field_validator("rate_limit_per_sec")
    @classmethod
    def _positive_rate(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("rate_limit_per_sec must be > 0")
        return v

    @field_validator("max_concurrency")
    @classmethod
    def _positive_concurrency(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_concurrency must be > 0")
        return v

    @field_validator("orderbook_top_n")
    @classmethod
    def _positive_topn(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("orderbook_top_n must be > 0")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once, supporting .env files."""
    load_dotenv()
    data = {
        "gamma_base_url": _env_str("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
        "clob_base_url": _env_str("CLOB_BASE_URL", "https://clob.polymarket.com"),
        "db_path": _resolve_research_db_path(),
        "rate_limit_per_sec": _env_float("RESEARCH_RATE_LIMIT_PER_SEC", 2.0),
        "max_concurrency": _env_int("RESEARCH_MAX_CONCURRENCY", 5),
        "cache_ttl_seconds": _env_int("RESEARCH_CACHE_TTL_SECONDS", 300),
        "orderbook_top_n": _env_int("RESEARCH_ORDERBOOK_TOP_N", 20),
        "batch_size_token_ids": _env_int("RESEARCH_BATCH_SIZE_TOKEN_IDS", 50),
        "w_rule": _env_float("RESEARCH_W_RULE", 0.6),
        "w_friction": _env_float("RESEARCH_W_FRICTION", 0.4),
        "spread_pct_cap": _env_float("RESEARCH_SPREAD_PCT_CAP", 0.10),
        "vol_score_threshold": _env_float("RESEARCH_VOL_SCORE_THRESHOLD", 50000.0),
        "depth_score_threshold": _env_float("RESEARCH_DEPTH_SCORE_THRESHOLD", 20000.0),
        "llm_base_url": os.getenv("LLM_BASE_URL"),
        "llm_api_key": os.getenv("LLM_API_KEY"),
        "llm_model": os.getenv("LLM_MODEL"),
        "llm_provider": os.getenv("LLM_PROVIDER"),
        "iflow_base_url": os.getenv("IFLOW_BASE_URL"),
        "iflow_api_key": os.getenv("IFLOW_API_KEY"),
        "iflow_model": os.getenv("IFLOW_MODEL"),
        "prompt_version": _env_str("RESEARCH_PROMPT_VERSION", "v1"),
        "archive_books": _env_bool("RESEARCH_ARCHIVE_BOOKS", False),
        "archive_dir": _env_str("RESEARCH_ARCHIVE_DIR", "archive/books"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "telegram_api_base_url": _env_str("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
    }
    return Settings(**data)
