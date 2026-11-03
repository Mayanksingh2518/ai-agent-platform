"""Command line for running the workflow without Claude Code. Prints JSON."""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.config.logging_config import configure_logging
from app.config.settings import get_settings
from app.graph.workflow import run_task
from app.llm import OllamaLLM


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local agent task.")
    commands = parser.add_subparsers(dest="command", required=True)

    review = commands.add_parser("review", help="Review Python code")
    review.add_argument("project_path")
    review.add_argument("--file", dest="file_path")
    review.add_argument("--focus")

    research = commands.add_parser("research", help="Answer a question from local files")
    research.add_argument("project_path")
    research.add_argument("question")

    ask = commands.add_parser("ask", help="Let the supervisor choose the agent")
    ask.add_argument("project_path")
    ask.add_argument("request")

    analyze = commands.add_parser("analyze", help="Scan a project without using the LLM")
    analyze.add_argument("project_path")

    summarize = commands.add_parser("summarize", help="Scan a project and describe its architecture")
    summarize.add_argument("project_path")

    search = commands.add_parser("search", help="Search code for literal text")
    search.add_argument("project_path")
    search.add_argument("query")
    search.add_argument("--ext", dest="extensions", action="append", help="Repeat for several extensions")

    explain = commands.add_parser("explain", help="Explain a Python file, or one symbol in it")
    explain.add_argument("project_path")
    explain.add_argument("file_path")
    explain.add_argument("--symbol")

    debug = commands.add_parser("debug", help="Locate and diagnose an error")
    debug.add_argument("project_path")
    debug.add_argument("error_message")
    debug.add_argument("--file", dest="file_path")

    tests = commands.add_parser("tests", help="Run a project's tests, which executes its code")
    tests.add_argument("project_path")
    tests.add_argument("--path", dest="test_path")
    tests.add_argument("--discover-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    configure_logging(settings)
    llm = OllamaLLM(settings)

    kwargs: dict[str, Any] = {"project_path": args.project_path}
    if args.command == "review":
        kwargs.update(task="code_review", file_path=args.file_path, focus=args.focus)
    elif args.command == "research":
        kwargs.update(task="research", request=args.question)
    elif args.command == "analyze":
        kwargs.update(task="analyze_project")
    elif args.command == "summarize":
        kwargs.update(task="summarize_project")
    elif args.command == "search":
        kwargs.update(task="search_code", query=args.query, extensions=args.extensions)
    elif args.command == "explain":
        kwargs.update(task="explain_code", file_path=args.file_path, symbol=args.symbol)
    elif args.command == "debug":
        kwargs.update(task="debug_error", error_message=args.error_message, file_path=args.file_path)
    elif args.command == "tests":
        kwargs.update(task="run_tests", test_path=args.test_path, discover_only=args.discover_only)
    else:
        kwargs.update(request=args.request)

    print(json.dumps(run_task(llm, settings, **kwargs), indent=2))


if __name__ == "__main__":
    main()
