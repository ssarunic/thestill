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

"""Builds the configured linker (spec #81). The worker and the CLI both
come through here, so ``ENTITY_LINKER`` means the same thing in both."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...repositories.link_decision_repository import LinkDecisionRepository
from ..llm_provider import LLMProvider, create_llm_provider, provider_kwargs_from_config
from ..wikidata_client import WikidataClient
from ..wikipedia_client import WikipediaClient
from .cache import LinkDecisionCache
from .candidates import WikidataCandidateSource
from .chooser import LLMCandidateChooser
from .live_linker import LiveWikidataLinker
from .protocol import EntityLinker
from .rate_limiter import get_shared_rate_limiter

if TYPE_CHECKING:
    from ...utils.config import Config


def create_linking_provider(config: "Config") -> LLMProvider:
    """``ENTITY_LINKING_PROVIDER``/``_MODEL``, else the cleaning pair.

    Provider and model are a coupled pair, as for the eval judge: a model id
    only applies to the provider family it was written for.
    """
    provider_name = (config.entity_linking_provider or config.cleaning_provider).lower()
    if config.entity_linking_model:
        model_name = config.entity_linking_model
    elif provider_name == config.cleaning_provider.lower():
        model_name = config.cleaning_model
    else:
        model_name = ""
    kwargs = provider_kwargs_from_config(config)
    if model_name:
        kwargs[f"{provider_name}_model"] = model_name
    return create_llm_provider(provider_type=provider_name, **kwargs)


def linker_is_available(config: "Config") -> bool:
    """Whether ``resolve-entities`` can run here. The live linker has nothing
    to install; ReFinED needs the ``entities`` extra."""
    if config.entity_linker == "live":
        return True
    from ..entity_resolver import EntityResolver

    return EntityResolver.is_available()


def build_linker(
    config: "Config",
    link_decisions: LinkDecisionRepository,
    *,
    wikidata_client: Optional[WikidataClient] = None,
    provider: Optional[LLMProvider] = None,
) -> EntityLinker:
    wikidata_client = wikidata_client or WikidataClient()
    if config.entity_linker != "live":
        from ..entity_resolver import EntityResolver

        return EntityResolver(wikidata_client=wikidata_client)
    chooser = LLMCandidateChooser(provider or create_linking_provider(config))
    limiter = get_shared_rate_limiter(config.wikidata_max_rps)
    return LiveWikidataLinker(
        candidate_source=WikidataCandidateSource(wikidata_client, limiter, wikipedia=WikipediaClient()),
        chooser=chooser,
        cache=LinkDecisionCache(
            link_decisions,
            linker_version=chooser.version,
            none_ttl_days=config.entity_linking_none_ttl_days,
        ),
        wikidata_client=wikidata_client,
        min_confidence=config.entity_linking_min_confidence,
        entity_lookup=lambda qid, language: (limiter.acquire(), wikidata_client.lookup_entity(qid, language=language))[
            1
        ],
    )
