"""Test discovery, which is read-only, and pytest execution, which runs project code."""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config.settings import Settings
from app.tools.ast_tools import count_test_functions, imported_modules, redact_line
from app.tools.filesystem_tools import (
    PathAccessError,
    iter_project_files,
    read_text_file,
    resolve_inside_project,
)
from app.tools.project_tools import module_name

logger = logging.getLogger(__name__)

VENV_NAMES = (".venv", "venv", "env")
TEST_CONFIG_FILES = ("pytest.ini", "tox.ini", "setup.cfg", "conftest.py", "tests/conftest.py")
MAX_FAILURES = 20
MAX_MESSAGE_CHARS = 300
MAX_OUTPUT_TAIL_CHARS = 1500
MAX_LISTED = 25
NODE_SUFFIX = re.compile(r"^[\w\[\]\-.:,= ]*$")
FAILURE_LOCATION = re.compile(r"^([\w./-]+\.py):(\d+):", re.MULTILINE)
EXIT_MEANINGS = {2: "interrupted", 3: "internal pytest error", 4: "usage error"}


class TestRunError(Exception):
    """Tests could not be run: not permitted, no environment, or an invalid request."""


class TestFailure(BaseModel):
    test: str
    file: str | None
    line: int | None
    message: str


class TestRunResult(BaseModel):
    task: Literal["run_tests"] = "run_tests"
    command: str
    status: Literal["passed", "failed", "no_tests", "timeout", "error"]
    passed: int
    failed: int
    errors: int
    skipped: int
    failures: list[TestFailure]
    failures_omitted: int
    output_tail: str
    summary: str


class TestDiscovery(BaseModel):
    task: Literal["run_tests"] = "run_tests"
    executed: Literal[False] = False
    test_files: int
    test_functions: int
    config_files: list[str]
    has_virtualenv: bool
    execution_permitted: bool
    modules_not_imported_by_tests: list[str]
    summary: str


def is_test_file(path: Path) -> bool:
    return path.suffix == ".py" and (path.name.startswith("test_") or path.name.endswith("_test.py"))


def find_project_python(project: Path) -> Path | None:
    # Deliberately not resolved: a venv's python is a symlink out of the project.
    for name in VENV_NAMES:
        candidate = project / name / "bin" / "python"
        if candidate.exists():
            return candidate
    return None


def is_execution_permitted(project: Path, settings: Settings) -> bool:
    return any(project.is_relative_to(root) for root in settings.allowed_test_workspaces)


def _iter_python_sources(project: Path, settings: Settings) -> Iterator[tuple[Path, str]]:
    for path in iter_project_files(project, {".py"}):
        with suppress(PathAccessError):
            yield path, read_text_file(path, settings)


def discover_tests(project: Path, settings: Settings) -> TestDiscovery:
    """Describe the test suite by parsing files. Nothing is imported or executed."""
    test_files = 0
    test_functions = 0
    tested: set[str] = set()
    modules: list[str] = []
    for path, source in _iter_python_sources(project, settings):
        name, is_package = module_name(path.relative_to(project))
        if not is_test_file(path):
            if name and not is_package and path.name != "conftest.py" and source.strip():
                modules.append(name)
            continue
        test_files += 1
        with suppress(SyntaxError, ValueError):
            test_functions += count_test_functions(source)
            tested |= imported_modules(source, name, is_package)

    # Only direct imports from test files count, so this is a lead rather than coverage data.
    untested = [module for module in modules if module not in tested and not module.startswith("tests.")]
    config = [name for name in TEST_CONFIG_FILES if (project / name).is_file()]
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        with suppress(PathAccessError):
            if "[tool.pytest" in read_text_file(pyproject, settings):
                config.append("pyproject.toml")
    return TestDiscovery(
        test_files=test_files,
        test_functions=test_functions,
        config_files=config,
        has_virtualenv=find_project_python(project) is not None,
        execution_permitted=is_execution_permitted(project, settings),
        modules_not_imported_by_tests=untested[:MAX_LISTED],
        summary=(
            f"{test_files} test files with {test_functions} test functions found without running them. "
            f"{len(untested)} modules are not imported directly by any test (not a coverage measurement)."
        ),
    )


