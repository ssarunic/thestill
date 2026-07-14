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

"""Language metadata for canonical and translated summary artefacts."""

from __future__ import annotations

import hashlib
from typing import Optional

from pydantic import BaseModel

from thestill.utils.file_storage import FileStorage
from thestill.utils.language_config import normalize_language_code

SUMMARY_MANIFEST_SCHEMA_VERSION = 1
TRANSLATION_METADATA_SCHEMA_VERSION = 1
TRANSLATOR_VERSION = 1


def content_sha256(content: str) -> str:
    """Return the stable content hash used to reject stale metadata."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def summary_manifest_key(summary_key: str) -> str:
    """Return the canonical-language manifest key for a summary."""

    return f"{summary_key[:-3]}.meta.json" if summary_key.endswith(".md") else f"{summary_key}.meta.json"


def translation_metadata_key(summary_key: str) -> str:
    """Return the provenance sidecar key for a translated summary."""

    suffix = ".translation.json"
    return f"{summary_key[:-3]}{suffix}" if summary_key.endswith(".md") else f"{summary_key}{suffix}"


class SummaryManifest(BaseModel):
    """Actual language and content identity of the canonical summary file."""

    schema_version: int = SUMMARY_MANIFEST_SCHEMA_VERSION
    canonical_language: str
    summary_sha256: str


class SummaryTranslationMetadata(BaseModel):
    """Provenance required before a language-suffixed sibling is trusted."""

    schema_version: int = TRANSLATION_METADATA_SCHEMA_VERSION
    translator_version: int = TRANSLATOR_VERSION
    source_language: str
    target_language: str
    source_sha256: str
    translation_sha256: str


def write_summary_manifest(
    file_storage: FileStorage,
    *,
    summary_key: str,
    summary_content: str,
    canonical_language: str,
) -> SummaryManifest:
    """Persist canonical summary language metadata after the summary write."""

    manifest = SummaryManifest(
        canonical_language=normalize_language_code(canonical_language),
        summary_sha256=content_sha256(summary_content),
    )
    file_storage.write_text(summary_manifest_key(summary_key), manifest.model_dump_json(indent=2))
    return manifest


def load_valid_summary_manifest(
    file_storage: FileStorage,
    *,
    summary_key: str,
    summary_content: str,
) -> Optional[SummaryManifest]:
    """Load a manifest only when its schema and content hash still match."""

    try:
        manifest = SummaryManifest.model_validate_json(file_storage.read_text(summary_manifest_key(summary_key)))
    except (FileNotFoundError, ValueError):
        return None
    if manifest.schema_version != SUMMARY_MANIFEST_SCHEMA_VERSION:
        return None
    if manifest.summary_sha256 != content_sha256(summary_content):
        return None
    manifest.canonical_language = normalize_language_code(manifest.canonical_language)
    return manifest


def write_translation_metadata(
    file_storage: FileStorage,
    *,
    summary_key: str,
    source_content: str,
    translated_content: str,
    source_language: str,
    target_language: str,
) -> SummaryTranslationMetadata:
    """Persist the source/target contract for a translated sibling."""

    metadata = SummaryTranslationMetadata(
        source_language=normalize_language_code(source_language),
        target_language=normalize_language_code(target_language),
        source_sha256=content_sha256(source_content),
        translation_sha256=content_sha256(translated_content),
    )
    file_storage.write_text(translation_metadata_key(summary_key), metadata.model_dump_json(indent=2))
    return metadata


def load_valid_translation_metadata(
    file_storage: FileStorage,
    *,
    summary_key: str,
    source_content: str,
    translated_content: str,
    source_language: str,
    target_language: str,
) -> Optional[SummaryTranslationMetadata]:
    """Reject legacy, mislabeled, or source-stale translation siblings."""

    try:
        metadata = SummaryTranslationMetadata.model_validate_json(
            file_storage.read_text(translation_metadata_key(summary_key))
        )
    except (FileNotFoundError, ValueError):
        return None
    if metadata.schema_version != TRANSLATION_METADATA_SCHEMA_VERSION:
        return None
    if metadata.translator_version != TRANSLATOR_VERSION:
        return None
    if normalize_language_code(metadata.source_language) != normalize_language_code(source_language):
        return None
    if normalize_language_code(metadata.target_language) != normalize_language_code(target_language):
        return None
    if metadata.source_sha256 != content_sha256(source_content):
        return None
    if metadata.translation_sha256 != content_sha256(translated_content):
        return None
    return metadata
