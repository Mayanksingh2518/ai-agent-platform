"""Deterministic Python analysis: what the LLM sees, and how a file is divided for review."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from typing import Literal

from pydantic import BaseModel

LINE_BREAK = re.compile(r"\r\n|\r|\n")
SECRET_NAME = re.compile(r"(^|_)(api_?key|secret|secret_?key|token|password|passwd|pwd)$", re.IGNORECASE)
KNOWN_TOKEN = re.compile(r"^(sk-[\w-]{8,}|ghp_\w{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[\w-]{10,})")
PLACEHOLDER = re.compile(r"^(<.*>|\$.*|changeme.*|change_me.*|your[_-].*|example.*|x{3,}|test.*|dummy.*|placeholder.*)$", re.IGNORECASE)
KNOWN_TOKEN_ANYWHERE = re.compile(r"\b(sk-[\w-]{8,}|ghp_\w{20,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[\w-]{10,})")
SECRET_ASSIGNMENT = re.compile(r"""(\b\w*(?:api_?key|secret|token|password|passwd|pwd)["']?\s*[:=]\s*)["'][^"'\s]{8,}["']""", re.IGNORECASE)
SQL_TEXT = re.compile(r"\b(SELECT\b.+\bFROM|INSERT\s+INTO|UPDATE\b.+\bSET|DELETE\s+FROM)\b", re.IGNORECASE | re.DOTALL)


def strip_comments_and_docstrings(source: str) -> list[str]:
    """Return lines with comments and docstrings blanked and line numbers unchanged.

    Repository text is untrusted, and these are the easiest places to hide instructions for the
    model. Raises SyntaxError if the source does not parse.
    """
    tree = ast.parse(source)
    # Split the way the parser counts lines. str.splitlines also breaks on form feeds and others.
    lines = LINE_BREAK.split(source)

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column].rstrip()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        is_docstring = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        # A docstring sharing a line with `def f(): """..."""` must not take the def with it.
        if is_docstring and not lines[first.lineno - 1][: first.col_offset].strip():
            for row in range(first.lineno, first.end_lineno + 1):
                lines[row - 1] = ""
    return lines


def review_units(source: str, lines: list[str]) -> list[list[int]]:
    """Group 0-based line indices into one unit per function or method, plus one for the rest.

    Imports and class headers are left out, since reviewing them alone only wastes an LLM call.
    """
    units: list[list[int]] = []
    covered: set[int] = set()

    def visit(statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                unit = [i for i in range(start - 1, node.end_lineno) if lines[i].strip()]
                covered.update(range(start - 1, node.end_lineno))
                if unit:
                    units.append(unit)
            elif isinstance(node, ast.ClassDef):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                covered.update(range(start - 1, node.body[0].lineno - 1))
                visit(node.body)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                covered.update(range(node.lineno - 1, node.end_lineno))

    visit(ast.parse(source).body)
    remainder = [i for i, line in enumerate(lines) if i not in covered and line.strip()]
    if remainder:
        units.append(remainder)
    return units


class StaticFinding(BaseModel):
    line: int
    category: Literal["bug", "code_quality", "security", "performance"]
    severity: Literal["low", "medium", "high"]
    issue: str
    recommendation: str


class SecretLiteral(BaseModel):
    line: int
    end_line: int
    value: str


class SymbolInfo(BaseModel):
    name: str
    kind: Literal["function", "class"]
    start_line: int
    end_line: int
    signature: str


class PythonOutline(BaseModel):
    functions: int
    classes: list[str]
    is_entry_point: bool


def _call_name(func: ast.expr) -> str:
    """Return a dotted name such as "subprocess.run", or the last known part of it."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _is_str(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def find_secret_literals(source: str) -> list[SecretLiteral]:
    """Find strings that are assigned to secret-like names or have a known token format."""
    found: dict[str, SecretLiteral] = {}

    def add(node: ast.expr) -> None:
        found[node.value] = SecretLiteral(line=node.lineno, end_line=node.end_lineno, value=node.value)

    for node in ast.walk(ast.parse(source)):
        if _is_str(node) and KNOWN_TOKEN.match(node.value):
            add(node)
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if not _is_str(value):
            continue
        looks_secret = len(value.value) >= 8 and " " not in value.value and not PLACEHOLDER.match(value.value)
        for target in targets:
            name = _call_name(target).rsplit(".", maxsplit=1)[-1]
            if looks_secret and SECRET_NAME.search(name):
                add(value)
    return sorted(found.values(), key=lambda secret: (secret.line, secret.value))


def redact_line(line: str) -> str:
    """Redact secrets in one line of any language, where parsing is not possible."""
    line = SECRET_ASSIGNMENT.sub(r'\1"<redacted>"', line)
    return KNOWN_TOKEN_ANYWHERE.sub("<redacted>", line)


def outline_python(source: str) -> PythonOutline:
    """Summarize a module's structure. Raises SyntaxError if the source does not parse."""
    tree = ast.parse(source)
    functions = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    is_entry_point = any(
        isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare)
        and _call_name(n.test.left) == "__name__"
        for n in tree.body
    )
    return PythonOutline(
        functions=functions,
        classes=[c.name for c in classes],
        is_entry_point=is_entry_point,
    )


