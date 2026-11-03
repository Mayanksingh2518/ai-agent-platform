"""A scripted LLM for tests that should not depend on Granite."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.llm import LLMUsage

SchemaT = TypeVar("SchemaT", bound=BaseModel)
USAGE = LLMUsage(prompt_tokens=100, output_tokens=20, duration_seconds=0.1)


class FakeLLM:
    """Returns scripted responses in order. An Exception in the script is raised instead."""

    def __init__(self, *responses: dict | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> tuple[str, LLMUsage]:
        raise NotImplementedError

    def complete_structured(
        self, system: str, user: str, schema: type[SchemaT]
    ) -> tuple[SchemaT, LLMUsage]:
        self.calls.append((system, user))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return schema.model_validate(response), USAGE
