from __future__ import annotations

import pytest

from app.config.settings import get_settings
from app.llm import OllamaLLM


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs a running Ollama with Granite")


@pytest.fixture(scope="session")
def real_llm() -> OllamaLLM:
    return OllamaLLM(get_settings())