def imported_modules(source: str, module: str, is_package: bool) -> set[str]:
    """Return absolute names of everything a module imports, resolving relative imports."""
    names: set[str] = set()
    package = module.split(".") if is_package else module.split(".")[:-1]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            prefix = ".".join(base + ([node.module] if node.module else []))
            if prefix:
                names.add(prefix)
            names.update(f"{prefix}.{alias.name}" if prefix else alias.name for alias in node.names)
    return names


def _symbol_kind(node: ast.AST) -> str:
    return "class" if isinstance(node, ast.ClassDef) else "function"


def signature_of(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def list_symbols(tree: ast.Module) -> list[SymbolInfo]:
    """List top-level functions and classes, and each class's methods as `Class.method`."""
    symbols: list[SymbolInfo] = []

    def add(node: ast.AST, name: str) -> None:
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        symbols.append(
            SymbolInfo(
                name=name, kind=_symbol_kind(node), start_line=start, end_line=node.end_lineno,
                signature=signature_of(node),
            )
        )  # fmt: skip

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, node.name)
        elif isinstance(node, ast.ClassDef):
            add(node, node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(child, f"{node.name}.{child.name}")
    return symbols


def calls_in_range(tree: ast.Module, start_line: int, end_line: int) -> list[str]:
    """Return names called within a line range, in source order and without repeats."""
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and start_line <= node.lineno <= end_line
    ]
    calls.sort(key=lambda call: (call.lineno, call.col_offset))
    names = (_call_name(call.func) for call in calls)
    return list(dict.fromkeys(name for name in names if name))


