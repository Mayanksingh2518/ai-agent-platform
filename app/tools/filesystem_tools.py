"""Read-only file access limited to allowlisted projects. Every agent reads files through here."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Collection, Iterator
from pathlib import Path

from app.config.settings import Settings

EXCLUDED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
        "build", "dist", "site-packages", ".next", "target",
        ".ssh", ".aws", ".gnupg", ".kube", ".docker",
    }
)  # fmt: skip

SECRET_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "credentials", "credentials.*", "secrets.*", ".netrc", ".npmrc", ".pypirc",
)  # fmt: skip

SAFE_SECRET_LOOKALIKES = frozenset({".env.example", ".env.sample", ".env.template"})


class PathAccessError(Exception):
    """The path is outside the allowlist, blocked, too large or does not exist."""


def _is_secret_file(name: str) -> bool:
    lowered = name.lower()
    if lowered in SAFE_SECRET_LOOKALIKES:
        return False
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SECRET_FILE_PATTERNS)


def resolve_project(project_path: str, settings: Settings) -> Path:
    """Return the real project directory if it lies inside an allowlisted workspace."""
    project = Path(project_path).expanduser().resolve()
    if not any(project.is_relative_to(root) for root in settings.allowed_workspaces):
        raise PathAccessError(f"Project is not in ALLOWED_WORKSPACES: {project}")
    if not project.is_dir():
        raise PathAccessError(f"Project directory does not exist: {project}")
    return project


def resolve_inside_project(project: Path, relative_path: str) -> Path:
    """Resolve a path against the project, following symlinks before checking containment."""
    target = (project / relative_path).resolve()
    if not target.is_relative_to(project):
        raise PathAccessError(f"Path escapes the project: {relative_path}")
    relative = target.relative_to(project)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        raise PathAccessError(f"Path is in an excluded directory: {relative}")
    if _is_secret_file(target.name):
        raise PathAccessError(f"Path looks like a secrets file: {relative}")
    if not target.exists():
        raise PathAccessError(f"Path does not exist: {relative}")
    return target


def iter_project_files(
    project: Path, suffixes: Collection[str] | None = None, root: Path | None = None
) -> Iterator[Path]:
    """Yield matching files (all if no suffixes) in a stable order, skipping excluded directories, secrets and symlinks."""
    for directory, subdirs, names in os.walk(root or project):
        subdirs[:] = sorted(
            d for d in subdirs if d not in EXCLUDED_DIRS and not (Path(directory) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(directory) / name
            matches = suffixes is None or path.suffix.lower() in suffixes
            if matches and not path.is_symlink() and not _is_secret_file(name):
                yield path


def read_text_file(path: Path, settings: Settings) -> str:
    size = path.stat().st_size
    if size > settings.max_file_bytes:
        raise PathAccessError(f"File is {size} bytes, over the {settings.max_file_bytes} limit: {path.name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PathAccessError(f"File is not UTF-8 text: {path.name}") from exc
