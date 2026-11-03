"""The only module that talks to Ollama. Enforces context limits and reports token usage."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import closing
from typing import Protocol, TypeVar

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from app.config.settings import Settings

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)
RETRY_TEMPERATURE = 0.3
MAX_CACHE_ENTRIES = 5000


class LLMError(Exception):
    """The model could not produce a usable response."""


class LLMUnavailableError(LLMError):
    """Ollama was unreachable, timed out or returned an error. Safe to retry."""


class LLMInvalidOutputError(LLMError):
    """The response did not match the requested schema. Safe to retry."""


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    cached: bool = False


class LLM(Protocol):
    """What agents depend on, so tests can substitute a fake."""

    def complete(self, system: str, user: str) -> tuple[str, LLMUsage]: ...

    def complete_structured(
        self, system: str, user: str, schema: type[SchemaT]
    ) -> tuple[SchemaT, LLMUsage]: ...


class OllamaLLM:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = ollama.Client(
            host=settings.ollama_base_url, timeout=settings.ollama_timeout_seconds
        )

    def _chat(
        self, system: str, user: str, json_schema: dict | None, temperature: float = 0
    ) -> tuple[str, LLMUsage]:
        try:
            response = self._client.chat(
                model=self._settings.ollama_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                format=json_schema,
                options={
                    "num_ctx": self._settings.ollama_num_ctx,
                    "num_predict": self._settings.ollama_num_predict,
                    "temperature": temperature,
                },
                keep_alive=self._settings.ollama_keep_alive,
            )
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as exc:
            raise LLMUnavailableError(f"Ollama request failed: {exc}") from exc

        usage = LLMUsage(
            prompt_tokens=response.prompt_eval_count or 0,
            output_tokens=response.eval_count or 0,
            duration_seconds=(response.total_duration or 0) / 1e9,
        )
        logger.info(
            "llm_call model=%s prompt_tokens=%d output_tokens=%d seconds=%.2f",
            self._settings.ollama_model,
            usage.prompt_tokens,
            usage.output_tokens,
            usage.duration_seconds,
        )
        return response.message.content or "", usage

    def complete(self, system: str, user: str) -> tuple[str, LLMUsage]:
        return self._chat(system, user, None)

    def complete_structured(
        self, system: str, user: str, schema: type[SchemaT]
    ) -> tuple[SchemaT, LLMUsage]:
        json_schema = schema.model_json_schema()
        text, usage = self._chat(system, user, json_schema)
        try:
            return schema.model_validate_json(text), usage
        except ValidationError:
            # Usually Granite rambling in one string until num_predict cuts the JSON off. Temperature 0
            # would repeat that exactly, so a little randomness is what gets it out of the loop.
            logger.info("llm_output_invalid schema=%s output_tokens=%d", schema.__name__, usage.output_tokens)
        text, usage = self._chat(system, user, json_schema, temperature=RETRY_TEMPERATURE)
        try:
            return schema.model_validate_json(text), usage
        except ValidationError as exc:
            raise LLMInvalidOutputError(
                f"Response did not match {schema.__name__}: {exc.error_count()} errors"
            ) from exc


class CachedLLM:
    """Reuses structured responses for identical prompts, so only changed code costs model time.

    The key covers everything that shapes the response, so changed code misses by itself. Prompts
    are not stored, and failures are not cached.
    """

    def __init__(self, inner: LLM, settings: Settings) -> None:
        self._inner = inner
        self._settings = settings
        self._path = settings.llm_cache_path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=5)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        # Responses can quote project code, so only this user should read them.
        self._path.chmod(0o600)
        return connection

    def _key(self, system: str, user: str, schema: type[BaseModel]) -> str:
        settings = self._settings
        parts = [
            settings.ollama_model,
            settings.ollama_num_ctx,
            settings.ollama_num_predict,
            system,
            user,
            schema.model_json_schema(),
        ]
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

    def _load(self, key: str) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("llm_cache_unavailable error=%s", exc)
            return None
        return row[0] if row else None

    def _store(self, key: str, value: str) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO llm_cache (key, value, created_at) VALUES (?, ?, ?)",
                    (key, value, time.time()),
                )
                connection.execute(
                    "DELETE FROM llm_cache WHERE key NOT IN "
                    "(SELECT key FROM llm_cache ORDER BY created_at DESC LIMIT ?)",
                    (MAX_CACHE_ENTRIES,),
                )
        except (sqlite3.Error, OSError) as exc:
            # A cache problem should cost time, never a result.
            logger.warning("llm_cache_unavailable error=%s", exc)

    def complete(self, system: str, user: str) -> tuple[str, LLMUsage]:
        return self._inner.complete(system, user)

    def complete_structured(
        self, system: str, user: str, schema: type[SchemaT]
    ) -> tuple[SchemaT, LLMUsage]:
        key = self._key(system, user, schema)
        stored = self._load(key)
        if stored is not None:
            try:
                return schema.model_validate_json(stored), LLMUsage(cached=True)
            except ValidationError:
                logger.info("llm_cache_entry_invalid schema=%s", schema.__name__)
        result, usage = self._inner.complete_structured(system, user, schema)
        self._store(key, result.model_dump_json())
        return result, usage


def build_llm(settings: Settings) -> LLM:
    llm = OllamaLLM(settings)
    return CachedLLM(llm, settings) if settings.llm_cache_enabled else llm
