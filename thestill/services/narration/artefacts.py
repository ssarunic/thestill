# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""On-disk narration artefact helpers (spec #33).

The runner writes one ``<digest_id>-<slug>.json`` header per variant
under ``data/narrations/``. Two API routes read those headers back to
expose them to the UI: the digest detail endpoint (per-digest
listing) and the dashboard tile aggregator. Both rely on the same
read-and-skip-corrupt semantics, so the helper lives here next to the
schema it understands.
"""

import json
from pathlib import Path
from typing import Optional

from structlog import get_logger

logger = get_logger(__name__)


def read_narration_header(path: Path) -> Optional[dict]:
    """Read the JSON header off a narration artefact.

    Returns ``None`` on any error (missing file, invalid JSON,
    non-dict payload). Errors are logged at warn level so a corrupt
    artefact is visible to operators but doesn't poison consumers
    that loop over many files.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("narration.read_failed", path=str(path), error=str(exc))
        return None
    if not isinstance(payload, dict):
        return None
    return payload
