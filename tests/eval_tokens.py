"""Compares real Claude Code usage with and without the local agents. This spends real usage.

Run with: python -m tests.eval_tokens <task> <baseline|agents>
Each raw result is saved to logs/token_eval/, which git ignores.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.config.settings import PROJECT_ROOT

CLAUDE = Path.home() / ".vscode/extensions/anthropic.claude-code-2.1.314-darwin-arm64/resources/native-binary/claude"
PROJECT = Path.home() / "finrag"
RESULTS = PROJECT_ROOT / "logs" / "token_eval"
MODEL = "sonnet"
MAX_BUDGET_USD = "1"
TIMEOUT_SECONDS = 540
# Read-only in both modes. Subagents and the web are off so each run measures one session on local code.
DISALLOWED = "Edit Write NotebookEdit Bash Agent WebFetch WebSearch"

# The sort of line a user would put in a project's CLAUDE.md to make delegation the habit.
FORCED_INSTRUCTION = (
    "This project has local-agents MCP tools. Use them before reading any files, for scanning, searching, "
    f"explaining, reviewing and debugging, with project_path {PROJECT}. Read a file only to confirm a "
    "specific claim, and read only the lines you need."
)

TRACEBACK = """Traceback (most recent call last):
  File "/Users/rishabhrai/finrag/app/retrieval/pg_store.py", line 85, in load
    raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
ValueError: 120 chunks but 118 vectors"""

TASKS = {
    "architecture": "Describe this project's architecture and main modules in under 200 words.",
    "usage": "Find everywhere filter_sql is used in this project and explain what it does, in under 150 words.",
    "review": "Review app/retrieval for real bugs. List each with file and line. If you find none, say so.",
    "debug": f"What are the likely causes of this error in this project? Answer in under 150 words.\n\n{TRACEBACK}",
}


def build_command(task: str, mode: str) -> list[str]:
    command = [
        str(CLAUDE), "-p", TASKS[task],
        "--output-format", "json",
        "--model", MODEL,
        "--max-budget-usd", MAX_BUDGET_USD,
        "--no-session-persistence",
        "--disallowedTools", DISALLOWED,
        "--strict-mcp-config",
    ]  # fmt: skip
    if mode == "forced":
        command += ["--append-system-prompt", FORCED_INSTRUCTION]
    if mode in {"agents", "forced"}:
        server = {
            "command": str(PROJECT_ROOT / ".venv" / "bin" / "python"),
            # -P because FinRAG has its own `app` package, which would otherwise shadow this one.
            "args": ["-P", "-m", "app.mcp.server"],
            "env": {"PYTHONPATH": str(PROJECT_ROOT)},
        }
        config = {"mcpServers": {"local-agents": server}}
        command += ["--mcp-config", json.dumps(config), "--allowedTools", "mcp__local-agents__*"]
    return command


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in TASKS or sys.argv[2] not in {"baseline", "agents", "forced"}:
        raise SystemExit(f"Usage: <{'|'.join(TASKS)}> <baseline|agents|forced>")
    task, mode = sys.argv[1], sys.argv[2]
    RESULTS.mkdir(parents=True, exist_ok=True)
    # The nested-session marker is removed because this is a separate, self-contained run.
    environment = {key: value for key, value in os.environ.items() if key != "CLAUDECODE"}
    completed = subprocess.run(
        build_command(task, mode),
        cwd=PROJECT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    path = RESULTS / f"{task}-{mode}.json"
    path.write_text(completed.stdout or json.dumps({"stderr": completed.stderr, "code": completed.returncode}))
    print(f"{path.name}: exit={completed.returncode} stdout_chars={len(completed.stdout)}")
    if completed.stderr:
        print(completed.stderr[-500:], file=sys.stderr)


if __name__ == "__main__":
    main()
