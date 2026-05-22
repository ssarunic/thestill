# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Regression tests for ``_transcript_to_json``.

Dalston stores the SDK ``job.id`` (a ``uuid.UUID``) in
``Transcript.provider_metadata``. Because ``provider_metadata`` is typed
``Dict[str, Any]``, Pydantic keeps the value as-is, so a naive
``json.dumps(transcript.model_dump())`` raised "Object of type UUID is not
JSON serializable" *after* a transcription had already succeeded — failing
the transcribe stage and triggering an endless re-transcribe loop. These
tests lock in that the persisted JSON encoder coerces such values to strings.
"""

import json
import uuid

from thestill.core.task_handlers import _transcript_to_json
from thestill.models.transcript import Transcript


def _make_transcript(provider_metadata: dict) -> Transcript:
    return Transcript(
        audio_file="better-offline/episode",
        language="en",
        text="hello world",
        segments=[],
        processing_time=1.23,
        model_used="dalston",
        timestamp=1234567890.0,
        provider_metadata=provider_metadata,
    )


def test_serializes_uuid_job_id_in_provider_metadata():
    """A UUID in provider_metadata (as Dalston supplies) must not break encoding."""
    job_id = uuid.uuid4()
    transcript = _make_transcript({"provider": "dalston", "job_id": job_id})

    raw = _transcript_to_json(transcript)  # must not raise
    parsed = json.loads(raw)

    # UUID is coerced to its canonical string form, not dropped.
    assert parsed["provider_metadata"]["job_id"] == str(job_id)


def test_naive_model_dump_would_have_raised():
    """Guards the root cause: plain model_dump() leaves a raw UUID that json.dumps rejects."""
    transcript = _make_transcript({"job_id": uuid.uuid4()})

    # Demonstrates the original bug surface so the fix can't silently regress.
    try:
        json.dumps(transcript.model_dump())
    except TypeError as exc:
        assert "UUID" in str(exc)
    else:  # pragma: no cover - only hit if pydantic changes default dump behaviour
        raise AssertionError("expected raw model_dump() to fail UUID serialization")
