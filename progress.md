# Progress

The brief, rules and decisions are in [CLAUDE.md](CLAUDE.md).

## Current status

- **Phase:** 6, Token Optimization, is closed. No saving was measured, whether tool use was optional or forced. See **Token comparison** below. The user chose to unregister the server. The tools remain usable through `python -m app.main`.
- **Tests:** 207 passed, including real Granite calls (`.venv/bin/python -m pytest`)
- **MCP server:** unregistered at the user's choice. The code is intact, and all seven tools were verified from Claude Code before removal
- **Workspaces:** FinRAG is readable; its tests are not allowed to run
- **Token savings:** none measured, on a small sample
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

## Token comparison (FinRAG, 2026-11-03)

Sonnet, read-only, 4 tasks. Raw results are in `logs/token_eval/`; run with `python -m tests.eval_tokens`. "Baseline 2" runs had the server failing to start, so they are also baselines and show normal variation.

| Task | Baseline | Baseline 2 | Tools available |
|---|---|---|---|
| Architecture | 70,379 / $0.085 | 67,249 / $0.064 | 71,256 / $0.079 |
| `filter_sql` usage | 94,045 / $0.064 | 63,566 / $0.047 | 95,251 / $0.051 |
| Review `app/retrieval` | 159,588 / $0.164 | 229,765 / $0.210 | 408,110 / $0.234 |
| Debug | 64,969 / $0.063 | 65,378 / $0.055 | 66,020 / $0.057 |
| **Total** | **388,981 / $0.376** | **425,958 / $0.375** | **640,637 / $0.421** |

- The log shows the server started in all 4 tool runs and made **0 tool calls**. Claude used its own Read and Grep, so this measures the overhead of having the tools available, not delegation.
- One sample each. Identical baseline runs differed by up to 48% in tokens on a single task, so the higher tool-run total is not good evidence of a real cost on its own. It is clearly not a saving.
- Quality: all answers looked right. Claude's reviews found real bugs, confirmed by reading the code: `EmbeddingCache.save` can misalign vectors and keys if interrupted, and `ranked[:rerank_n]` caps results at 30 when `k` is larger. One baseline review found neither. `review_code` found neither.
- Total spent: about $1.24.

Forced delegation, with an added instruction to use the local tools first:

| Task | Tokens / cost | Local tools called |
|---|---|---|
| Architecture | 95,128 / $0.144 | `summarize_project` |
| `filter_sql` usage | 94,939 / $0.059 | `search_code` |
| Review `app/retrieval` | 283,884 / $0.176 | `review_code` |
| Debug | 96,121 / $0.063 | `search_code`, `debug_error` |
| **Total** | **570,072 / $0.441** | 5 calls |

- **No saving:** $0.441 against $0.376 and $0.375 for the baselines. The architecture run was first after the instruction changed the system prompt, so it paid a prompt-cache write. Without that task, costs are about equal, so it may be an ordering effect rather than a real cost.
- **Worse answers:** the review still read 11 files in full, rejected all 3 local findings, and found no bugs. The usage answer said it had not read the function body. The debug answer named `PgStore.load`, which does not exist.
- **Why, as far as these runs show:** tool results are small next to the 50,000+ tokens of a session's base context, and Claude's Read and Grep are already targeted.
- **Limits:** one run per task, one project, Sonnet, short tasks. Long sessions and very large files are untested.
- Total spent on all comparison runs: about $1.68.
- Re-registered with `-P` at user scope; connected from FinRAG.

Decision: the user chose to unregister the server, because the tools added to every session without a measured saving. Nothing was deleted from the repo.

## First real-code result (FinRAG, 2026-11-03)

`app/retrieval`, checked by reading the cited code:

- Before the SQL rule fix: 7 findings (2 static, 5 Granite), and **0 were real bugs**. The 2 static ones were SQL false positives, since fixed. The cost was 54 local LLM calls and 51 s, and 5 of the module's 15 files were skipped by the 10-file limit, including `retriever.py` and `store.py`.
- Static rules after the fix, on all 95 FinRAG Python files: 1 finding (`scripts/ui_screenshot.py:46`, `open().write()` with no close), which is real but minor. Precision is 1 of 1, a tiny sample. How many real bugs they miss is unknown.
- Granite across all real files so far: 10 findings, about 1 plausible (`researcher.py` substring matching).

The seeded fixtures overstated quality: they contain only the bug shapes the rules were written for.

## Open decisions for the user

