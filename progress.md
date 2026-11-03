# Progress

The brief, rules and decisions are in [CLAUDE.md](CLAUDE.md).

## Current status

- **Phase:** 5 is built and tested. Phase 6, Token Optimization, is next. Phase 4 step 6 still needs project paths from the user.
- **To use the new tools:** reload VS Code, because the running server still has the old four. A real Claude Code call to `explain_code`, `debug_error` and `run_tests` is not yet verified.
- **Tests:** see the latest log entry (`.venv/bin/python -m pytest`)
- **MCP server:** registered at user scope. Seven tools exist; the original four are verified by real calls from Claude Code
- **Token savings:** not measured
- **Open quality issue:** Granite's review findings and prose are unreliable; see below

## Review and model quality

Measured with `python -m tests.eval_review` on small fixtures, so treat these as indicative.

| Measure | Result |
|---|---|
| Seeded bugs, static checks only | 7/9, no LLM |
| False positives on the clean file | Static 0; Granite 1 |
| Granite on seeded bugs | Found the O(n^2) loop (reported on line 40; the eval expects 39, so it scores as missed). Stopped finding division by zero after the stricter prompt |
| `app/tools/filesystem_tools.py` | Was 17 findings; now 0 static and 1 from Granite, which looks wrong |
| `app/agents/researcher.py` | 0 static and 4 from Granite: the substring-match point is plausible, and the other 3 look wrong |
| Injected comment | No longer reaches Granite: comments are stripped before the model sees the code |
| Consistency | `inject.py` and `noinject.py` differ only in line numbers after stripping, yet got 2 and 0 Granite findings |

Granite findings are labelled `source: model, confidence: low`, capped at 10 per run and listed after static findings. Treat them as leads. Static checks do not cover logic errors such as division by zero.

`summarize_project` prose is also unverified: on LangGraph's source it added "distributed graph processing", which the facts do not show. The scanned facts are deterministic.

`debug_error` diagnoses are unverified too. Given 10 made-up errors, several impossible in the code shown (an `IndexError` with no indexing, a `RecursionError` with no recursion), Granite gave a confident cause every time and never said the code did not explain the error. Parsed locations are reliable. Its evidence check only proves the quoted line exists.

Granite also rambled until the output limit in about 1 of 15 diagnosis runs, breaking the JSON. Unusable output now gets one retry at a higher temperature; 0 of 10 failed on the next trial, a small sample.

## Open decisions for the user

- **Try a stronger review model?** Only if Granite's findings prove not worth having in real use. The alternative is to turn off model findings by default and use static checks only. Any download needs approval.

## Phases

- [x] Phase 1: Environment and Foundation
- [x] Phase 2: LangGraph
- [x] Phase 3: Project Intelligence
- [ ] Phase 4: MCP Integration
- [x] Phase 5: Advanced Agents (a real Claude Code call to the new tools is pending a reload)
- [ ] Phase 6: Token Optimization

## Phase 4 steps

- [x] 1. Read the installed `mcp` 2.x API before writing any server code
- [x] 2. Implement `app/mcp/server.py` with the tools that already have agents: `analyze_project`, `search_code`, `review_code`, `summarize_project`
- [x] 3. Test the server over real STDIO with an MCP client, confirming stdout carries only protocol messages
- [x] 4. Register with Claude Code at user scope (approved by the user; command below)
- [x] 5. Reload the session and test a real delegation from Claude Code. The tools loaded and were called directly in the session; `/mcp` itself is a user command and was not used
- [ ] 6. Add the user's real projects to `ALLOWED_WORKSPACES`, which currently lists only this workspace

Registration command. User scope is recommended, because the server is meant for use across projects and its paths are specific to this machine:

```bash
~/.vscode/extensions/anthropic.claude-code-2.1.314-darwin-arm64/resources/native-binary/claude mcp add --scope user local-agents -e PYTHONPATH=/Users/rishabhrai/ai-agent-platform -- /Users/rishabhrai/ai-agent-platform/.venv/bin/python -m app.mcp.server
```

To undo: the same binary with `mcp remove --scope user local-agents`.

## Phase 4 reminders

- The tool schemas and instructions add to every Claude Code session, a cost to weigh in the Phase 6 comparison. Measure with all seven tools.

## Phase 6 steps

- [ ] 1. Reload and verify `explain_code`, `debug_error` and `run_tests` from a real Claude Code session
- [ ] 2. Cache project scans and LLM results by content hash
- [ ] 3. Track files returned to Claude and result size in the metrics
- [ ] 4. Compare actual Claude Code usage with and without delegation, on real tasks and including the per-session overhead
- [ ] 5. Decide from the data which tools are worth keeping, including whether Granite's text earns its cost

## Known limits of `run_tests`

Not a sandbox. Test code can still read files by absolute path, use the network and write to disk; only the environment and `HOME` are cleaned. There are no memory or disk limits, and a process that starts its own session can survive the timeout kill. Only `ALLOWED_TEST_WORKSPACES` projects can run. Docker isolation is the stronger option if untrusted projects are ever added.

## Deferred

- `docs/archive/` can skew Research Agent answers on this repo. This affects only this repo, so it was not worth adding a special exclusion.
- Research Agent quality on real projects has barely been tested.
- More static rules could be added, such as nested loops over the same data.

## Log

Newest first.

### 2026-11-03

- Phase 5: added `explain_code`, `debug_error`, `run_tests` and a deterministic Summarizer node. 189 tests passed, including real Granite calls. Tool schemas and instructions now total 5,235 characters per session. `run_tests` has its own allowlist, and only this workspace is on it. The Research Agent is still not exposed as an MCP tool, because the brief's tool list does not include it.
- Found by a failing integration test: Granite's runaway output, now retried once at a higher temperature. Local paths are removed from debug prompts.
- Phase 4 verified from a real Claude Code session: all four tools returned correct results on this workspace, and `~/.ssh` and a `../../.ssh/config` traversal were both refused. The seeded review returned 7 static findings and 1 from Granite (the O(n^2) loop, correct, though it also claimed a stack overflow risk, which is wrong). It missed division by zero. Took 15 s and 8 local LLM calls. The user committed and pushed Phase 4.
- Phase 4: read the `mcp` 2.3.0 API (FastMCP is now `MCPServer`), built the server and tested all four tools over real STDIO with a minimal environment. stdout carries only protocol messages. Results are one compact JSON block, with no structured copy, because that would send the result twice.
- The user created a Git repo with `origin/main` and committed earlier work. `.env` is not tracked.
- Phase 3: added deterministic review checks with secret redaction, `project_tools`, `search_tools`, the Project Scanner, and `analyze`, `summarize` and `search` CLI commands. Installed ripgrep 15.2.0 with Homebrew, with the user's approval.
- Found by trying LangGraph's source and by tests, then fixed: quoted secret keys were not redacted in search results, and stdlib imports were credited to local files with the same name. Also added handling for `src/` layouts and for scanning a folder that is itself a package.
- Verified search with an empty `PATH` from another directory. LangGraph's 111 files were summarized in 2 s using 315 tokens; the routing eval is still 15/15.
- New brief received: local MCP worker engine. Moved the previous `CLAUDE.md` and `progress.md` to `docs/archive/`.
- Phase 1: created the venv, installed pinned dependencies, added settings, logging and the Ollama client. Tested direct Granite calls and a minimal LangGraph workflow.
- Phase 2: added the path security layer, AgentState, Supervisor, Code Review and Research agents, conditional routing, retries and a CLI (`python -m app.main`).
- Found and fixed two review defects: prompt injection through comments, and low recall on whole-file review (1/9).
