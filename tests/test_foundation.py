"""Phase 1: configuration, a direct Granite call, and a minimal LangGraph workflow."""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.config.settings import Settings, get_settings
from app.llm import LLMUnavailableError, OllamaLLM


class Answer(BaseModel):
    capital: str


def test_settings_load_from_env_file() -> None:
    settings = get_settings()
    assert settings.ollama_num_ctx == 8192
    assert settings.allowed_workspaces


def test_input_budget_fits_inside_context() -> None:
    settings = Settings(ollama_num_ctx=8192, ollama_num_predict=1024)
    assert 0 < settings.max_input_chars < 8192 * 4


def test_unreachable_ollama_raises_clear_error() -> None:
    llm = OllamaLLM(Settings(ollama_base_url="http://127.0.0.1:9", ollama_timeout_seconds=2))
    with pytest.raises(LLMUnavailableError):
        llm.complete("system", "hello")


@pytest.mark.integration
def test_direct_granite_call_reports_usage(real_llm: OllamaLLM) -> None:
    text, usage = real_llm.complete("Answer with one word.", "What color is a clear daytime sky?")
    assert "blue" in text.lower()
    assert usage.prompt_tokens > 0 and usage.output_tokens > 0


@pytest.mark.integration
def test_granite_structured_output(real_llm: OllamaLLM) -> None:
    answer, _ = real_llm.complete_structured("Answer factually.", "Capital of France?", Answer)
    assert answer.capital.lower() == "paris"


@pytest.mark.integration
def test_minimal_langgraph_workflow_calls_granite(real_llm: OllamaLLM) -> None:
    class State(TypedDict):
        question: str
        answer: str

    def ask(state: State) -> dict[str, str]:
        text, _ = real_llm.complete("Answer with one word.", state["question"])
        return {"answer": text}

    builder = StateGraph(State)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)

    result = builder.compile().invoke({"question": "What color is a clear daytime sky?"})
    assert "blue" in result["answer"].lower()
