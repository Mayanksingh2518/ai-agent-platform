"""Deterministic project facts: languages, important files, dependencies, frameworks and imports."""

from __future__ import annotations

import json
import logging
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from app.config.settings import Settings
from app.tools.ast_tools import imported_modules, outline_python
from app.tools.filesystem_tools import PathAccessError, iter_project_files, read_text_file

logger = logging.getLogger(__name__)

MAX_FILES_SCANNED = 5000
MAX_PYTHON_FILES_PARSED = 300
MAX_DEPENDENCY_DEPTH = 2
MAX_LISTED = 25

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C",
    ".cpp": "C++", ".cs": "C#", ".swift": "Swift", ".kt": "Kotlin", ".sh": "Shell", ".html": "HTML",
    ".css": "CSS", ".sql": "SQL",
}  # fmt: skip

IMPORTANT_FILE = re.compile(
    r"^(readme(\..+)?|pyproject\.toml|setup\.py|setup\.cfg|requirements.*\.txt|package\.json|manage\.py|"
    r"pytest\.ini|tox\.ini|dockerfile|docker-compose.*\.ya?ml|compose\.ya?ml|makefile|\.env\.example)$",
    re.IGNORECASE,
)

FRAMEWORKS = {
    "django": "Django", "fastapi": "FastAPI", "flask": "Flask", "react": "React", "next": "Next.js",
    "vue": "Vue", "@angular/core": "Angular", "express": "Express", "langgraph": "LangGraph",
    "langchain": "LangChain", "sqlalchemy": "SQLAlchemy", "celery": "Celery", "pytest": "pytest",
}  # fmt: skip

REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


class ProjectFacts(BaseModel):
    total_files: int
    truncated: bool
    languages: dict[str, int]
    top_directories: dict[str, int]
    important_files: list[str]
    dependencies: list[str]
    frameworks: list[str]
    python_files: int = 0
    python_files_unparsed: int = 0
    classes: int = 0
    functions: int = 0
    entry_points: list[str] = []
    most_imported_modules: dict[str, int] = {}


def parse_requirements(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        match = REQUIREMENT_NAME.match(line.strip())
        if match:
            names.add(match.group(1).lower())
    return names


def parse_pyproject(text: str) -> set[str]:
    data = tomllib.loads(text)
    declared = data.get("project", {}).get("dependencies", [])
    names = {m.group(1).lower() for m in map(REQUIREMENT_NAME.match, declared) if m}
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    return names | {name.lower() for name in poetry if name.lower() != "python"}


def parse_package_json(text: str) -> set[str]:
    data = json.loads(text)
    return {name.lower() for key in ("dependencies", "devDependencies") for name in data.get(key, {})}


def read_dependencies(project: Path, important: list[Path], settings: Settings) -> set[str]:
    names: set[str] = set()
    for path in important:
        if len(path.relative_to(project).parts) > MAX_DEPENDENCY_DEPTH + 1:
            continue
        lowered = path.name.lower()
        try:
            if lowered.startswith("requirements") and lowered.endswith(".txt"):
                names |= parse_requirements(read_text_file(path, settings))
            elif lowered == "pyproject.toml":
                names |= parse_pyproject(read_text_file(path, settings))
            elif lowered == "package.json":
                names |= parse_package_json(read_text_file(path, settings))
        except (PathAccessError, ValueError, AttributeError, TypeError) as exc:
            # A malformed manifest should not stop the scan of everything else.
            logger.info("dependency_file_skipped file=%s error=%s", path.name, exc)
    return names


def module_name(relative: Path) -> tuple[str, bool]:
    """Return a file's dotted module name and whether it is a package."""
    parts = list(relative.with_suffix("").parts)
    # In a src layout, code imports `pkg.mod`, not `src.pkg.mod`.
    if parts[0] == "src" and len(parts) > 1:
        parts.pop(0)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def analyze_python(project: Path, files: list[Path], settings: Settings, facts: ProjectFacts) -> None:
    """Fill in structural facts by parsing Python files. Nothing here is executed."""
    sources: dict[str, tuple[str, bool]] = {}
    facts.python_files = len(files)
    entry_points: list[str] = []
    for path in files[:MAX_PYTHON_FILES_PARSED]:
        relative = path.relative_to(project)
        try:
            source = read_text_file(path, settings)
            outline = outline_python(source)
        except (PathAccessError, SyntaxError, ValueError):
            facts.python_files_unparsed += 1
            continue
        facts.classes += len(outline.classes)
        facts.functions += outline.functions
        if outline.is_entry_point:
            entry_points.append(str(relative))
        name, is_package = module_name(relative)
        if name:
            sources[name] = (source, is_package)
    facts.python_files_unparsed += max(0, len(files) - MAX_PYTHON_FILES_PARSED)

    # The scanned folder may itself be a package, imported as `<folder>.module`.
    aliases = {f"{project.name}.{name}": name for name in sources}
    # A bare `import typing` means the standard library, even beside a local typing.py.
    aliases.update(
        {name: name for name in sources if name.split(".")[0] not in sys.stdlib_module_names}
    )
    fan_in: Counter[str] = Counter()
    for name, (source, is_package) in sources.items():
        targets = {aliases.get(target) for target in imported_modules(source, name, is_package)}
        fan_in.update(targets - {None, name})
    facts.entry_points = entry_points[:MAX_LISTED]
    facts.most_imported_modules = dict(fan_in.most_common(10))


def scan_project(project: Path, settings: Settings) -> ProjectFacts:
    languages: Counter[str] = Counter()
    directories: Counter[str] = Counter()
    important: list[Path] = []
    python_files: list[Path] = []
    total = 0
    truncated = False

    for path in iter_project_files(project):
        if total >= MAX_FILES_SCANNED:
            truncated = True
            break
        total += 1
        relative = path.relative_to(project)
        directories[relative.parts[0] if len(relative.parts) > 1 else "."] += 1
        suffix = path.suffix.lower()
        if suffix in LANGUAGES:
            languages[LANGUAGES[suffix]] += 1
        if suffix == ".py":
            python_files.append(path)
        if IMPORTANT_FILE.match(path.name) or (path.parent.name == "workflows" and suffix in {".yml", ".yaml"}):
            important.append(path)

    # Shallowest first, so the root README outranks one buried in a fixture.
    important.sort(key=lambda p: (len(p.relative_to(project).parts), str(p)))
    dependencies = read_dependencies(project, important, settings)
    frameworks = sorted({FRAMEWORKS[name] for name in dependencies if name in FRAMEWORKS})

    facts = ProjectFacts(
        total_files=total,
        truncated=truncated,
        languages=dict(languages.most_common()),
        top_directories=dict(directories.most_common(15)),
        important_files=[str(p.relative_to(project)) for p in important[:MAX_LISTED]],
        dependencies=sorted(dependencies)[:40],
        frameworks=frameworks,
    )
    analyze_python(project, python_files, settings, facts)
    return facts