def resolve_test_target(project: Path, test_path: str | None) -> str | None:
    """Validate a path or pytest node id, so it cannot escape the project or act as an option."""
    if not test_path:
        return None
    file_part, separator, node_part = test_path.partition("::")
    target = resolve_inside_project(project, file_part)
    if not NODE_SUFFIX.match(node_part):
        raise TestRunError(f"Invalid test node id: {node_part!r}")
    relative = target.relative_to(project)
    return f"{relative}{separator}{node_part}" if separator else str(relative)


def parse_junit(path: Path) -> tuple[dict[str, int], list[TestFailure], int]:
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failures: list[TestFailure] = []
    omitted = 0
    root = ET.parse(path).getroot()
    for suite in root.iter("testsuite"):
        for key in counts:
            counts[key] += int(suite.get(key, 0))
    for case in root.iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        if len(failures) >= MAX_FAILURES:
            omitted += 1
            continue
        locations = FAILURE_LOCATION.findall(problem.text or "")
        file, line = locations[-1] if locations else (None, None)
        message = (problem.get("message") or "").strip().replace("\n", " ")
        failures.append(
            TestFailure(
                test=f"{case.get('classname', '')}::{case.get('name', '')}".strip(":"),
                file=file,
                line=int(line) if line else None,
                message=redact_line(message)[:MAX_MESSAGE_CHARS],
            )
        )
    return counts, failures, omitted


def _read_tail(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - MAX_OUTPUT_TAIL_CHARS))
        text = handle.read().decode("utf-8", errors="replace")
    return "\n".join(redact_line(line) for line in text.splitlines())


def run_pytest(project: Path, test_path: str | None, settings: Settings) -> TestRunResult:
    """Run pytest in the project's own virtualenv. This executes the project's code."""
    if not is_execution_permitted(project, settings):
        raise TestRunError(f"Running tests is not permitted here. Add it to ALLOWED_TEST_WORKSPACES: {project}")
    python = find_project_python(project)
    if python is None:
        raise TestRunError(f"No virtualenv found in {project} (looked for {', '.join(VENV_NAMES)}).")
    target = resolve_test_target(project, test_path)

    arguments = ["-m", "pytest", "-q", "--tb=short", "--color=no", "--no-header", "-p", "no:cacheprovider"]
    if target:
        arguments.append(target)
    shown = " ".join([str(python.relative_to(project)), *arguments])

    with tempfile.TemporaryDirectory(prefix="local-agents-tests-") as temporary:
        scratch = Path(temporary).resolve()
        report = scratch / "report.xml"
        output = scratch / "output.txt"
        home = scratch / "home"
        home.mkdir()
        # A clean environment, so API keys and the real HOME's credentials are not handed over.
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(scratch),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        with output.open("wb") as sink:
            process = subprocess.Popen(
                [str(python), *arguments, f"--junitxml={report}"],
                cwd=project,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            timed_out = False
            try:
                exit_code = process.wait(timeout=settings.test_timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = -1
            finally:
                # Also ends anything the tests left running in the background.
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()

        tail = _read_tail(output)
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        failures: list[TestFailure] = []
        omitted = 0
        if report.exists():
            try:
                counts, failures, omitted = parse_junit(report)
            except (ET.ParseError, ValueError) as exc:
                logger.warning("junit_parse_failed error=%s", exc)

    passed = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    status: Literal["passed", "failed", "no_tests", "timeout", "error"]
    if timed_out:
        status = "timeout"
        summary = f"Stopped after {settings.test_timeout_seconds:g} seconds."
    elif exit_code == 0:
        status = "passed"
        summary = f"{passed} passed, {counts['skipped']} skipped."
    elif exit_code == 1:
        status = "failed"
        summary = f"{counts['failures']} failed, {counts['errors']} errors, {passed} passed."
    elif exit_code == 5:
        status = "no_tests"
        summary = "No tests were collected."
    else:
        status = "error"
        summary = f"pytest could not complete: {EXIT_MEANINGS.get(exit_code, f'exit code {exit_code}')}."
    return TestRunResult(
        command=shown,
        status=status,
        passed=max(passed, 0),
        failed=counts["failures"],
        errors=counts["errors"],
        skipped=counts["skipped"],
        failures=failures,
        failures_omitted=omitted,
        # Passing runs need no output, and listed failures already say what went wrong.
        output_tail="" if status == "passed" or (status == "failed" and failures) else tail,
        summary=summary,
    )
