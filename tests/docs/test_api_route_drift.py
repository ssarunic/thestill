"""docs/web-server.md route tables stay in sync with the FastAPI app.

Both directions: every documented (method, path) exists in code, and
every mounted route is documented. Param names are normalized — only
position matters.
"""

import re

from tests.docs.helpers import DOCS_DIR, extract_fastapi_routes, normalize_path_params, read_doc, table_rows

WEB_SERVER_MD = DOCS_DIR / "web-server.md"

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Registered on the app object inside create_app rather than on a router,
# so route extraction can't see them; they are real and documented.
APP_LEVEL_DOC_PATHS = {
    ("GET", "/"),  # SPA catch-all
    ("GET", "/docs"),  # gated on ENVIRONMENT/ENABLE_DOCS
}

# Mounted routes intentionally left out of the doc tables.
UNDOCUMENTED_ROUTES: set[tuple[str, str]] = set()


def documented_routes() -> set[tuple[str, str]]:
    routes = set()
    for cells in table_rows(read_doc(WEB_SERVER_MD)):
        if len(cells) < 2:
            continue
        path_match = re.fullmatch(r"`(/[^`]*)`", cells[0])
        if path_match and cells[1] in _METHODS:
            routes.add((cells[1], normalize_path_params(path_match.group(1))))
    return routes


def code_routes() -> set[tuple[str, str]]:
    return {(method, normalize_path_params(path)) for method, path in extract_fastapi_routes()}


def test_documented_routes_exist_in_code():
    docs = documented_routes()
    assert len(docs) > 40, "route-table extraction looks broken (too few rows)"
    ghosts = docs - code_routes() - APP_LEVEL_DOC_PATHS
    assert not ghosts, "web-server.md documents endpoints that do not exist " f"(method, path): {sorted(ghosts)}"


def test_code_routes_are_documented():
    missing = code_routes() - documented_routes() - UNDOCUMENTED_ROUTES
    assert not missing, (
        "Mounted API routes missing from web-server.md "
        f"(add a table row, or add to UNDOCUMENTED_ROUTES if intentional): {sorted(missing)}"
    )
