"""Shared extraction helpers for the doc-drift suite (tests/docs/).

These tests keep user documentation honest by cross-checking identifiers
the docs mention (env vars, API routes, CLI commands, MCP tools, log
events, file paths) against what the code actually defines. Extraction is
deliberately conservative: prose is never parsed for meaning, only for
mechanical tokens (backticked code spans, table cells, fenced blocks), so
a failure here means a real identifier mismatch, not a wording opinion.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
PACKAGE_DIR = REPO_ROOT / "thestill"

# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^(```|~~~)")


def read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def iter_doc_files() -> list[Path]:
    """All user docs plus the top-level entry points."""
    files = sorted(DOCS_DIR.glob("*.md"))
    for extra in ("README.md", "CLAUDE.md"):
        p = REPO_ROOT / extra
        if p.exists():
            files.append(p)
    return files


def split_fenced_blocks(text: str) -> tuple[list[str], list[str]]:
    """Return (prose_lines, fenced_lines) for a markdown document."""
    prose, fenced = [], []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        (fenced if in_fence else prose).append(line)
    return prose, fenced


def inline_code_spans(text: str) -> list[str]:
    """All `backticked` spans outside fenced blocks."""
    prose, _ = split_fenced_blocks(text)
    spans: list[str] = []
    for line in prose:
        spans.extend(re.findall(r"`([^`]+)`", line))
    return spans


def code_text(text: str) -> str:
    """Everything the doc marks as code: fenced blocks + inline spans."""
    _, fenced = split_fenced_blocks(text)
    return "\n".join(fenced + inline_code_spans(text))


def table_rows(text: str) -> list[list[str]]:
    """All markdown table rows (split into stripped cells), skipping
    header-separator rows."""
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue  # |----|----| separator
        rows.append(cells)
    return rows


def markdown_links(text: str) -> list[str]:
    """Link targets from [text](target) outside fenced blocks."""
    prose, _ = split_fenced_blocks(text)
    targets = []
    for line in prose:
        targets.extend(re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", line))
    return targets


# ---------------------------------------------------------------------------
# Python source scanning
# ---------------------------------------------------------------------------


def iter_python_files() -> list[Path]:
    return [p for p in PACKAGE_DIR.rglob("*.py") if "frontend" not in p.parts and "node_modules" not in p.parts]


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract_env_var_reads() -> dict[str, list]:
    """Env var name -> list of (file, literal default or ELLIPSIS).

    Covers os.getenv(name[, default]), os.environ.get(name[, default]),
    and os.environ[name]. Only literal string names are collected.
    """
    reads: dict[str, list] = {}
    wrapper_re = re.compile(r"^_(env_[a-z]+|[a-z]+_env)$")

    def record(name: str, path: Path, default) -> None:
        reads.setdefault(name, []).append((path, default))

    for path in iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - repo should always parse
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # local wrappers: _env_int("X", 3600) / _int_env("X", 10) ...
                if isinstance(func, ast.Name) and wrapper_re.match(func.id) and node.args:
                    name = _literal_str(node.args[0])
                    if name is not None:
                        default = ...
                        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                            default = node.args[1].value
                        record(name, path, default)
                    continue
                # os.getenv(...) / environ.get(...)
                if isinstance(func, ast.Attribute) and func.attr in ("getenv", "get"):
                    base = ast.unparse(func.value)
                    if base not in ("os", "os.environ", "environ"):
                        continue
                    if func.attr == "get" and base == "os":
                        continue
                    if not node.args:
                        continue
                    name = _literal_str(node.args[0])
                    if name is None:
                        continue
                    default = ...
                    if len(node.args) > 1:
                        default = node.args[1].value if isinstance(node.args[1], ast.Constant) else ...
                    record(name, path, default)
            elif isinstance(node, ast.Subscript):
                base = ast.unparse(node.value)
                if base in ("os.environ", "environ"):
                    name = _literal_str(node.slice)
                    if name is not None:
                        record(name, path, ...)
    return reads


_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]{2,}$")


def extract_log_events() -> set[str]:
    """snake_case string literals passed as the first argument to a
    structlog-style logger call anywhere in the package."""
    events: set[str] = set()
    for path in iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            # logger.info("event", ...) or log_method("event", ...) where
            # log_method = getattr(logger, level)
            is_log_call = (isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS) or (
                isinstance(func, ast.Name) and func.id.startswith("log")
            )
            if not is_log_call:
                continue
            literal = _literal_str(node.args[0])
            if literal and _EVENT_RE.fullmatch(literal):
                events.add(literal)
    return events


def extract_mcp_tool_names() -> set[str]:
    """Tool(name=...) literals across the MCP modules."""
    names: set[str] = set()
    for path in (PACKAGE_DIR / "mcp").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "Tool":
                continue
            for kw in node.keywords:
                if kw.arg == "name":
                    literal = _literal_str(kw.value)
                    if literal:
                        names.add(literal)
    return names


def extract_fastapi_routes() -> set[tuple[str, str]]:
    """(METHOD, path) pairs for every mounted API route.

    Prefixes come from AST-parsing the include_router() calls in
    web/app.py; paths come from importing each router module (cheap —
    the unit suite already imports them) and reading router.routes.
    App-level routes registered inside create_app (the SPA catch-all,
    conditional /docs) are not included here.
    """
    app_src = (PACKAGE_DIR / "web" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(app_src)
    mounts: list[tuple[str, str]] = []  # (module_name, prefix)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "include_router" or not node.args:
            continue
        arg = node.args[0]
        # Modules may expose several routers (e.g. router + admin_router).
        if not (isinstance(arg, ast.Attribute) and arg.attr.endswith("router")):
            continue
        module_name = ast.unparse(arg.value).split(".")[-1]
        prefix = ""
        for kw in node.keywords:
            if kw.arg == "prefix":
                prefix = _literal_str(kw.value) or ""
        mounts.append((module_name, arg.attr, prefix))

    import importlib

    routes: set[tuple[str, str]] = set()
    for module_name, router_attr, prefix in mounts:
        module = importlib.import_module(f"thestill.web.routes.{module_name}")
        for route in getattr(module, router_attr).routes:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                routes.add((method, prefix + route.path))
    return routes


def normalize_path_params(path: str) -> str:
    """/api/podcasts/{slug}/x -> /api/podcasts/{}/x — param names are
    documentation flavor, only position matters for matching."""
    return re.sub(r"\{[^}]+\}", "{}", path.rstrip("/") or "/")
