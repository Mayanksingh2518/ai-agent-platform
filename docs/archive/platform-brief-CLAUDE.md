# Multi-Agent AI Platform: Project Brief

## When the user types `go`

1. Read [progress.md](progress.md). It holds the current status, the step checklist and the log.
2. Start at the first unchecked step. Do not redo checked steps unless something they depend on has changed.
3. Before building on earlier work, check that it still exists on disk. `progress.md` can be out of date.
4. After each step is tested, tick it in `progress.md`, update its **Current status**, and add a log entry. Add new facts to **Verified environment** or **Decisions** in this file.
5. If a test fails, debug it before moving on. Stop and ask if the fix needs a decision from the user.

## How to work with the user

- **Who runs commands:** Claude runs them and explains each one as it goes (the user asked Claude to run the checks itself). The user can switch to running commands by hand at any time.
- **Never delete anything without asking.** The user said this explicitly.
- The user is building this to understand it, so explain as you build.
- Work incrementally. Do not dump the whole application at once.

Use this format for each development step:

1. Step number and objective: what we are building and why
2. Terminal commands, each explained
3. Exact file path for each file
4. Complete code
5. Explanation of the important functions, classes, logic and architecture
6. Testing: the exact commands and the result
7. Troubleshooting: common errors and fixes
8. Completion checklist

## Development rules

1. Give exact file paths, and explain what each file does.
2. Never assume a file or folder exists. Check first.
3. Use environment variables for configuration. Never hardcode API keys or passwords.
4. Use proper error handling and logging, Python type hints, and Pydantic models for structured data.
5. Write modular, reusable code. Each agent gets its own module, system prompt, tools and responsibilities. No single giant file of agents.
6. Avoid unnecessary dependencies and frameworks. Prefer open-source, self-hostable components.
7. Keep everything compatible with macOS and mindful of 16 GB RAM.
8. Explain the reason before changing an existing file.
9. Never claim code works without running it or verifying tests.
10. Do not move to the next phase until the current one is tested successfully.
11. Do not download new models unnecessarily. If one would substantially help, explain why and ask first.

## Verified environment (checked 2026-11-03)

| Item | Value |
|---|---|
| Machine | Apple M4, native `arm64`, 16 GB RAM, about 98 GB disk free |
| OS | macOS 26.6.2 |
| Python | 3.13.5 default; also 3.11.14 and 3.14.8 (Homebrew) |
| Ollama | 0.40.0 at `http://localhost:11434`, running |
| Docker | 29.0.1, daemon running, about 7.7 GB allocated |
| Other tools | git 2.50.1, gh, brew, uv, Node 24.4.0, npm |
| Not installed | `psql` (not needed on the host: PostgreSQL will run in Docker) |

### Model test results

| Test | Result |
|---|---|
| `granite4.1:3b` structured output | 8/8 valid against a JSON schema |
| Routing, with agent names only in the prompt | 5/8. Wrong: tester → developer, researcher → tester, code_fixer → code_reviewer |
| Tool calling | Correct `read_file` call and path |
| Code review of one small function | Found the off-by-one error, missed division by zero on an empty list |
| Speed | About 40 tokens/s, about 1 s per routing call |
| Memory | 2.6 GB at `num_ctx` 8192 |
| `nomic-embed-text` | 768 dimensions, 2,048-token context, 370 MB loaded; relevant text scored 0.67, unrelated 0.44 |

## Decisions

- **Python:** 3.13 in a `.venv` virtual environment. Confirm dependencies install on it in step 9.
- **Context length:** always set `num_ctx` explicitly, starting at 8192. Granite's maximum of 131,072 is far too much for this machine.
- **Concurrency:** one LLM call at a time to start. Test parallel agents separately in Phase 6.
- **Ollama:** runs natively on the Mac, not in Docker, because Docker on macOS cannot use the GPU.
- **PostgreSQL and pgvector:** run in Docker.
- **Embeddings:** use a 768-dimension pgvector column, keep chunks under 2,048 tokens, and use the `search_document:` and `search_query:` prefixes.
- **Models:** no new downloads for now. After Phase 2, run a larger routing and code-review test and decide from the results.
- **Routing:** give the Supervisor agent descriptions and examples, and add a routing test in Phase 2. Whether this fixes the 5/8 score is untested.
- **Langfuse:** its self-hosted setup needs several extra services, so check memory use before adding it in Phase 8.
- **Git:** `.gitignore` must cover `.env` and `.venv`.

## Project goal

A general-purpose, open-source, production-oriented multi-agent platform that behaves like an AI development and research team. A central Supervisor understands each request, breaks it into tasks, picks agents, delegates, and combines the results.

