"""Builds the LangGraph workflow and runs tasks, always returning a structured result."""

from __future__ import annotations

import logging
import time
from typing import Any, get_args

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from app.agents.code_analyzer import AnalysisError
from app.config.settings import Settings
from app.graph.nodes import Nodes
from app.graph.routing import route_from_supervisor
from app.graph.state import AgentState, Metrics, TaskType
from app.llm import LLM, LLMError, LLMUnavailableError
from app.tools.filesystem_tools import PathAccessError
from app.tools.search_tools import SearchError
from app.tools.testing_tools import TestRunError

logger = logging.getLogger(__name__)

# Only connection failures are retried here. app/llm.py handles unusable output, which needs a different call.
OLLAMA_RETRY = RetryPolicy(max_attempts=3, initial_interval=1.0, retry_on=LLMUnavailableError)
SPECIALISTS = get_args(TaskType)


def build_workflow(llm: LLM, settings: Settings) -> CompiledStateGraph:
    nodes = Nodes(llm, settings)
    builder = StateGraph(AgentState)
    builder.add_node("supervisor", nodes.supervisor, retry_policy=OLLAMA_RETRY)
    builder.add_node("code_review", nodes.code_review, retry_policy=OLLAMA_RETRY)
    builder.add_node("research", nodes.research, retry_policy=OLLAMA_RETRY)
    builder.add_node("summarize_project", nodes.summarize_project, retry_policy=OLLAMA_RETRY)
    builder.add_node("explain_code", nodes.explain_code, retry_policy=OLLAMA_RETRY)
    builder.add_node("debug_error", nodes.debug_error, retry_policy=OLLAMA_RETRY)
    builder.add_node("analyze_project", nodes.analyze_project)
    builder.add_node("search_code", nodes.search_code)
    # Not retried: rerunning a project's tests is not something to do automatically.
    builder.add_node("run_tests", nodes.run_tests)
    builder.add_node("unsupported", nodes.unsupported)
    builder.add_node("summarizer", nodes.summarizer)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_from_supervisor, {task: task for task in SPECIALISTS})
    for node in SPECIALISTS:
        builder.add_edge(node, "summarizer")
    builder.add_edge("summarizer", END)
    return builder.compile()


def run_task(
    llm: LLM,
    settings: Settings,
    *,
    project_path: str,
    request: str = "",
    task: TaskType | None = None,
    file_path: str | None = None,
    focus: str | None = None,
    query: str = "",
    extensions: list[str] | None = None,
    symbol: str | None = None,
    error_message: str = "",
    test_path: str | None = None,
    discover_only: bool = False,
) -> dict[str, Any]:
    """Run one task. Expected failures come back as data so a caller never sees a traceback."""
    started = time.perf_counter()
    state: AgentState = {
        "project_path": project_path,
        "request": request,
        "file_path": file_path,
        "focus": focus,
        "query": query,
        "extensions": extensions,
        "symbol": symbol,
        "error_message": error_message,
        "test_path": test_path,
        "discover_only": discover_only,
    }
    if task:
        state["task"] = task
    try:
        final = build_workflow(llm, settings).invoke(state)
    except (PathAccessError, LLMError, SearchError, AnalysisError, TestRunError) as exc:
        logger.warning("task_failed task=%s error=%s", task, exc)
        return {
            "status": "error",
            "error": str(exc),
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    metrics: Metrics = final.get("metrics") or Metrics()
    duration = round(time.perf_counter() - started, 2)
    logger.info("task_done task=%s seconds=%.2f %s", final.get("task"), duration, metrics)
    return {
        "status": "ok",
        "result": final["result"],
        "metrics": {**metrics.model_dump(), "duration_seconds": duration},
    }
