"""The only module that talks to Ollama. Enforces context limits and reports token usage."""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

import httpx
import ollama
from pydantic import BaseModel, ValidationError

from app.config.settings import Settings

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


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

    def _chat(self, system: str, user: str, json_schema: dict | None) -> tuple[str, LLMUsage]:
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
                    "temperature": 0,
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
        text, usage = self._chat(system, user, schema.model_json_schema())
        try:
            return schema.model_validate_json(text), usage
        except ValidationError as exc:
            # Usually a response cut off at num_predict.
            raise LLMInvalidOutputError(
                f"Response did not match {schema.__name__}: {exc.error_count()} errors"
            ) from exc
