"""The model response cache: reuse, invalidation, and failing safely."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agents.code_reviewer import review_code
from app.config.settings import Settings
from app.graph.state import Metrics
from app.llm import CachedLLM, LLMInvalidOutputError, LLMUsage, OllamaLLM, build_llm
from tests.fakes import FakeLLM


class Answer(BaseModel):
    value: str


class OtherAnswer(BaseModel):
    number: int


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(llm_cache_path=tmp_path / "cache" / "llm.sqlite", allowed_workspaces=(tmp_path.resolve(),))


def test_identical_prompt_is_served_from_the_cache(settings: Settings) -> None:
    inner = FakeLLM({"value": "first"})
    llm = CachedLLM(inner, settings)
    llm.complete_structured("s", "u", Answer)
    answer, usage = llm.complete_structured("s", "u", Answer)
    assert answer.value == "first"
    assert usage.cached is True and usage.prompt_tokens == 0
    assert len(inner.calls) == 1


def test_cache_survives_a_restart(settings: Settings) -> None:
    CachedLLM(FakeLLM({"value": "first"}), settings).complete_structured("s", "u", Answer)
    inner = FakeLLM()
    answer, _ = CachedLLM(inner, settings).complete_structured("s", "u", Answer)
    assert answer.value == "first" and inner.calls == []


@pytest.mark.parametrize(
    "system,user", [("other", "u"), ("s", "other")]
)
def test_any_prompt_change_misses(settings: Settings, system: str, user: str) -> None:
    inner = FakeLLM({"value": "a"}, {"value": "b"})
    llm = CachedLLM(inner, settings)
    llm.complete_structured("s", "u", Answer)
    answer, usage = llm.complete_structured(system, user, Answer)
    assert answer.value == "b" and usage.cached is False


def test_a_different_schema_misses(settings: Settings) -> None:
    inner = FakeLLM({"value": "a"}, {"number": 1})
    llm = CachedLLM(inner, settings)
    llm.complete_structured("s", "u", Answer)
    assert llm.complete_structured("s", "u", OtherAnswer)[0].number == 1


@pytest.mark.parametrize("change", [{"ollama_model": "other:1b"}, {"ollama_num_ctx": 4096}, {"ollama_num_predict": 512}])
def test_model_settings_are_part_of_the_key(settings: Settings, change: dict) -> None:
    CachedLLM(FakeLLM({"value": "a"}), settings).complete_structured("s", "u", Answer)
    inner = FakeLLM({"value": "b"})
    answer, _ = CachedLLM(inner, settings.model_copy(update=change)).complete_structured("s", "u", Answer)
    assert answer.value == "b"


def test_failures_are_not_cached(settings: Settings) -> None:
    inner = FakeLLM(LLMInvalidOutputError("bad"), {"value": "good"})
    llm = CachedLLM(inner, settings)
    with pytest.raises(LLMInvalidOutputError):
        llm.complete_structured("s", "u", Answer)
    assert llm.complete_structured("s", "u", Answer)[0].value == "good"


def test_cache_file_is_private_and_stores_no_prompts(settings: Settings) -> None:
    CachedLLM(FakeLLM({"value": "a"}), settings).complete_structured("s", "distinctive-prompt-text", Answer)
    path = settings.llm_cache_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"distinctive-prompt-text" not in path.read_bytes()


def test_a_corrupt_cache_costs_time_but_not_the_result(settings: Settings) -> None:
    settings.llm_cache_path.parent.mkdir(parents=True)
    settings.llm_cache_path.write_bytes(b"this is not a database")
    answer, usage = CachedLLM(FakeLLM({"value": "a"}), settings).complete_structured("s", "u", Answer)
    assert answer.value == "a" and usage.cached is False


def test_cache_hits_are_counted_apart_from_model_calls() -> None:
    metrics = Metrics()
    metrics.add_usage(LLMUsage(cached=True))
    metrics.add_usage(LLMUsage(prompt_tokens=10, output_tokens=2))
    assert (metrics.cache_hits, metrics.llm_calls, metrics.prompt_tokens) == (1, 1, 10)


def test_only_the_changed_function_goes_back_to_the_model(tmp_path: Path, settings: Settings) -> None:
    root = tmp_path.resolve()
    (root / "m.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    inner = FakeLLM({"findings": []}, {"findings": []}, {"findings": []})
    llm = CachedLLM(inner, settings)
    _, first = review_code(root, "m.py", None, llm, settings)
    assert (first.llm_calls, first.cache_hits) == (2, 0)
    (root / "m.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 3\n")
    _, second = review_code(root, "m.py", None, llm, settings)
    assert (second.llm_calls, second.cache_hits) == (1, 1)


def test_build_llm_respects_the_setting(settings: Settings) -> None:
    assert isinstance(build_llm(settings), CachedLLM)
    assert isinstance(build_llm(settings.model_copy(update={"llm_cache_enabled": False})), OllamaLLM)
