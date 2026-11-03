"""Graph nodes: thin adapters between AgentState and the agent functions."""

from __future__ import annotations

from typing import Any

from app.agents.code_reviewer import review_code
from app.agents.project_scanner import analyze_project, summarize_project
from app.agents.researcher import research
from app.agents.supervisor import classify_request
from app.config.settings import Settings
from app.graph.state import AgentState, Metrics
from app.llm import LLM
from app.tools.filesystem_tools import resolve_project
from app.tools.search_tools import search_code


class Nodes:
    def __init__(self, llm: LLM, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    def supervisor(self, state: AgentState) -> dict[str, Any]:
        if state.get("task"):
            return {"route_reason": "Task set by the caller; no LLM call needed."}
        route, usage = classify_request(state["request"], self._llm)
        metrics = Metrics()
        metrics.add_usage(usage)
        return {"task": route.task, "route_reason": route.reason, "metrics": metrics}

    def code_review(self, state: AgentState) -> dict[str, Any]:
        project = resolve_project(state["project_path"], self._settings)
        result, metrics = review_code(
            project, state.get("file_path"), state.get("focus"), self._llm, self._settings
        )
        return {"result": result.model_dump(), "metrics": metrics}

    def research(self, state: AgentState) -> dict[str, Any]:
        project = resolve_project(state["project_path"], self._settings)
        result, metrics = research(project, state["request"], self._llm, self._settings)
        return {"result": result.model_dump(), "metrics": metrics}

    def analyze_project(self, state: AgentState) -> dict[str, Any]:
        project = resolve_project(state["project_path"], self._settings)
        result, metrics = analyze_project(project, self._settings)
        return {"result": result.model_dump(), "metrics": metrics}

    def summarize_project(self, state: AgentState) -> dict[str, Any]:
        project = resolve_project(state["project_path"], self._settings)
        result, metrics = summarize_project(project, self._llm, self._settings)
        return {"result": result.model_dump(), "metrics": metrics}

    def search_code(self, state: AgentState) -> dict[str, Any]:
        project = resolve_project(state["project_path"], self._settings)
        result = search_code(
            project, state.get("query", ""), state.get("extensions"), self._settings.max_file_bytes
        )
        return {"result": result.model_dump(), "metrics": Metrics(files_inspected=result.files_matched)}

    def unsupported(self, state: AgentState) -> dict[str, Any]:
        return {
            "result": {
                "task": "unsupported",
                "summary": "No local agent handles this request. Claude Code should handle it directly.",
            }
        }
