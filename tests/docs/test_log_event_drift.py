"""Log event names referenced by the logging/profiling docs are actually
emitted somewhere in the code.

Catches the ``mcp_tool_invoked`` / ``"Episode processed"`` class of drift:
query cookbooks filtering on events that no logger ever emits. Extraction
is scoped to explicit ``event =/: name`` patterns so prose stays out of it.
"""

import re

from tests.docs.helpers import DOCS_DIR, extract_log_events, read_doc

LOGGING_DOCS = [
    DOCS_DIR / "logging-configuration.md",
    DOCS_DIR / "logging-cloud-deployment.md",
    DOCS_DIR / "logging-cloudwatch-queries.md",
    DOCS_DIR / "logging-elastic-queries.md",
    DOCS_DIR / "logging-gcp-queries.md",
    DOCS_DIR / "profiling-refresh.md",
]

# event = "x" | event: "x" | "event": "x" | jsonPayload.event="x" | $.event = "x"
_DOC_EVENT_RE = re.compile(r"""["']?(?:jsonPayload\.|\$\.)?event["']?\s*[=:]\s*["']([a-z][a-z0-9_]{3,})["']""")

# Doc-side names that are legitimate but not literal logger first-args
# (e.g. emitted via a variable). Keep empty unless proven necessary.
KNOWN_INDIRECT_EVENTS: set[str] = set()


def test_doc_referenced_log_events_exist():
    code_events = extract_log_events()
    ghosts = []
    for doc in LOGGING_DOCS:
        for match in _DOC_EVENT_RE.finditer(read_doc(doc)):
            event = match.group(1)
            if event not in code_events and event not in KNOWN_INDIRECT_EVENTS:
                ghosts.append(f"{doc.name}: {event}")
    assert not ghosts, "Logging docs reference events that no logger call emits:\n" + "\n".join(sorted(set(ghosts)))
