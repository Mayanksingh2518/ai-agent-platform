"""Phase 3: project scanning, import analysis, code search and summarization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.project_scanner import analyze_project, summarize_project
from app.config.settings import Settings
from app.graph.workflow import run_task
from app.llm import LLMInvalidOutputError, OllamaLLM
from app.tools.ast_tools import imported_modules, outline_python, redact_line
from app.tools.project_tools import parse_package_json, parse_pyproject, parse_requirements, scan_project
from app.tools.search_tools import SearchError, search_code
from tests.fakes import FakeLLM


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    (root / "api").mkdir()
    (root / "api" / "__init__.py").write_text("")
    (root / "api" / "models.py").write_text("class User:\n    pass\n")
    (root / "api" / "views.py").write_text("from .models import User\n\n\ndef index():\n    return User()\n")
    (root / "run.py").write_text("from api.views import index\n\nif __name__ == '__main__':\n    index()\n")
    (root / "bad.py").write_text("def broken(:\n")
    (root / "README.md").write_text("# Demo\n\nA demo API service.\n")
    (root / "requirements.txt").write_text("# comment\nFastAPI==0.1\npytest>=8\n-r other.txt\n")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text('{"dependencies": {"react": "19"}}')
    (root / "frontend" / "app.tsx").write_text("export const App = () => null;\n")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "hidden.py").write_text("needle = 1\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("needle\n")
    (root / ".env").write_text("needle=supersecret\n")
    (root / "server.pem").write_text("needle\n")
    return root


@pytest.fixture
def settings(project: Path) -> Settings:
    return Settings(allowed_workspaces=(project,))


def test_scan_reports_languages_frameworks_and_dependencies(project: Path, settings: Settings) -> None:
    facts = scan_project(project, settings)
    assert facts.languages["Python"] == 5
    assert facts.languages["TypeScript"] == 1
    assert facts.frameworks == ["FastAPI", "React", "pytest"]
    assert {"fastapi", "pytest", "react"} <= set(facts.dependencies)
    assert "README.md" in facts.important_files
    assert "frontend/package.json" in facts.important_files


def test_scan_ignores_excluded_and_secret_files(project: Path, settings: Settings) -> None:
    facts = scan_project(project, settings)
    assert ".venv" not in facts.top_directories
    assert "node_modules" not in facts.top_directories
    assert facts.total_files == 9


def test_scan_analyzes_python_structure_and_survives_bad_files(project: Path, settings: Settings) -> None:
    facts = scan_project(project, settings)
    assert facts.python_files_unparsed == 1
    assert facts.classes == 1
    assert facts.entry_points == ["run.py"]
    assert facts.most_imported_modules == {"api.models": 1, "api.views": 1}


def test_stdlib_import_is_not_credited_to_a_local_file_with_the_same_name(tmp_path: Path) -> None:
    root = (tmp_path / "pkg").resolve()
    root.mkdir()
    (root / "typing.py").write_text("x = 1\n")
    (root / "core.py").write_text("y = 1\n")
    (root / "a.py").write_text("import typing\nfrom pkg.core import y\n")
    (root / "b.py").write_text("from pkg.typing import x\n")
    facts = scan_project(root, Settings(allowed_workspaces=(root,)))
    assert facts.most_imported_modules == {"core": 1, "typing": 1}


def test_src_layout_imports_resolve(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "src" / "shop").mkdir(parents=True)
    (root / "src" / "shop" / "__init__.py").write_text("")
    (root / "src" / "shop" / "cart.py").write_text("x = 1\n")
    (root / "src" / "shop" / "api.py").write_text("from shop.cart import x\n")
    facts = scan_project(root, Settings(allowed_workspaces=(root,)))
    assert facts.most_imported_modules["shop.cart"] == 1


def test_malformed_manifest_does_not_stop_the_scan(project: Path, settings: Settings) -> None:
    (project / "pyproject.toml").write_text("this is not [ toml")
    (project / "frontend" / "package.json").write_text("{not json")
    assert "fastapi" in scan_project(project, settings).dependencies


def test_scan_never_executes_project_code(project: Path, settings: Settings) -> None:
    marker = project / "executed.txt"
    (project / "evil.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    scan_project(project, settings)
    assert not marker.exists()


def test_dependency_parsers() -> None:
    assert parse_requirements("Django==5\n# no\n-e .\nrequests[socks]>=2\n") == {"django", "requests"}
    assert parse_pyproject('[project]\ndependencies = ["flask>=3"]\n[tool.poetry.dependencies]\npython = "^3"\nCelery = "*"\n') == {"flask", "celery"}
    assert parse_package_json('{"devDependencies": {"Vue": "3"}}') == {"vue"}


def test_outline_and_imports() -> None:
    source = "import os\nfrom .a import b\n\nclass C:\n    pass\n\ndef f():\n    pass\n\nif __name__ == '__main__':\n    f()\n"
    outline = outline_python(source)
    assert outline.classes == ["C"] and outline.functions == 1 and outline.is_entry_point
    assert imported_modules(source, "pkg.mod", False) == {"os", "pkg.a", "pkg.a.b"}


def test_relative_import_from_a_package_resolves_inside_it() -> None:
    assert "pkg.sub.a" in imported_modules("from .a import x\n", "pkg.sub", True)
    assert "pkg.a" in imported_modules("from ..a import x\n", "pkg.sub", True)


def test_search_returns_lines_and_skips_excluded_and_secret_files(project: Path) -> None:
    (project / "api" / "views.py").write_text("x = 1\nneedle = 2\n")
    result = search_code(project, "needle", None, 200_000)
    assert [(m.file, m.line) for m in result.matches] == [("api/views.py", 2)]


def test_search_filters_by_extension(project: Path) -> None:
    (project / "notes.md").write_text("needle\n")
    (project / "code.py").write_text("needle\n")
    result = search_code(project, "needle", [".md"], 200_000)
    assert [m.file for m in result.matches] == ["notes.md"]


def test_search_treats_query_as_literal_text_not_a_regex_or_option(project: Path) -> None:
    (project / "code.py").write_text("a.*b\naxxb\n--files\n")
    assert [m.line for m in search_code(project, "a.*b", None, 200_000).matches] == [1]
    assert [m.line for m in search_code(project, "--files", None, 200_000).matches] == [3]


def test_search_caps_output_and_says_so(project: Path) -> None:
    for i in range(8):
        (project / f"f{i}.py").write_text("needle\n" * 10)
    result = search_code(project, "needle", None, 200_000)
    assert len(result.matches) == 50
    assert result.truncated
    assert result.files_matched == 8


def test_search_redacts_secrets_in_matching_lines(project: Path) -> None:
    (project / "conf.py").write_text('API_KEY = "abc123def456"\nother = "ghp_abcdefghijklmnopqrstuvwxyz"\n')
    assert "abc123def456" not in search_code(project, "API_KEY", None, 200_000).model_dump_json()
    assert "ghp_abc" not in search_code(project, "other", None, 200_000).model_dump_json()


def test_search_with_no_matches_is_not_an_error(project: Path) -> None:
    result = search_code(project, "definitely-absent-text", None, 200_000)
    assert result.matches == [] and not result.truncated


@pytest.mark.parametrize("bad", ["py;rm -rf", "../py", "*"])
def test_search_rejects_unsafe_extensions(project: Path, bad: str) -> None:
    with pytest.raises(SearchError, match="extension"):
        search_code(project, "needle", [bad], 200_000)


def test_search_rejects_an_empty_query(project: Path) -> None:
    with pytest.raises(SearchError, match="empty"):
        search_code(project, "  ", None, 200_000)


def test_redact_line_leaves_ordinary_code_alone() -> None:
    assert redact_line('token = get_token("name")') == 'token = get_token("name")'
    assert redact_line('name = "abcdefghijkl"') == 'name = "abcdefghijkl"'
    assert "<redacted>" in redact_line('"password": "hunter2hunter2",')


def test_analyze_uses_no_llm_calls_through_the_workflow(project: Path, settings: Settings) -> None:
    output = run_task(FakeLLM(), settings, project_path=str(project), task="analyze_project")
    assert output["status"] == "ok"
    assert output["metrics"]["llm_calls"] == 0
    assert "FastAPI" in output["result"]["summary"]
    json.dumps(output)


def test_search_through_the_workflow_and_its_errors(project: Path, settings: Settings) -> None:
    output = run_task(FakeLLM(), settings, project_path=str(project), task="search_code", query="README-absent")
    assert output["status"] == "ok" and output["metrics"]["llm_calls"] == 0
    failed = run_task(FakeLLM(), settings, project_path=str(project), task="search_code", query="")
    assert failed["status"] == "error"


def test_summary_keeps_the_facts_when_the_model_fails(project: Path, settings: Settings) -> None:
    result, _ = summarize_project(project, FakeLLM(LLMInvalidOutputError("bad")), settings)
    assert result.architecture_source == "unavailable"
    assert "FastAPI" in result.facts.frameworks


def test_analyze_project_counts_files(project: Path, settings: Settings) -> None:
    result, metrics = analyze_project(project, settings)
    assert metrics.files_inspected == result.facts.total_files


@pytest.mark.integration
def test_granite_summary_uses_the_project_facts(project: Path, settings: Settings, real_llm: OllamaLLM) -> None:
    result, metrics = summarize_project(project, real_llm, settings)
    assert result.architecture_source == "model" and metrics.llm_calls == 1
    assert result.architecture.strip()
