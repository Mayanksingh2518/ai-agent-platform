"""Literal code search with ripgrep. No shell, no regex from callers, and capped output."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from app.tools.ast_tools import redact_line
from app.tools.filesystem_tools import EXCLUDED_DIRS, SECRET_FILE_PATTERNS

MAX_MATCHES = 50
MAX_MATCHES_PER_FILE = 10
MAX_LINE_CHARS = 200
TIMEOUT_SECONDS = 20
EXTENSION = re.compile(r"^\.?[A-Za-z0-9]{1,10}$")
# Claude Code may start the server with a minimal PATH that leaves out Homebrew.
FALLBACK_RG_PATHS = ("/opt/homebrew/bin/rg", "/usr/local/bin/rg")


class SearchError(Exception):
    """The search could not run, or the request was invalid."""


class SearchMatch(BaseModel):
    file: str
    line: int
    text: str


class SearchResult(BaseModel):
    task: str = "search_code"
    query: str
    matches: list[SearchMatch]
    files_matched: int
    truncated: bool
    summary: str


def find_ripgrep() -> str:
    found = shutil.which("rg") or next((p for p in FALLBACK_RG_PATHS if Path(p).exists()), None)
    if not found:
        raise SearchError("ripgrep is not installed. Install it with: brew install ripgrep")
    return found


def build_command(rg: str, query: str, extensions: list[str] | None, max_file_bytes: int) -> list[str]:
    command = [
        rg,
        "--no-config",
        "--json",
        "--fixed-strings",
        "--smart-case",
        "--sort=path",
        f"--max-count={MAX_MATCHES_PER_FILE}",
        f"--max-filesize={max_file_bytes}",
    ]
    command += [f"--iglob=!**/{name}/**" for name in sorted(EXCLUDED_DIRS)]
    command += [f"--iglob=!{pattern}" for pattern in SECRET_FILE_PATTERNS]
    for extension in extensions or []:
        if not EXTENSION.match(extension):
            raise SearchError(f"Invalid file extension: {extension!r}")
        command.append(f"--iglob=*.{extension.lstrip('.')}")
    # -e and -- stop a query or path that starts with a dash from being read as an option.
    return [*command, "-e", query, "--", "."]


def search_code(
    project: Path, query: str, extensions: list[str] | None, max_file_bytes: int
) -> SearchResult:
    if not query.strip():
        raise SearchError("Search query is empty.")
    command = build_command(find_ripgrep(), query, extensions, max_file_bytes)
    try:
        completed = subprocess.run(
            command, cwd=project, capture_output=True, text=True, errors="replace",
            timeout=TIMEOUT_SECONDS, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise SearchError(f"Search timed out after {TIMEOUT_SECONDS} seconds.") from exc
    # ripgrep exits 1 for no matches and 2 for errors, though it can return results with 2 as well.
    if completed.returncode == 2 and not completed.stdout:
        raise SearchError(f"ripgrep failed: {completed.stderr.strip()[:300]}")

    matches: list[SearchMatch] = []
    files: set[str] = set()
    truncated = False
    for raw in completed.stdout.splitlines():
        event = json.loads(raw)
        if event["type"] != "match":
            continue
        data = event["data"]
        # Non-UTF-8 paths and lines arrive as base64 under "bytes" instead of "text".
        path_text, line_text = data["path"].get("text"), data["lines"].get("text")
        if path_text is None or line_text is None:
            continue
        relative = path_text.removeprefix("./")
        files.add(relative)
        if len(matches) >= MAX_MATCHES:
            truncated = True
            continue
        text = redact_line(line_text.strip())[:MAX_LINE_CHARS]
        matches.append(SearchMatch(file=relative, line=data["line_number"], text=text))

    summary = f"{len(matches)} matches shown from {len(files)} files."
    if truncated:
        summary += f" Results capped at {MAX_MATCHES}; narrow the query or filter by extension."
    return SearchResult(
        query=query, matches=matches, files_matched=len(files), truncated=truncated, summary=summary
    )