Example requests: review Python code, find bugs in a Django app, fix errors in a FastAPI project, generate a REST API, research a technical topic, analyze a GitHub repository, create unit and integration tests, review code quality and security, generate documentation, debug terminal errors, explain unfamiliar code, help with architecture, and generate reports.

## Technology stack

| Component | Technology |
|---|---|
| Backend | Python + FastAPI |
| Agent orchestration | LangGraph |
| LLM runtime | Ollama |
| Initial LLM | `granite4.1:3b` |
| Embeddings | `nomic-embed-text` |
| Database and vectors | PostgreSQL + pgvector |
| Agent memory | LangGraph checkpointer + PostgreSQL |
| Code execution | Docker sandbox |
| Frontend | React + TypeScript |
| API docs | FastAPI Swagger |
| Monitoring | Langfuse |
| Version control | Git + GitHub |

## Agents

- **Supervisor:** understands requests, classifies intent, picks and delegates to specialists, tracks execution, handles failures and retries, combines responses.
- **Planner:** breaks down complex requests, identifies dependencies, creates an execution plan, decides what can run in parallel.
- **Research:** researches technical topics, searches documentation and permitted web sources, analyzes GitHub repositories, returns structured findings with sources.
- **Developer:** generates Python code, builds FastAPI and Django apps, creates modules and project structures, implements features.
- **Code Review:** inspects source code; finds bugs, logic errors, security vulnerabilities and performance problems; checks standards; suggests improvements.
- **Code Correction:** understands review findings, generates corrected code and patches, explains changes. Never modifies important files without approval.
- **Debugging:** analyzes errors and stack traces, identifies root causes, suggests fixes and implements them after approval.
- **Testing:** generates pytest tests, runs unit and integration tests, identifies failing cases, verifies corrections.
- **Documentation:** generates READMEs and API docs, explains architecture, documents functions, classes and modules.
- **DevOps:** generates Dockerfiles and Docker Compose configs, helps with CI/CD, reviews deployment configs.

## Architecture

Use a Supervisor-based LangGraph design: `StateGraph`, conditional routing, shared state, subgraphs where appropriate, and structured Pydantic outputs.

```text
User Request
  → Supervisor Agent
  → Intent Classification
  → Planner Agent (when needed)
  → Specialized Agents
  → Code Review / Fact Verification
  → Testing Agent (when applicable)
  → Human Approval (for sensitive actions)
  → Final Response
```

## Target folder structure

Improve this where technically justified, and explain the change.

```text
ai-agent-platform/
├── app/
│   ├── agents/    supervisor, planner, researcher, developer, code_reviewer,
│   │              code_fixer, debugger, tester, documentation, devops (.py)
│   ├── graph/     state, nodes, routing, workflow (.py)
│   ├── tools/     github_tools, filesystem_tools, terminal_tools,
│   │              research_tools, testing_tools (.py)
│   ├── memory/    checkpointer, conversation (.py)
│   ├── api/       routes, schemas (.py)
│   ├── config/    settings.py
│   └── main.py
├── workspace/
├── tests/
├── docker/
├── frontend/
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Security requirements

The platform will eventually read and modify GitHub repositories and execute code. It must have:

- Docker-based isolated execution, with resource limits on containers
- Restricted filesystem access and no unrestricted shell access
- Timeouts on terminal commands
- Human approval before destructive operations and before modifying important source files
- No automatic production deployment
- Protection against prompt injection from repository files
- Secure environment-variable handling
- Audit logs of agent actions

Agents start in read-only mode. Write permissions are enabled gradually.

## Roadmap

- **Phase 1, Environment and Foundation:** see the step checklist in `progress.md`.
- **Phase 2, Supervisor:** shared `AgentState`, Supervisor, structured task classification, conditional routing, Research and Developer agents, connect with LangGraph, test routing.
- **Phase 3, Specialist Agents:** Code Review, Code Correction, Debugging, Planner, Testing, Documentation, DevOps.
- **Phase 4, Tools:** filesystem reading, GitHub repository, terminal, Python execution, pytest execution, documentation search.
- **Phase 5, Memory:** conversation history, PostgreSQL, LangGraph persistence, long-term project memory, context management.
- **Phase 6, Advanced Workflow:** parallel execution, agent-to-agent delegation, retries, failure recovery, human-in-the-loop approvals, workflow visualization.
- **Phase 7, Frontend:** React + TypeScript chat interface, agent status, activity logs, file upload, code display, approval interface, conversation history.
- **Phase 8, Production Readiness:** Docker, authentication, permissions, logging and monitoring, Langfuse, unit and integration testing, deployment documentation.