def direct_imports(tree: ast.Module) -> list[str]:
    """Return imported module names as written. Relative imports keep their leading dots."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    return list(dict.fromkeys(names))


def count_test_functions(source: str) -> int:
    """Count test functions and methods. Raises SyntaxError if the source does not parse."""
    tree = ast.parse(source)
    count = sum(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
        for n in tree.body
    )
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += sum(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
                for n in node.body
            )
    return count


def redact_secrets(lines: list[str], secrets: list[SecretLiteral]) -> list[str]:
    redacted = list(lines)
    for secret in secrets:
        for row in range(secret.line, secret.end_line + 1):
            redacted[row - 1] = redacted[row - 1].replace(secret.value, "<redacted>")
    return redacted


def _string_built_sql(node: ast.AST) -> bool:
    """True for SQL text combined with runtime values by +, %, .format() or an f-string."""
    if isinstance(node, ast.JoinedStr):
        text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
        return bool(SQL_TEXT.search(text)) and any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = node.left
        while isinstance(left, ast.BinOp):
            left = left.left
        return _is_str(left) and bool(SQL_TEXT.search(left.value))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _is_str(node.func.value) and bool(SQL_TEXT.search(node.func.value.value))
    return False


def check_python(source: str) -> list[StaticFinding]:
    """Detect defects that can be established from the code's structure alone.

    Each rule favors missing a case over a false alarm, because Claude Code treats these results
    as reliable. Raises SyntaxError if the source does not parse.
    """
    tree = ast.parse(source)
    findings: dict[tuple[int, str], StaticFinding] = {}

    def add(line: int, category: str, severity: str, issue: str, recommendation: str) -> None:
        findings[(line, issue)] = StaticFinding(
            line=line, category=category, severity=severity, issue=issue, recommendation=recommendation
        )

    # Handles that something manages: `with` items, and names closed explicitly.
    managed_calls: set[int] = set()
    managed_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                managed_calls.add(id(item.context_expr))
                if isinstance(item.context_expr, ast.Name):
                    managed_names.add(item.context_expr.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
        ):
            managed_names.add(node.func.value.id)

    for secret in find_secret_literals(source):
        add(secret.line, "security", "high", "Hardcoded secret in source code.",
            "Load it from the environment or a secrets manager, and rotate the exposed value.")  # fmt: skip

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                mutable_call = isinstance(default, ast.Call) and _call_name(default.func) in {"list", "dict", "set"}
                if isinstance(default, (ast.List, ast.Dict, ast.Set)) or mutable_call:
                    add(default.lineno, "bug", "medium", "Mutable default argument is shared between calls.",
                        "Default to None and create the object inside the function.")  # fmt: skip

        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                add(node.lineno, "bug", "medium", "Bare except also catches SystemExit and KeyboardInterrupt.",
                    "Catch the specific exceptions you expect.")  # fmt: skip
            elif (
                _call_name(node.type) in {"Exception", "BaseException"}
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                add(node.lineno, "bug", "medium", "Exception is silently discarded.",
                    "Catch specific exceptions, or at least log the failure.")  # fmt: skip

        elif isinstance(node, ast.Assign) and _is_open_call(node.value) and id(node.value) not in managed_calls:
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if names and not managed_names.intersection(names):
                add(node.lineno, "bug", "medium", "File opened without being closed.",
                    "Use a with statement.")  # fmt: skip

        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            shell = keywords.get("shell")
            if (
                name.startswith("subprocess.")
                and isinstance(shell, ast.Constant)
                and shell.value is True
            ):
                add(node.lineno, "security", "high", "subprocess call uses shell=True.",
                    "Pass an argument list without shell=True.")  # fmt: skip
            elif name in {"os.system", "os.popen"}:
                add(node.lineno, "security", "high", f"{name} runs its argument through a shell.",
                    "Use subprocess with an argument list.")  # fmt: skip
            elif name in {"pickle.load", "pickle.loads", "marshal.load", "marshal.loads"}:
                add(node.lineno, "security", "high", f"{name} can execute code from untrusted data.",
                    "Use a data-only format such as JSON unless the source is fully trusted.")  # fmt: skip
            elif name in {"eval", "exec"}:
                add(node.lineno, "security", "high", f"{name} runs dynamically built code.",
                    "Replace it with explicit logic, or ast.literal_eval for literals.")  # fmt: skip
            elif name == "yaml.load" and "Loader" not in keywords and len(node.args) < 2:
                add(node.lineno, "security", "high", "yaml.load without a Loader can be unsafe.",
                    "Use yaml.safe_load.")  # fmt: skip
            elif (
                isinstance(node.func, ast.Attribute)
                and _is_open_call(node.func.value)
                and id(node.func.value) not in managed_calls
            ):
                add(node.lineno, "bug", "medium", "File opened without being closed.",
                    "Use a with statement.")  # fmt: skip

        if _string_built_sql(node):
            add(node.lineno, "security", "high", "SQL text is built from runtime values, which allows SQL injection.",
                "Use parameterized queries.")  # fmt: skip

    return sorted(findings.values(), key=lambda finding: finding.line)


def _is_open_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
