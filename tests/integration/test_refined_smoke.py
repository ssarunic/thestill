"""ReFinED actually runs with the installed dependency set.

The unit tests stub ReFinED, so nothing there notices when a dependency
upgrade breaks the real model: on 2026-09-19 the lockfile resolved
transformers 5.x for the ``entities`` extra, ReFinED's ``encode_plus`` call
stopped existing, and every mention was written off as unresolvable.

Skipped where the ``entities`` extra is absent (CI, the prod image) or the
model data has never been downloaded (~several GB into ``~/.cache/refined``).
Run it before widening the transformers constraint:

    ./venv/bin/python -m pytest tests/integration/test_refined_smoke.py -p no:cacheprovider
"""

from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("refined") is None or not os.path.isdir(os.path.expanduser("~/.cache/refined")),
    reason="needs the `entities` extra and a downloaded ReFinED model",
)


def test_transformers_is_a_version_refined_can_use():
    import importlib.metadata as metadata

    major = int(metadata.version("transformers").split(".")[0])
    assert major < 5, "transformers 5 removed tokenizer.encode_plus, which ReFinED calls on every text"


def test_resolver_links_an_obvious_entity_end_to_end():
    from thestill.core.entity_resolver import EntityResolver
    from thestill.models.entities import EntityMention

    mention = EntityMention(
        id=1,
        episode_id="00000000-0000-4000-8000-000000000000",
        segment_id=1,
        start_ms=0,
        end_ms=1000,
        surface_form="Elon Musk",
        surface_label="person",
        quote_excerpt="Elon Musk said Tesla will expand its factory in Texas next year.",
        confidence=0.9,
        extractor="gliner",
    )
    [result] = EntityResolver().resolve([mention])
    assert result.status == "resolved"
    assert result.entity.wikidata_qid == "Q317521"
