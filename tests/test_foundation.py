"""Phase 1: configuration, a direct Granite call, and a minimal LangGraph workflow."""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.config.settings import Settings, get_settings
from app.llm import RETRY_TEMPERATURE, LLMInvalidOutputError, LLMUnavailableError, LLMUsage, OllamaLLM


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


def scripted_llm(monkeypatch: pytest.MonkeyPatch, *outputs: str) -> tuple[OllamaLLM, list[float]]:
    llm = OllamaLLM(Settings())
    temperatures: list[float] = []
    remaining = list(outputs)

    def fake_chat(system: str, user: str, schema: dict | None, temperature: float = 0) -> tuple[str, LLMUsage]:
        temperatures.append(temperature)
        return remaining.pop(0), LLMUsage(prompt_tokens=1, output_tokens=1)

    monkeypatch.setattr(llm, "_chat", fake_chat)
    return llm, temperatures


def test_unusable_output_is_retried_once_with_a_different_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, temperatures = scripted_llm(monkeypatch, '{"capital": "Par', '{"capital": "Paris"}')
    answer, _ = llm.complete_structured("s", "u", Answer)
    assert answer.capital == "Paris"
    assert temperatures == [0, RETRY_TEMPERATURE]


def test_good_output_makes_only_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, temperatures = scripted_llm(monkeypatch, '{"capital": "Paris"}')
    llm.complete_structured("s", "u", Answer)
    assert temperatures == [0]


def test_output_that_stays_unusable_raises_after_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, temperatures = scripted_llm(monkeypatch, "bad", "still bad")
    with pytest.raises(LLMInvalidOutputError):
        llm.complete_structured("s", "u", Answer)
    assert len(temperatures) == 2


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
