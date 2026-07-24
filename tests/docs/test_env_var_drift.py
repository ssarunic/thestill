"""configuration.md and .env.example stay in sync with the env vars the
code actually reads.

Three invariants:

1. Every env var documented in a configuration.md table exists in code.
2. Every env var the code reads is documented in configuration.md.
3. Where both configuration.md and the code state a simple literal
   default for the same var, they agree.

Additions go through the allowlists below only when a var is genuinely
internal (test-only plumbing, standard OS vars) — if you are tempted to
allowlist a real setting, document it instead.
"""

import re

from tests.docs.helpers import DOCS_DIR, REPO_ROOT, extract_env_var_reads, read_doc, table_rows

CONFIGURATION_MD = DOCS_DIR / "configuration.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

# Vars the code reads that are intentionally not user documentation.
INTERNAL_VARS = {
    "HOME",  # OS standard
    "PATH",  # OS standard
    "TEST_DATABASE_URL",  # CI-only switch for the dual-backend contract suites
    "PYTEST_CURRENT_TEST",  # pytest internals
}

# Vars documented for operators but read outside thestill/ (deploy tooling,
# docker-compose, frontend build) or via a mechanism AST extraction misses.
DOCUMENTED_ELSEWHERE: set[str] = set()

# Read via a dynamic name the AST extractor cannot see:
# config.py builds f"{stage.upper()}_PARALLEL_JOBS" per pipeline stage.
DYNAMIC_READ_VARS = {
    f"{stage}_PARALLEL_JOBS"
    for stage in (
        "DOWNLOAD",
        "DOWNSAMPLE",
        "TRANSCRIBE",
        "CLEAN",
        "SUMMARIZE",
        "EXTRACT_ENTITIES",
        "RESOLVE_ENTITIES",
        "REINDEX",
        "REFRESH_FEED",
    )
}


def documented_vars() -> set[str]:
    """Backticked ALL_CAPS tokens in the first column of configuration.md
    tables, plus assignment lines in .env.example."""
    doc_vars: set[str] = set()
    for cells in table_rows(read_doc(CONFIGURATION_MD)):
        if not cells:
            continue
        match = re.fullmatch(r"`([A-Z][A-Z0-9_]{2,})`", cells[0])
        if match:
            doc_vars.add(match.group(1))
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]{2,})=", line)
        if match:
            doc_vars.add(match.group(1))
    return doc_vars


def test_documented_env_vars_exist_in_code():
    code_vars = set(extract_env_var_reads()) | DYNAMIC_READ_VARS
    assert len(code_vars) > 60, "env-var extraction looks broken (too few reads)"
    doc_vars = documented_vars()
    assert len(doc_vars) > 60, "doc-var extraction looks broken (too few rows)"
    ghosts = doc_vars - code_vars - DOCUMENTED_ELSEWHERE
    assert not ghosts, (
        "configuration.md/.env.example document env vars the code never "
        f"reads (rename or delete them): {sorted(ghosts)}"
    )


def test_code_env_vars_are_documented():
    code_vars = set(extract_env_var_reads())
    missing = code_vars - documented_vars() - INTERNAL_VARS
    assert not missing, (
        "Code reads env vars that docs/configuration.md never documents "
        f"(add them to a table, or to INTERNAL_VARS if truly internal): {sorted(missing)}"
    )


def _doc_defaults() -> dict[str, str]:
    """Var -> backticked default from configuration.md tables that have a
    Default column. Only simple literal tokens are considered; templated
    or prose defaults are skipped."""
    defaults: dict[str, str] = {}
    for cells in table_rows(read_doc(CONFIGURATION_MD)):
        if len(cells) < 3:
            continue
        name_match = re.fullmatch(r"`([A-Z][A-Z0-9_]{2,})`", cells[0])
        default_match = re.fullmatch(r"`([^`{}\s]+)`", cells[-1])
        if name_match and default_match:
            defaults[name_match.group(1)] = default_match.group(1)
    return defaults


def _normalize(value) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def test_documented_defaults_match_code():
    code_reads = extract_env_var_reads()
    mismatches = []
    for var, doc_default in _doc_defaults().items():
        literal_defaults = {
            _normalize(default)
            for _, default in code_reads.get(var, [])
            if default is not ... and default not in (None, "")
        }
        if not literal_defaults:
            continue  # default is computed or empty; nothing to compare
        if doc_default not in literal_defaults:
            mismatches.append(f"{var}: doc says `{doc_default}`, code says {sorted(literal_defaults)}")
    assert not mismatches, "configuration.md Default column disagrees with code defaults:\n" + "\n".join(mismatches)
