"""State shared across the graph, and the metrics each run reports."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel

from app.llm import LLMUsage

TaskType = Literal[
    "code_review", "research", "analyze_project", "search_code", "summarize_project", "unsupported"
]


class Metrics(BaseModel):
    files_inspected: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    cache_hits: int = 0

    def add_usage(self, usage: LLMUsage) -> None:
        self.llm_calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.output_tokens += usage.output_tokens


def merge_metrics(left: Metrics, right: Metrics) -> Metrics:
    return Metrics(
        files_inspected=left.files_inspected + right.files_inspected,
        llm_calls=left.llm_calls + right.llm_calls,
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_hits=left.cache_hits + right.cache_hits,
    )


class AgentState(TypedDict, total=False):
    # Inputs. An MCP tool sets `task` directly, which skips LLM classification.
    request: str
    task: TaskType
    project_path: str
    file_path: str | None
    focus: str | None
    query: str
    extensions: list[str] | None
    # Outputs.
    route_reason: str
    result: dict[str, Any]
    metrics: Annotated[Metrics, merge_metrics]
