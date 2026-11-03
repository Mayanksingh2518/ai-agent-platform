# Local Multi-Agent Engine

Local LangGraph agents running on Ollama, intended to be called from Claude Code over MCP so that repository scanning, review and research use fewer Claude tokens.

**Status:** early development. The MCP server does not exist yet. Static review findings are reliable; findings from the local model are often wrong. See [progress.md](progress.md).

## Setup

Requires Python 3.13, ripgrep (`brew install ripgrep`), and Ollama with `granite4.1:3b` pulled.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

In `.env`, set `ALLOWED_WORKSPACES` to the absolute paths of the projects the agents may read. With it empty, all access is denied.

## Usage

```bash
# Review a file or folder (read-only)
.venv/bin/python -m app.main review /path/to/project --file app/service.py --focus security

# Answer a question from the project's local files
.venv/bin/python -m app.main research /path/to/project "How is the cache configured?"

# Let the supervisor choose the agent
.venv/bin/python -m app.main ask /path/to/project "Find bugs in the retrieval module"

# Scan a project without using the LLM
.venv/bin/python -m app.main analyze /path/to/project

# Scan a project and add a short architecture description from the LLM
.venv/bin/python -m app.main summarize /path/to/project

# Search for literal text
.venv/bin/python -m app.main search /path/to/project "def connect" --ext py
```

Results are printed as JSON. Logs go to `logs/agents.log` and stderr.

## Tests

```bash
.venv/bin/python -m pytest                     # everything, needs Ollama running
.venv/bin/python -m pytest -m "not integration"  # no Ollama needed
.venv/bin/python -m tests.eval_review          # measures review quality against Granite
```
# ai-agent-platform
