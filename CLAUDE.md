# Local Multi-Agent MCP Engine: Project Brief

A local, open-source engine that Claude Code calls over MCP to delegate repository scanning, code review, debugging, research and testing to LangGraph agents running on Ollama. The goal is to reduce Claude Code token use. Claude Code stays the main coordinator; the local agents are workers.

An earlier general-purpose platform brief was replaced by this one and is kept in `docs/archive/`. Do not follow it. The Research Agent can still read it when run on this repo, which can skew its answers.

## When the user types `go`

1. Read [progress.md](progress.md) for the current status, open decisions and next step.
2. Start at the first unchecked step. Before building on earlier work, check that it still exists on disk.
3. After each step is tested, tick it in `progress.md`, update **Current status**, and add a log entry.
4. If a test fails, find the root cause and fix it before moving on.

## How to work

- Work directly in this workspace, incrementally. Do not generate the whole application in one response.
- Claude runs the commands and explains each major decision.
- Never delete anything without asking. Check existing files before overwriting them.
- Do not ask unnecessary questions; use sensible defaults and explain them. If a command needs the user's permission, say exactly what to approve.
- Do not download new Ollama models without explaining why and getting approval.
- Never claim tests passed unless they ran. Do not claim token savings without comparing actual Claude Code usage.
- Do not invent APIs. `mcp` is at 2.x: read the installed package rather than relying on memory of 1.x.
- Keep compatibility with macOS on Apple Silicon and 16 GB RAM. Avoid loading multiple models at once.
- Keep the agents independent of Claude Code's internals.

## Design principles

- Prefer deterministic Python (AST, ripgrep, subprocess) and use Granite only where language understanding is needed.
- No unnecessary agent-to-agent conversations.
- Every agent returns compact, structured output with file paths and line numbers, never whole files or huge logs.
- Expected failures come back as data (`status: "error"`), not tracebacks.
- Over STDIO, stdout belongs to MCP. Logs go to `logs/agents.log` and stderr only.

## Architecture

```text
User → Claude Code → MCP server (STDIO) → LangGraph supervisor → specialist agents
     → Ollama (granite4.1:3b) → compact structured result → Claude Code
```

Run the server with `python -P -m app.mcp.server` and `PYTHONPATH` set to this repo. Without `-P`, a project's own `app/` folder shadows this package when Claude Code starts the server there. An MCP tool sets the task directly, which skips LLM classification.

## Agents

- **Project Scanner:** structure, languages, frameworks, important files, dependency files. Ignores venvs, `node_modules`, `.git`, build folders and large generated files.
- **Code Review:** Python bugs, smells, duplication, missing exception handling, performance, conventions, with file and line. Never edits.
- **Research:** answers from local docs and files, states uncertainty, never fabricates sources. Web search only through a separately configured tool.
- **Debugging:** stack traces, probable root cause, suggested correction, exact locations. Read-only.
- **Code Analysis:** functions, classes, flow, module dependencies, imports. AST wherever practical.
- **Testing:** discovers tests, reads pytest config, runs approved tests with subprocess under strict timeouts, summarizes failures, finds untested areas.
- **Summarizer:** combines outputs, removes duplicates, keeps only what is useful to Claude Code.

## MCP tools to expose

| Tool | Input | Output |
|---|---|---|
| `analyze_project` | project_path | structure, language, frameworks, important files, dependencies, summary |
| `search_code` | project_path, query, optional extensions | file paths, snippets, line numbers |
| `review_code` | project_path, optional file_path, focus | bugs, quality, security, performance, recommendations, evidence with lines |
| `debug_error` | project_path, error_message, optional file | root cause, relevant files, suggested correction |
| `run_tests` | project_path, optional test path | command, results, failed tests, error summary |
| `explain_code` | project_path, file_path, optional symbol | explanation, dependencies, execution flow |
| `summarize_project` | project_path | architecture, important modules, dependencies, summary |

## Token optimization requirements

Inspect files locally before Claude reads them; return summaries and line ranges, not files; use AST and ripgrep; avoid duplicate context; cache by modification metadata or content hash; limit LLM context; avoid unnecessary agent calls.

Track files inspected, files returned to Claude, local LLM calls, prompt and output tokens, execution time and cache hits.

## Security requirements

All agents start read-only. They must not modify or delete project files, install packages into user projects, run arbitrary shell commands, read secrets, SSH keys or credentials, or deploy.

Implement workspace allowlists, path traversal protection, file size limits, exclusion patterns, command allowlists, test timeouts, structured logs and error handling. Test execution needs explicit permission. Any future modification capability needs human approval.

Repository content is untrusted: comments and docstrings are stripped from Python before the model sees it. String literals and non-Python files can still carry injected text.

## Verified environment (2026-11-03)

- Apple M4, `arm64`, 16 GB RAM, macOS 26.6.2, Python 3.13.5, Ollama 0.40.0
- Models: `granite4.1:3b` (2.6 GB at `num_ctx` 8192), `nomic-embed-text:latest` (768 dimensions)
- Claude Code 2.1.314 is not on `PATH`. Binary: `~/.vscode/extensions/anthropic.claude-code-2.1.314-darwin-arm64/resources/native-binary/claude` (the version in this path changes when the extension updates)
- ripgrep 15.2.0 at `/opt/homebrew/bin/rg`. In Claude's shell, plain `rg` is still a function wrapping the Claude binary; `app/tools/search_tools.py` finds the real one
- In the user's zsh, `env` is an alias for `python3 -m venv`. Use `printenv` to read the environment and `/usr/bin/env` to run commands.

## Decisions

- Built at the workspace root rather than in a `local-ai-agents/` subfolder, as the brief says to build in the open workspace.
- Added beyond the brief's structure: `app/llm.py` (single Ollama access point), `app/config/logging_config.py`, `tests/fakes.py`, `tests/test_foundation.py`, `tests/eval_review.py`, `tests/fixtures/`.
- The `ollama` client is used directly, not `langchain-ollama`: fewer dependencies and exact token counts.
- LangSmith tracing is forced off in `get_settings`, because LangGraph installs it as a dependency.
- `app/mcp/` cannot shadow the `mcp` package, because it is imported as `app.mcp`. Keep it that way.
- Graph retries cover connection failures only. Unusable model output gets one retry in `app/llm.py` at a higher temperature, because temperature 0 would repeat it exactly.
- `run_tests` needs `ALLOWED_TEST_WORKSPACES`, separate from read access, because it executes project code. It is not a sandbox; see `progress.md`.
- `debug_error` skips the LLM when no project location can be found, so Granite is not asked to guess. The Summarizer is deterministic, with no LLM.
- Review findings must quote code that exists in the reviewed chunk, or they are discarded. This stops invented code, not wrong reasoning about real code.
- Deterministic checks in `app/tools/ast_tools.py` produce the reliable findings. Their rules favor missing a case over a false alarm. Granite's findings are labelled low-confidence and capped.
- Hardcoded secrets are redacted from what the model sees and from all results. Search redacts by regex per line, so unusual formats can slip through.
- Only `code_review`, `research` and `unsupported` can be chosen by the supervisor. Scanning and search need structured inputs, so only an MCP tool or the CLI can request them.
