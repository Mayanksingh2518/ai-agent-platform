# Progress

The project brief, rules and decisions are in [CLAUDE.md](CLAUDE.md).

## Current status

- **Phase:** 1, Environment and Foundation
- **Last completed:** Step 5, environment and model checks (2026-11-03)
- **Up next:** Step 6, set up the project directory
- **Files created so far:** `CLAUDE.md`, `progress.md`
- **Blockers:** none

## Phase checklist

- [ ] Phase 1: Environment and Foundation (in progress)
- [ ] Phase 2: Supervisor
- [ ] Phase 3: Specialist Agents
- [ ] Phase 4: Tools
- [ ] Phase 5: Memory
- [ ] Phase 6: Advanced Workflow
- [ ] Phase 7: Frontend
- [ ] Phase 8: Production Readiness

## Phase 1 steps

- [x] 1. Check the Mac environment
- [x] 2. Check the Python version
- [x] 3. Check Ollama version and running status
- [x] 4. Check installed models
- [x] 5. Assess whether Granite and Nomic are sufficient
- [ ] 6. Set up the project directory: it already exists, so check its contents, then `git init` and add `.gitignore`
- [ ] 7. Create the virtual environment at `.venv` with Python 3.13
- [ ] 8. Build the folder structure
- [ ] 9. Create `requirements.txt` and install the minimum dependencies
- [ ] 10. Create `.env.example` and `.env`, connect Granite to LangGraph, and test a basic LLM call

Phase 1 is done only when step 10's test has been run and passes.

## Log

Newest first. Record what was done and what was tested, including failures.

### 2026-11-03

- Ran the environment checks: no blockers. `psql` is not installed, which is fine because PostgreSQL will run in Docker.
- Tested the models. Granite: structured output 8/8, routing 5/8, tool calling correct, found 1 of 2 bugs in a code review test. Nomic embeddings work.
- Created `CLAUDE.md` and `progress.md`. No application code exists yet.
