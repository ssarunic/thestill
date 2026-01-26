# Copyright 2025 thestill.me
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

"""
Webhook endpoints for receiving external service callbacks.

Currently supports:
- ElevenLabs Speech-to-Text webhooks

Security:
- HMAC signature verification (proves request came from ElevenLabs)
- Metadata validation (correlates callback to our transcription requests)

These endpoints receive transcription results from external services
when async transcription jobs complete.
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from structlog import get_logger

from ...webhook import get_tracker
from ..dependencies import AppState, get_app_state
from ..services import WebhookTranscriptProcessor

logger = get_logger(__name__)

router = APIRouter()


def _get_webhook_secret(state: AppState) -> str:
    """Get webhook secret from config, falling back to env var."""
    return state.config.elevenlabs_webhook_secret or os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")


def _get_require_metadata(state: AppState) -> bool:
    """Get require_metadata setting from config."""
    return state.config.elevenlabs_webhook_require_metadata


class ElevenLabsWebhookPayload(BaseModel):
    """ElevenLabs speech-to-text webhook payload."""

    # Transcript data
    language_code: Optional[str] = None
    language_probability: Optional[float] = None
    text: Optional[str] = None
    words: Optional[List[Dict[str, Any]]] = None
    transcription_id: Optional[str] = None

    # Metadata we sent with the request
    webhook_metadata: Optional[Dict[str, Any]] = None

    class Config:
        extra = "allow"


class WebhookResultSummary(BaseModel):
    """Summary of a webhook result for list endpoint."""

    transcription_id: str
    received_at: str
    has_text: bool
    language: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def _get_webhook_data_dir(state: AppState) -> Path:
    """Get the webhook data directory from app state."""
    return state.path_manager.storage_path / "webhook_data"


def _verify_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
) -> bool:
    """
    Verify ElevenLabs webhook signature using HMAC-SHA256.

    The ElevenLabs-Signature header format: t=<timestamp>,v1=<signature>

    Args:
        payload: Raw request body bytes
        signature_header: Value of ElevenLabs-Signature header
        secret: HMAC shared secret from ElevenLabs dashboard

    Returns:
        True if signature is valid, False otherwise
    """
    if not secret:
        logger.warning("No webhook secret configured, skipping signature verification")
        return True

    if not signature_header:
        logger.warning("No signature header present")
        return False

    try:
        # Parse signature header: t=<timestamp>,v<N>=<signature>
        # ElevenLabs uses versioned signatures (v0, v1, etc.) - we detect which version is present
        logger.debug(f"Signature header received: {signature_header}")
        parts = dict(part.split("=", 1) for part in signature_header.split(","))
        timestamp = parts.get("t", "")

        # Find the signature version dynamically - check for v0, v1, v2, etc.
        received_signature = None
        signature_version = None
        for version in range(10):  # Support v0 through v9
            version_key = f"v{version}"
            if version_key in parts:
                received_signature = parts[version_key]
                signature_version = version_key
                logger.debug(f"Found signature version: {signature_version}")
                break

        if not timestamp or not received_signature:
            logger.warning(f"Invalid signature header format: {signature_header}")
            logger.warning(f"Parsed parts: {parts}")
            return False

        # Compute expected signature: HMAC-SHA256(timestamp.payload)
        signed_payload = f"{timestamp}.".encode() + payload
        expected_signature = hmac.new(
            secret.encode(),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()

        # Compare signatures (constant-time comparison)
        return hmac.compare_digest(expected_signature, received_signature)

    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


def _save_webhook_result(webhook_dir: Path, transcription_id: str, data: Dict[str, Any]) -> Path:
    """
    Save webhook result to disk for later processing.

    Args:
        webhook_dir: Directory to save webhook data
        transcription_id: ElevenLabs transcription ID
        data: Full webhook payload

    Returns:
        Path to saved file
    """
    webhook_dir.mkdir(parents=True, exist_ok=True)

    # Add received timestamp
    data["_webhook_received_at"] = datetime.now(timezone.utc).isoformat()

    file_path = webhook_dir / f"elevenlabs_{transcription_id}.json"

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved webhook result: {file_path}")
    return file_path


@router.post("/elevenlabs/speech-to-text")
async def elevenlabs_webhook(
    request: Request,
    state: AppState = Depends(get_app_state),
    elevenlabs_signature: Optional[str] = Header(None, alias="ElevenLabs-Signature"),
):
    """
    Receive ElevenLabs speech-to-text webhook callback.

    ElevenLabs sends a POST request with the transcription results
    when an async transcription job completes.

    Security layers:
    1. HMAC signature verification (if ELEVENLABS_WEBHOOK_SECRET is set)
       - Proves the request actually came from ElevenLabs
    2. Metadata validation (if ELEVENLABS_WEBHOOK_REQUIRE_METADATA is true)
       - Ensures the webhook belongs to our transcription request
       - Prevents processing webhooks from other apps sharing the same account

    Args:
        request: FastAPI request object
        state: Application state with services
        elevenlabs_signature: ElevenLabs signature header for verification

    Returns:
        Confirmation of receipt with transcription ID and episode info

    Raises:
        HTTPException: If signature is invalid, payload is malformed, or metadata is missing
    """
    # Get raw body for signature verification
    body = await request.body()

    # Layer 1: Verify HMAC signature (proves it's from ElevenLabs)
    webhook_secret = _get_webhook_secret(state)
    if webhook_secret:
        logger.info(f"Signature verification enabled (secret length: {len(webhook_secret)})")
        logger.info(f"Received signature header: {elevenlabs_signature}")
        if not _verify_signature(body, elevenlabs_signature or "", webhook_secret):
            logger.error("Invalid webhook signature - request rejected")
            raise HTTPException(status_code=401, detail="Invalid signature")
        logger.info("Signature verification passed")
    else:
        logger.warning("No ELEVENLABS_WEBHOOK_SECRET configured - signature verification skipped")

    # Parse payload
    try:
        raw_payload = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    # Log payload structure for debugging
    logger.info(f"Webhook payload keys: {list(raw_payload.keys())}")

    # ElevenLabs wraps the transcript in: {"type": "...", "event_timestamp": ..., "data": {...}}
    # Extract the actual data from the wrapper
    if "data" in raw_payload and isinstance(raw_payload.get("data"), dict):
        event_type = raw_payload.get("type", "unknown")
        logger.info(f"Webhook event type: {event_type}")
        data = raw_payload["data"]
        logger.info(f"Extracted data keys: {list(data.keys())}")
    else:
        # Fallback for direct payload (no wrapper)
        data = raw_payload

    # Extract transcription_id - ElevenLabs may use different field names
    # Try common variations: transcription_id, request_id, id
    transcription_id = data.get("transcription_id") or data.get("request_id") or data.get("id")
    if not transcription_id:
        logger.error(f"No transcription_id found in webhook payload. Available keys: {list(data.keys())}")
        raise HTTPException(status_code=400, detail="Missing transcription_id")

    logger.info(f"Received webhook for transcription: {transcription_id}")

    # Layer 2: Validate metadata (correlates to our request)
    metadata = data.get("webhook_metadata")
    episode_id = None
    podcast_slug = None
    episode_slug = None

    if metadata:
        episode_id = metadata.get("episode_id")
        podcast_slug = metadata.get("podcast_slug")
        episode_slug = metadata.get("episode_slug")
        logger.info(f"Metadata: episode_id={episode_id}, podcast={podcast_slug}, episode={episode_slug}")

    if _get_require_metadata(state):
        if not metadata or not episode_id:
            logger.warning(f"Webhook {transcription_id} rejected: missing required metadata (episode_id)")
            raise HTTPException(
                status_code=400,
                detail="Missing webhook_metadata with episode_id. This webhook may be from another application.",
            )

        # Optionally verify episode exists in our database
        # get_episode returns (Podcast, Episode) tuple or None
        episode_result = state.repository.get_episode(episode_id)
        if not episode_result:
            logger.warning(f"Webhook {transcription_id} rejected: episode_id {episode_id} not found in database")
            raise HTTPException(
                status_code=404,
                detail=f"Episode {episode_id} not found. This webhook may be from another application.",
            )

        _, episode = episode_result
        logger.info(f"Webhook validated for episode: {episode.title}")

    # Save to disk for debugging/backup purposes
    webhook_dir = _get_webhook_data_dir(state)
    saved_path = _save_webhook_result(webhook_dir, transcription_id, data)

    # Process transcript and save to raw_transcripts directory
    transcript_path = None
    processing_error = None

    # ElevenLabs nests transcript data inside "transcription" key
    # Structure: {"request_id": ..., "transcription": {"text": ..., "words": ...}, "webhook_metadata": ...}
    transcription_data = data.get("transcription", {})
    has_text = bool(transcription_data.get("text") if isinstance(transcription_data, dict) else data.get("text"))

    if episode_id and has_text:
        try:
            processor = WebhookTranscriptProcessor(
                path_manager=state.path_manager,
                repository=state.repository,
            )
            transcript = processor.process_elevenlabs_webhook(data)
            if transcript:
                # Get the episode to report the path
                # get_episode returns (Podcast, Episode) tuple or None
                episode_result = state.repository.get_episode(episode_id)
                if episode_result:
                    _, episode = episode_result
                    if episode.raw_transcript_path:
                        transcript_path = episode.raw_transcript_path
                        logger.info(f"Transcript processed and saved: {transcript_path}")
        except Exception as e:
            processing_error = str(e)
            logger.error(f"Error processing webhook transcript: {e}")
    elif not has_text:
        logger.warning(f"Webhook {transcription_id} has no transcript text, skipping processing")

    # Notify the webhook tracker that this episode's transcription is complete
    # This allows the CLI to know when all pending transcriptions are done
    # Note: We use episode_id (not transcription_id) because ElevenLabs returns
    # a different request_id in the webhook callback than the transcription_id
    # from the submission response. episode_id is consistent in both.
    if episode_id:
        tracker = get_tracker()
        success = transcript_path is not None and processing_error is None
        tracker.mark_completed(episode_id, success=success)

    # Return 200 OK immediately (required by ElevenLabs)
    response = {
        "status": "received",
        "transcription_id": transcription_id,
        "episode_id": episode_id,
        "saved_to": str(saved_path.name) if saved_path else None,
    }

    if transcript_path:
        response["transcript_path"] = transcript_path
        response["status"] = "processed"

    if processing_error:
        response["processing_error"] = processing_error

    return response


@router.get("/elevenlabs/results")
async def list_webhook_results(state: AppState = Depends(get_app_state)):
    """
    List all received webhook results.

    Args:
        state: Application state with services

    Returns:
        List of webhook result summaries with count
    """
    webhook_dir = _get_webhook_data_dir(state)

    if not webhook_dir.exists():
        return {"results": [], "count": 0}

    results: List[Dict[str, Any]] = []
    for file_path in webhook_dir.glob("elevenlabs_*.json"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append(
                {
                    "transcription_id": data.get("transcription_id"),
                    "received_at": data.get("_webhook_received_at"),
                    "has_text": bool(data.get("text")),
                    "language": data.get("language_code"),
                    "metadata": data.get("webhook_metadata"),
                }
            )
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")

    return {"results": results, "count": len(results)}


@router.get("/elevenlabs/results/{transcription_id}")
async def get_webhook_result(transcription_id: str, state: AppState = Depends(get_app_state)):
    """
    Get a specific webhook result by transcription ID.

    Args:
        transcription_id: ElevenLabs transcription ID
        state: Application state with services

    Returns:
        Full webhook payload data

    Raises:
        HTTPException: If result not found
    """
    webhook_dir = _get_webhook_data_dir(state)
    file_path = webhook_dir / f"elevenlabs_{transcription_id}.json"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Result not found")

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


@router.delete("/elevenlabs/results/{transcription_id}")
async def delete_webhook_result(transcription_id: str, state: AppState = Depends(get_app_state)):
    """
    Delete a webhook result by transcription ID.

    Args:
        transcription_id: ElevenLabs transcription ID
        state: Application state with services

    Returns:
        Confirmation of deletion

    Raises:
        HTTPException: If result not found
    """
    webhook_dir = _get_webhook_data_dir(state)
    file_path = webhook_dir / f"elevenlabs_{transcription_id}.json"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Result not found")

    file_path.unlink()
    logger.info(f"Deleted webhook result: {transcription_id}")

    return {"status": "deleted", "transcription_id": transcription_id}
