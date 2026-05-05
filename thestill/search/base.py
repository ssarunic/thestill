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

"""Spec #28 §2.10.1 — search-backend abstraction.

The ``SearchBackend`` Protocol is the seam at which a future
``PgVectorBackend`` would land if/when the multi-tenant hosted story
forces a Postgres move. Today there is exactly one implementation
(``SqliteVecBackend``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Protocol, Tuple

from ..models.entities import MatchType


class SearchMode(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


# Embedding-model → vector dimension. The ``chunks_vec`` virtual table
# is sized at migration time from this map; changing a model to one
# with a different dimension requires re-running the chunks migration
# against an empty ``chunks`` table. Add new entries here as models
# get adopted; the migration and the embedding wrapper both read it.
EMBEDDING_MODEL_DIMS: dict[str, int] = {
    # Multilingual default — supports 50+ languages (en, hr, sr, bs,
    # sl, mk, de, fr, es, it, pl, ru, ja, ko, zh, ar, etc). Distilled
    # from xlm-roberta-base.
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    # English-only alternative; ~5-10% better English recall on
    # benchmarks. Set ``EMBEDDING_MODEL`` to this for English-only
    # corpora that don't need multilingual coverage.
    "BAAI/bge-small-en-v1.5": 384,
}

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def embedding_dim_for(model: str) -> int:
    """Return the vector dimension for an embedding model name.

    Raises ``KeyError`` with a clear message if the model is unknown —
    callers should add it to ``EMBEDDING_MODEL_DIMS`` rather than
    silently default to a wrong dimension.
    """
    try:
        return EMBEDDING_MODEL_DIMS[model]
    except KeyError as exc:
        raise KeyError(
            f"Unknown embedding model {model!r}. Add it to " f"EMBEDDING_MODEL_DIMS in thestill/search/base.py."
        ) from exc


@dataclass(frozen=True)
class SearchFilters:
    """Pushed-down WHERE-clause filters for ``SearchBackend.search``.

    All fields optional. ``has_entity`` is AND-logic across entity
    ids — a chunk must come from an episode whose ``entity_mentions``
    cover *every* listed entity. Frozen + tuple to prevent shared-
    instance aliasing across callers.
    """

    podcast_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    has_entity: Tuple[str, ...] = field(default_factory=tuple)
    # Spec #28 §O2 — case-insensitive substring filter on
    # ``chunks.speaker`` (diarised label). Populated by the query
    # translator when the input contains ``speaker:foo``; not exposed
    # on the wire as its own param because the typing UX always goes
    # via the operator syntax.
    speaker: Optional[str] = None


@dataclass(frozen=True)
class ResolvedHit:
    """One backend search result, fully hydrated for citation rendering.

    ``score`` semantics depend on ``match_type``:

    - ``LEXICAL`` — ``-bm25(...)`` so higher is better (FTS5's bm25
      returns a negative ranking).
    - ``SEMANTIC`` — cosine distance, lower is better.
    - ``HYBRID`` — reciprocal-rank-fusion score, higher is better.

    Don't compare scores across match types.
    """

    episode_id: str
    podcast_id: str
    podcast_title: str
    episode_title: str
    published_at: Optional[datetime]
    segment_id: int
    start_ms: int
    end_ms: int
    speaker: Optional[str]
    text: str
    score: float
    match_type: MatchType

    def as_citation(self, *, quote_max: int = 600) -> dict:
        """Serialize to the citation-shaped wire dict used by MCP + REST.

        Single source of truth for the deeplink/web_url/quote shape so
        MCP and REST callers can't drift.
        """
        seconds = self.start_ms // 1000
        return {
            "episode_id": self.episode_id,
            "podcast_id": self.podcast_id,
            "podcast_title": self.podcast_title,
            "episode_title": self.episode_title,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "speaker": self.speaker,
            "quote": self.text[:quote_max],
            "score": self.score,
            "match_type": self.match_type.value,
            "deeplink": f"thestill://episode/{self.episode_id}?t={seconds}",
            "web_url": f"/episodes/{self.episode_id}?t={seconds}",
        }


class SearchBackend(Protocol):
    """Contract implemented by every concrete corpus search backend.

    Implementations MUST:

    - Return at most ``limit`` hits, ranked best-first per the
      ``match_type`` semantics above.
    - Push ``filters`` down to the storage layer (no fetch-then-filter
      in Python).
    - Be safe to call from multiple threads; per-call state should
      live on the connection, not the instance.
    """

    def search(
        self,
        query: str,
        *,
        mode: SearchMode,
        limit: int,
        filters: Optional[SearchFilters],
    ) -> List[ResolvedHit]: ...
