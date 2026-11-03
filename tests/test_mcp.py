"""MCP server: tools in memory with a fake LLM, then the real server as a STDIO subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

from app.config.settings import PROJECT_ROOT, Settings
from app.mcp.server import build_server
from tests.fakes import FakeLLM

EXPECTED_TOOLS = {
    "analyze_project",
    "search_code",
    "review_code",
    "summarize_project",
    "explain_code",
    "debug_error",
    "run_tests",
}
BUGGY = "def add_item(item, items=[]):\n    items.append(item)\n    return items\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    (root / "calc.py").write_text(BUGGY)
    (root / "README.md").write_text("# Demo\n")
    (root / "requirements.txt").write_text("fastapi==0.1\n")
    (root / ".env").write_text("needle=supersecret\n")
    return root


@pytest.fixture
def settings(project: Path) -> Settings:
    return Settings(allowed_workspaces=(project,))


def call(server_or_params: Any, tool: str, arguments: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(server_or_params) as client:
            return await client.call_tool(tool, arguments, read_timeout_seconds=180)

    return asyncio.run(run())


def text_of(result: Any) -> str:
    assert len(result.content) == 1
    return result.content[0].text


def test_only_run_tests_is_not_marked_read_only(settings: Settings) -> None:
    async def run() -> Any:
        async with Client(build_server(FakeLLM(), settings)) as client:
            return await client.list_tools()

    tools = asyncio.run(run()).tools
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description
        assert tool.annotations.read_only_hint is (tool.name != "run_tests")
        assert tool.annotations.destructive_hint is (tool.name == "run_tests")


def test_no_tool_can_write_or_run_commands(settings: Settings) -> None:
    async def run() -> Any:
        async with Client(build_server(FakeLLM(), settings)) as client:
            return await client.list_tools()

    names = " ".join(tool.name for tool in asyncio.run(run()).tools)
    for word in ("write", "edit", "delete", "exec", "shell", "install"):
        assert word not in names


def test_result_is_one_compact_block_without_a_structured_copy(project: Path, settings: Settings) -> None:
    server = build_server(FakeLLM(), settings)
    result = call(server, "analyze_project", {"project_path": str(project)})
    assert result.is_error is False
    assert result.structured_content is None
    text = text_of(result)
    body = json.loads(text)
    assert text == json.dumps(body, separators=(",", ":"))
    assert body["facts"]["frameworks"] == ["FastAPI"]


def test_search_tool_passes_query_and_extensions(project: Path, settings: Settings) -> None:
    server = build_server(FakeLLM(), settings)
    result = call(server, "search_code", {"project_path": str(project), "query": "add_item", "extensions": ["py"]})
    body = json.loads(text_of(result))
    assert [(m["file"], m["line"]) for m in body["matches"]] == [("calc.py", 1)]
    assert body["metrics"]["llm_calls"] == 0


def test_search_tool_cannot_reach_secret_files(project: Path, settings: Settings) -> None:
    server = build_server(FakeLLM(), settings)
    result = call(server, "search_code", {"project_path": str(project), "query": "supersecret"})
    assert json.loads(text_of(result))["matches"] == []


def test_review_tool_labels_sources_and_includes_the_notice(project: Path, settings: Settings) -> None:
    server = build_server(FakeLLM({"findings": []}), settings)
    result = call(server, "review_code", {"project_path": str(project), "file_path": "calc.py"})
    body = json.loads(text_of(result))
    assert body["findings"][0]["source"] == "static"
    assert body["findings"][0]["line"] == 1
    assert "unverified" in body["notice"]


def test_blocked_project_is_a_tool_error_with_a_useful_message(tmp_path: Path, settings: Settings) -> None:
    other = tmp_path / "other"
    other.mkdir()
    result = call(build_server(FakeLLM(), settings), "analyze_project", {"project_path": str(other)})
    assert result.is_error is True
    assert "ALLOWED_WORKSPACES" in result.content[0].text


def test_path_traversal_is_a_tool_error(project: Path, settings: Settings) -> None:
    (project.parent / "outside.py").write_text("x = 1\n")
    arguments = {"project_path": str(project), "file_path": "../outside.py"}
    result = call(build_server(FakeLLM(), settings), "review_code", arguments)
    assert result.is_error is True
    assert "escapes" in result.content[0].text


def test_explain_tool_returns_facts_and_the_notice(project: Path, settings: Settings) -> None:
    server = build_server(FakeLLM({"explanation": "Adds an item."}), settings)
    arguments = {"project_path": str(project), "file_path": "calc.py", "symbol": "add_item"}
    body = json.loads(text_of(call(server, "explain_code", arguments)))
    assert (body["start_line"], body["end_line"]) == (1, 3)
    assert body["calls"] == ["items.append"]
    assert "unverified" in body["notice"]


def test_debug_tool_locates_the_error(project: Path, settings: Settings) -> None:
    diagnosis = {"probable_root_cause": "c", "suggested_correction": "f", "line": 2, "evidence": "items.append(item)"}
    error = f'File "{project}/calc.py", line 2, in add_item\nValueError: bad\n'
    server = build_server(FakeLLM(diagnosis), settings)
    body = json.loads(text_of(call(server, "debug_error", {"project_path": str(project), "error_message": error})))
    assert body["error_type"] == "ValueError"
    assert body["location"]["line"] == 2 and body["cause_location"]["line"] == 2


def test_run_tests_tool_discovers_without_permission_but_will_not_run(project: Path, settings: Settings) -> None:
    (project / "test_x.py").write_text("def test_a():\n    pass\n")
    server = build_server(FakeLLM(), settings)
    arguments = {"project_path": str(project), "discover_only": True}
    body = json.loads(text_of(call(server, "run_tests", arguments)))
    assert body["executed"] is False and body["test_functions"] == 1
    refused = call(server, "run_tests", {"project_path": str(project)})
    assert refused.is_error is True
    assert "ALLOWED_TEST_WORKSPACES" in refused.content[0].text


def test_review_tool_removes_near_duplicates_through_the_summarizer(project: Path, settings: Settings) -> None:
    near_duplicate = {
        "line": 2, "category": "bug", "severity": "high", "issue": "Shared list.",
        "recommendation": "Use None.", "evidence": "items.append(item)",
    }  # fmt: skip
    server = build_server(FakeLLM({"findings": [near_duplicate]}), settings)
    body = json.loads(text_of(call(server, "review_code", {"project_path": str(project), "file_path": "calc.py"})))
    assert [f["source"] for f in body["findings"]] == ["static"]
    assert "near-duplicate" in body["summary"]


def stdio_params(project: Path) -> StdioServerParameters:
    # The environment Claude Code provides: no venv activation, and any working directory.
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": os.environ["HOME"],
            "PYTHONPATH": str(PROJECT_ROOT),
            "ALLOWED_WORKSPACES": str(project),
        },
        cwd=str(project.parent),
    )


def test_real_server_over_stdio_with_a_minimal_environment(project: Path) -> None:
    params = stdio_params(project)

    async def run() -> tuple[set[str], Any, Any]:
        async with Client(params) as client:
            tools = await client.list_tools()
            scan = await client.call_tool("analyze_project", {"project_path": str(project)})
            search = await client.call_tool("search_code", {"project_path": str(project), "query": "add_item"})
            return {tool.name for tool in tools.tools}, scan, search

    names, scan, search = asyncio.run(run())
    assert names == EXPECTED_TOOLS
    assert json.loads(text_of(scan))["facts"]["frameworks"] == ["FastAPI"]
    assert json.loads(text_of(search))["matches"][0]["line"] == 1


def test_stdout_carries_only_protocol_messages_while_logs_go_elsewhere(project: Path) -> None:
    params = stdio_params(project)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "analyze_project", "arguments": {"project_path": str(project)}},
        },
    ]
    process = subprocess.Popen(
        [params.command, *params.args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=params.env,
        cwd=params.cwd,
    )
    # Stay connected until the tool answers, as a real client does. Closing stdin ends the server.
    assert process.stdin and process.stdout
    messages: list[dict[str, Any]] = []
    try:
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        for line in process.stdout:
            # json.loads fails this test if anything other than protocol JSON reaches stdout.
            messages.append(json.loads(line))
            if messages[-1].get("id") == 2:
                break
        _, stderr = process.communicate(timeout=30)
    finally:
        process.kill()
    assert {m.get("id") for m in messages} >= {1, 2}
    assert "task_done" in stderr


@pytest.mark.integration
def test_real_server_reviews_with_granite_over_stdio(project: Path) -> None:
    result = call(stdio_params(project), "review_code", {"project_path": str(project), "file_path": "calc.py"})
    body = json.loads(text_of(result))
    assert any(f["source"] == "static" and f["line"] == 1 for f in body["findings"])
    assert body["metrics"]["llm_calls"] >= 1
