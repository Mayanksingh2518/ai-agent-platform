"""Routing, error handling and retries in the full LangGraph workflow."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings
from app.graph.workflow import run_task
from app.llm import LLMInvalidOutputError, LLMUnavailableError, OllamaLLM
from tests.fakes import FakeLLM

ROUTE_RESEARCH = {"task": "research", "reason": "A question."}
ROUTE_REVIEW = {"task": "code_review", "reason": "A review."}
ROUTE_NONE = {"task": "unsupported", "reason": "Out of scope."}
NO_FINDINGS = {"findings": []}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    (root / "calc.py").write_text("x = 1\n")
    (root / "README.md").write_text("The cache expires after ten minutes.\n")
    return root


@pytest.fixture
def settings(project: Path) -> Settings:
    return Settings(allowed_workspaces=(project,))


def test_explicit_task_skips_classification(project: Path, settings: Settings) -> None:
    llm = FakeLLM(NO_FINDINGS)
    output = run_task(llm, settings, project_path=str(project), task="code_review")
    assert output["status"] == "ok"
    assert output["result"]["task"] == "code_review"
    assert output["metrics"]["llm_calls"] == 1


def test_supervisor_routes_to_code_review(project: Path, settings: Settings) -> None:
    llm = FakeLLM(ROUTE_REVIEW, NO_FINDINGS)
    output = run_task(llm, settings, project_path=str(project), request="Review my code")
    assert output["result"]["task"] == "code_review"
    assert output["metrics"]["llm_calls"] == 2


def test_supervisor_routes_to_research(project: Path, settings: Settings) -> None:
    answer = {"answer": "Ten minutes", "source_ids": [1], "uncertainty": "", "confidence": "high"}
    llm = FakeLLM(ROUTE_RESEARCH, answer)
    output = run_task(llm, settings, project_path=str(project), request="When does the cache expire?")
    assert output["result"]["task"] == "research"
    assert output["result"]["sources"][0]["file"] == "README.md"


def test_unsupported_request_is_handed_back(project: Path, settings: Settings) -> None:
    llm = FakeLLM(ROUTE_NONE)
    output = run_task(llm, settings, project_path=str(project), request="Deploy to production")
    assert output["result"]["task"] == "unsupported"
    assert output["metrics"]["llm_calls"] == 1


def test_blocked_project_returns_an_error_result(tmp_path: Path, project: Path) -> None:
    llm = FakeLLM()
    output = run_task(
        llm,
        Settings(allowed_workspaces=(project / "elsewhere",)),
        project_path=str(project),
        task="code_review",
    )
    assert output["status"] == "error"
    assert "ALLOWED_WORKSPACES" in output["error"]


def test_connection_failure_is_retried_then_succeeds(project: Path, settings: Settings) -> None:
    llm = FakeLLM(LLMUnavailableError("down"), ROUTE_NONE)
    output = run_task(llm, settings, project_path=str(project), request="Deploy")
    assert output["status"] == "ok"
    assert len(llm.calls) == 2


def test_persistent_connection_failure_returns_an_error_result(project: Path, settings: Settings) -> None:
    llm = FakeLLM(*[LLMUnavailableError("down")] * 3)
    output = run_task(llm, settings, project_path=str(project), request="Deploy")
    assert output["status"] == "error"
    assert len(llm.calls) == 3


def test_invalid_output_is_not_retried(project: Path, settings: Settings) -> None:
    llm = FakeLLM(LLMInvalidOutputError("bad"))
    output = run_task(llm, settings, project_path=str(project), request="Deploy")
    assert output["status"] == "error"
    assert len(llm.calls) == 1


ROUTING_CASES = [
    ("Review auth.py for security holes", "code_review"),
    ("Find bugs in the retrieval module", "code_review"),
    ("Check this file for performance problems", "code_review"),
    ("Are there any code smells in utils.py?", "code_review"),
    ("Look for missing exception handling in the API layer", "code_review"),
    ("How do I configure the database connection?", "research"),
    ("What does the README say about installation?", "research"),
    ("Explain what the retry setting in the docs does", "research"),
    ("Which environment variables does this project document?", "research"),
    ("Summarize the project's deployment documentation", "research"),
    ("Build me a REST API for todos", "unsupported"),
    ("Deploy this to production", "unsupported"),
    ("Delete all the old migration files", "unsupported"),
    ("Rewrite the auth module in Rust", "unsupported"),
    ("Install numpy into my project", "unsupported"),
]


@pytest.mark.integration
def test_granite_routing_accuracy(project: Path, settings: Settings, real_llm: OllamaLLM) -> None:
    from app.agents.supervisor import classify_request

    wrong = []
    for request, expected in ROUTING_CASES:
        route, _ = classify_request(request, real_llm)
        if route.task != expected:
            wrong.append((request, expected, route.task))
    correct = len(ROUTING_CASES) - len(wrong)
    print(f"\nrouting accuracy: {correct}/{len(ROUTING_CASES)}; wrong: {wrong}")
    assert correct >= 13, wrong
