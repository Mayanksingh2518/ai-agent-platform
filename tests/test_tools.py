"""Filesystem security: allowlist, traversal, symlinks, secrets, exclusions and size limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings
from app.tools.ast_tools import (
    check_python,
    find_secret_literals,
    redact_secrets,
    review_units,
    strip_comments_and_docstrings,
)
from app.tools.filesystem_tools import (
    PathAccessError,
    iter_project_files,
    read_text_file,
    resolve_inside_project,
    resolve_project,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = (tmp_path / "project").resolve()
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')\n")
    return root


@pytest.fixture
def settings(project: Path) -> Settings:
    return Settings(allowed_workspaces=(project,), max_file_bytes=100)


def test_project_inside_allowlist_is_accepted(project: Path, settings: Settings) -> None:
    assert resolve_project(str(project), settings) == project


def test_project_outside_allowlist_is_rejected(tmp_path: Path, settings: Settings) -> None:
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PathAccessError, match="ALLOWED_WORKSPACES"):
        resolve_project(str(other), settings)


def test_empty_allowlist_denies_everything(project: Path) -> None:
    with pytest.raises(PathAccessError):
        resolve_project(str(project), Settings())


def test_sibling_with_shared_prefix_is_rejected(project: Path, settings: Settings) -> None:
    sibling = project.parent / (project.name + "-private")
    sibling.mkdir()
    with pytest.raises(PathAccessError):
        resolve_project(str(sibling), settings)


@pytest.mark.parametrize("path", ["../outside.py", "src/../../outside.py", "/etc/passwd"])
def test_traversal_is_rejected(project: Path, path: str) -> None:
    (project.parent / "outside.py").write_text("secret")
    with pytest.raises(PathAccessError, match="escapes"):
        resolve_inside_project(project, path)


def test_symlink_out_of_project_is_rejected(project: Path) -> None:
    (project.parent / "outside.py").write_text("secret")
    (project / "link.py").symlink_to(project.parent / "outside.py")
    with pytest.raises(PathAccessError, match="escapes"):
        resolve_inside_project(project, "link.py")


@pytest.mark.parametrize("name", [".env", ".env.production", "id_rsa", "server.pem", "credentials.json", ".netrc"])
def test_secret_files_are_blocked(project: Path, name: str) -> None:
    (project / name).write_text("x")
    with pytest.raises(PathAccessError, match="secrets"):
        resolve_inside_project(project, name)


def test_env_example_is_allowed(project: Path) -> None:
    (project / ".env.example").write_text("KEY=")
    assert resolve_inside_project(project, ".env.example").name == ".env.example"


def test_excluded_directory_is_blocked(project: Path) -> None:
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("x")
    with pytest.raises(PathAccessError, match="excluded"):
        resolve_inside_project(project, ".git/config")


def test_discovery_skips_excluded_secret_and_symlinked_files(project: Path) -> None:
    (project / ".venv" / "lib").mkdir(parents=True)
    (project / ".venv" / "lib" / "skip.py").write_text("x")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "skip.py").write_text("x")
    (project / "secrets.py").write_text("x")
    (project / "alias.py").symlink_to(project / "src" / "main.py")
    found = [p.relative_to(project).as_posix() for p in iter_project_files(project, {".py"})]
    assert found == ["src/main.py"]


def test_oversized_file_is_rejected(project: Path, settings: Settings) -> None:
    big = project / "big.py"
    big.write_text("x" * 101)
    with pytest.raises(PathAccessError, match="limit"):
        read_text_file(big, settings)


def test_binary_file_is_rejected(project: Path, settings: Settings) -> None:
    binary = project / "data.py"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(PathAccessError, match="UTF-8"):
        read_text_file(binary, settings)


def test_stripping_preserves_line_numbers_and_real_strings() -> None:
    source = 'def f():\n    """Doc."""\n    x = "# not a comment"  # real comment\n    return x\n'
    assert strip_comments_and_docstrings(source)[:4] == ["def f():", "", '    x = "# not a comment"', "    return x"]


def test_one_line_function_with_docstring_keeps_its_def() -> None:
    source = 'def f(): """Doc."""\n'
    assert strip_comments_and_docstrings(source)[0].startswith("def f():")


def test_form_feed_does_not_shift_line_numbers() -> None:
    source = "x = 1\n\f\ny = 2  # note\n"
    assert strip_comments_and_docstrings(source)[2] == "y = 2"


def test_review_units_split_functions_methods_and_module_code() -> None:
    source = (
        "import os\n"                # 0
        "LIMIT = 5\n"                # 1
        "@decorator\n"               # 2
        "def a():\n"                 # 3
        "    return 1\n"             # 4
        "class C:\n"                 # 5
        "    size = 2\n"             # 6
        "    def m(self):\n"         # 7
        "        return 3\n"         # 8
    )  # fmt: skip
    units = review_units(source, strip_comments_and_docstrings(source))
    assert units == [[2, 3, 4], [7, 8], [1, 6]]


def test_review_units_skip_a_file_of_only_imports_and_comments() -> None:
    source = "# header\nimport os\nfrom sys import path\n"
    assert review_units(source, strip_comments_and_docstrings(source)) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("def f(a=[]):\n    pass\n", "Mutable default"),
        ("def f(*, a={}):\n    pass\n", "Mutable default"),
        ("def f(a=list()):\n    pass\n", "Mutable default"),
        ("try:\n    x()\nexcept:\n    y()\n", "Bare except"),
        ("try:\n    x()\nexcept Exception:\n    pass\n", "silently discarded"),
        ("import subprocess\nsubprocess.run(c, shell=True)\n", "shell=True"),
        ("import os\nos.system(c)\n", "os.system"),
        ("import pickle\npickle.loads(d)\n", "pickle.loads"),
        ("eval(c)\n", "eval"),
        ("import yaml\nyaml.load(s)\n", "yaml.load"),
        ("f = open(p)\ndata = f.read()\n", "without being closed"),
        ("data = open(p).read()\n", "without being closed"),
        ('q = "SELECT * FROM t WHERE a=\'" + a + "\'"\n', "SQL"),
        ('q = f"DELETE FROM t WHERE a={a}"\n', "SQL"),
        ('q = "INSERT INTO t VALUES ({})".format(a)\n', "SQL"),
        ('q = "UPDATE t SET a=%s" % a\n', "SQL"),
        ('API_KEY = "abc123def456"\n', "Hardcoded secret"),
        ('settings.password = "hunter2hunter2"\n', "Hardcoded secret"),
        ('x = "ghp_abcdefghijklmnopqrstuvwxyz"\n', "Hardcoded secret"),
    ],
)
def test_static_check_detects(code: str, expected: str) -> None:
    assert any(expected in finding.issue for finding in check_python(code))


@pytest.mark.parametrize(
    "code",
    [
        "def f(a=None, b=(), c=1, d='x'):\n    pass\n",
        "try:\n    x()\nexcept ValueError:\n    pass\n",
        "try:\n    x()\nexcept Exception as e:\n    log(e)\n",
        "import subprocess\nsubprocess.run(['ls'], shell=False)\n",
        "import subprocess\nsubprocess.run(['ls'])\n",
        "evaluate(c)\n",
        "import yaml\nyaml.load(s, Loader=yaml.SafeLoader)\n",
        "with open(p) as f:\n    f.read()\n",
        "f = open(p)\ntry:\n    f.read()\nfinally:\n    f.close()\n",
        "f = open(p)\nwith f:\n    f.read()\n",
        "def factory(p):\n    return open(p)\n",
        'q = "SELECT * FROM t WHERE a=?"\n',
        'msg = "please select " + name\n',
        'msg = f"update {name}"\n',
        'API_KEY = os.environ["API_KEY"]\n',
        'API_KEY = ""\n',
        'API_KEY = "your-api-key-here"\n',
        'password = "changeme"\n',
        'token_type = "bearer_token"\n',
        'max_tokens = "12345678"\n',
    ],
)
def test_static_check_stays_quiet_on_correct_code(code: str) -> None:
    assert check_python(code) == []


def test_redaction_removes_the_secret_and_keeps_line_numbers() -> None:
    source = 'name = "ok"\nAPI_KEY = "abc123def456"\n'
    lines = redact_secrets(source.split("\n"), find_secret_literals(source))
    assert lines[1] == 'API_KEY = "<redacted>"'
    assert lines[0] == 'name = "ok"'
