"""Testing Agent: discovers and runs tests. Uses no LLM, because results need no interpretation."""

from __future__ import annotations

from pathlib import Path

from app.config.settings import Settings
from app.graph.state import Metrics
from app.tools.testing_tools import TestDiscovery, TestRunResult, discover_tests, run_pytest


def run_tests(
    project: Path, test_path: str | None, discover_only: bool, settings: Settings
) -> tuple[TestRunResult | TestDiscovery, Metrics]:
    if discover_only:
        return discover_tests(project, settings), Metrics()
    return run_pytest(project, test_path, settings), Metrics()
