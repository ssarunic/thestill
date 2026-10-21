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

"""The ``reprocess`` block a spec #106 run puts on every task's metadata.

Queued by :mod:`thestill.services.episode_reprocess`, inherited by every
successor in the chain, and read by the stage handlers that behave
differently on a reprocess (transcribe, clean, reindex) and by retry.
"""

from datetime import datetime
from typing import Any, Dict, Optional

#: ``initiated_by`` of every task in a reprocess run. An operator tag, so
#: the worker's "nobody wants this episode" stop never applies.
REPROCESS_INITIATED_BY = "reprocess"


def reprocess_options(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The ``reprocess`` block of a task's metadata, or ``None`` on a first pass."""
    block = (metadata or {}).get("reprocess")
    return block if isinstance(block, dict) else None


def reprocess_requested_at(metadata: Optional[Dict[str, Any]]) -> Optional[datetime]:
    """When the reprocess run this task belongs to was requested, if it is one."""
    block = reprocess_options(metadata)
    if not block or not block.get("requested_at"):
        return None
    try:
        return datetime.fromisoformat(block["requested_at"])
    except (TypeError, ValueError):
        return None