- **Turn off Granite's review findings by default?** Recommended, given about 1 in 10 plausible on real code. Static-only review would be close to instant and use no LLM. A stronger model is the alternative; any download needs approval. They stay on until the user decides.
- **Allow FinRAG's tests to run?** It is in `ALLOWED_WORKSPACES` for reading only, not `ALLOWED_TEST_WORKSPACES`.

## Phases

- [x] Phase 1: Environment and Foundation
- [x] Phase 2: LangGraph
- [x] Phase 3: Project Intelligence
- [ ] Phase 4: MCP Integration
- [x] Phase 5: Advanced Agents
- [x] Phase 6: Token Optimization (no saving measured)

## Phase 4 steps

- [x] 1. Read the installed `mcp` 2.x API before writing any server code
- [x] 2. Implement `app/mcp/server.py` with the tools that already have agents: `analyze_project`, `search_code`, `review_code`, `summarize_project`
- [x] 3. Test the server over real STDIO with an MCP client, confirming stdout carries only protocol messages
- [x] 4. Register with Claude Code at user scope (approved by the user; command below)
- [x] 5. Reload the session and test a real delegation from Claude Code. The tools loaded and were called directly in the session; `/mcp` itself is a user command and was not used
- [x] 6. Add the user's real projects to `ALLOWED_WORKSPACES`. FinRAG added, read-only

Registration command. User scope is recommended, because the server is meant for use across projects and its paths are specific to this machine:

```bash
~/.vscode/extensions/anthropic.claude-code-2.1.314-darwin-arm64/resources/native-binary/claude mcp add --scope user local-agents -e PYTHONPATH=/Users/rishabhrai/ai-agent-platform -- /Users/rishabhrai/ai-agent-platform/.venv/bin/python -P -m app.mcp.server
```

The server is not registered now. Run this to add it back.

To undo: the same binary with `mcp remove --scope user local-agents`.

## Phase 4 reminders

- The tool schemas and instructions add to every Claude Code session, a cost to weigh in the Phase 6 comparison. Measure with all seven tools.

## Phase 6 steps

- [x] 1. Reload and verify `explain_code`, `debug_error` and `run_tests` from a real Claude Code session
- [x] 2. Cache LLM results by prompt hash. Scans are not cached: at 0.23 s for 1,528 files there is nothing to gain
- [x] 3. Add result size to the metrics and log. "Files returned to Claude" is 0 by design, since no tool returns whole files, so it is not a field
- [x] 4. Compare actual Claude Code usage with and without delegation. No saving, whether tool use was optional or forced
- [x] 5. Decide from the data which tools are worth keeping. Unregistered; the tools stay available from the command line

## Known limits of `run_tests`

Not a sandbox. Test code can still read files by absolute path, use the network and write to disk; only the environment and `HOME` are cleaned. There are no memory or disk limits, and a process that starts its own session can survive the timeout kill. Only `ALLOWED_TEST_WORKSPACES` projects can run. Docker isolation is the stronger option if untrusted projects are ever added.

## Deferred

- `docs/archive/` can skew Research Agent answers on this repo. This affects only this repo, so it was not worth adding a special exclusion.
- Research Agent quality on real projects has barely been tested.
- More static rules could be added, such as nested loops over the same data.

## Log

Newest first.

### 2026-11-03

- The user chose to unregister `local-agents`. Removed from the user config; the command line tools still work.
- Ran the token comparison. The first 7 launches crashed before spending anything, because zsh does not split `$pair`. The next 4 "agents" runs had a failed server, so they became a second baseline. Cause: `python -m` puts the working directory first on the path, so FinRAG's `app` shadowed this one. Fixed with `-P`, confirmed connected from FinRAG, and the MCP tests now start from a folder with its own `app/`. In the valid runs, Claude made 0 tool calls.
- Phase 6: verified `explain_code`, `debug_error` and `run_tests` from a real session, on FinRAG and this workspace; running FinRAG's tests was refused as intended. `debug_error`'s location was right, but Granite's diagnosis only restated the error. Added a model response cache: the FinRAG retrieval review went from 49 s to 0.06 s on a repeat, with 54 of 54 hits. Caching repeats wrong findings as readily as right ones. 207 tests passed.
- Added FinRAG to `ALLOWED_WORKSPACES` (read-only); running its tests is refused as intended. First real review found 0 real bugs in 7 findings; see above. Narrowed the SQL rule to function arguments placed directly in SQL, so it now misses local variables built from outside data.
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
