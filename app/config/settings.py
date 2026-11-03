"""Configuration loaded from the environment and the project's .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Rough characters per token, kept low so code never overflows the context.
CHARS_PER_TOKEN = 3
# Tokens held back for system prompts and instructions.
PROMPT_OVERHEAD_TOKENS = 600


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "granite4.1:3b"
    ollama_embed_model: str = "nomic-embed-text:latest"
    ollama_num_ctx: int = Field(default=8192, ge=1024)
    ollama_num_predict: int = Field(default=1024, ge=64)
    ollama_timeout_seconds: float = Field(default=120, gt=0)
    ollama_keep_alive: str = "2m"
    allowed_workspaces: tuple[Path, ...] = ()
    # Running tests executes project code, so it needs a separate, stricter opt-in.
    allowed_test_workspaces: tuple[Path, ...] = ()
    test_timeout_seconds: float = Field(default=120, gt=0)
    max_file_bytes: int = Field(default=200_000, gt=0)
    max_review_files: int = Field(default=10, gt=0)
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "logs"

    @property
    def max_input_chars(self) -> int:
        """Characters of source or docs that fit in one LLM call."""
        tokens = self.ollama_num_ctx - self.ollama_num_predict - PROMPT_OVERHEAD_TOKENS
        return max(tokens, 256) * CHARS_PER_TOKEN


def _parse_workspaces(raw: str) -> tuple[Path, ...]:
    paths = (Path(part.strip()).expanduser() for part in raw.split(",") if part.strip())
    return tuple(path.resolve() for path in paths)


@lru_cache
def get_settings() -> Settings:
    # An explicit path, because Claude Code may start the server from any directory.
    load_dotenv(PROJECT_ROOT / ".env")
    # LangGraph installs LangSmith, which would upload traces if enabled. Everything stays local.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    env_fields = {
        "ollama_base_url": "OLLAMA_BASE_URL",
        "ollama_model": "OLLAMA_MODEL",
        "ollama_embed_model": "OLLAMA_EMBED_MODEL",
        "ollama_num_ctx": "OLLAMA_NUM_CTX",
        "ollama_num_predict": "OLLAMA_NUM_PREDICT",
        "ollama_timeout_seconds": "OLLAMA_TIMEOUT_SECONDS",
        "ollama_keep_alive": "OLLAMA_KEEP_ALIVE",
        "max_file_bytes": "MAX_FILE_BYTES",
        "max_review_files": "MAX_REVIEW_FILES",
        "log_level": "LOG_LEVEL",
        "test_timeout_seconds": "TEST_TIMEOUT_SECONDS",
    }
    values = {field: os.environ[env] for field, env in env_fields.items() if env in os.environ}
    return Settings(
        allowed_workspaces=_parse_workspaces(os.environ.get("ALLOWED_WORKSPACES", "")),
        allowed_test_workspaces=_parse_workspaces(os.environ.get("ALLOWED_TEST_WORKSPACES", "")),
        **values,
    )
