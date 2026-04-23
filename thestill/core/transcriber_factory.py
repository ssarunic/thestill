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

"""
Single factory for transcriber construction shared by the CLI, the web
worker, and the MCP server.

Keeping construction in one place means a new provider is wired up
once, not three times. Entry points layer their own UX (CLI echoes,
webhook-server lifecycle, MCP JSON responses) on top of this factory.
"""

from typing import Optional

from structlog import get_logger

from thestill.utils.exceptions import ThestillError

from ..utils.console import ConsoleOutput
from .progress import ProgressCallback
from .transcriber import Transcriber

logger = get_logger(__name__)


def validate_transcription_provider(config) -> None:
    """
    Fail fast if the configured transcription provider cannot be used.

    This check runs at startup so misconfigured deployments surface the
    problem before any episode is processed. It verifies two things per
    provider:

    1. The Python module(s) backing the provider are importable. This
       catches the slim-Docker-image case where `TRANSCRIPTION_PROVIDER`
       points at a local-transcription provider (whisper / whisperx /
       parakeet) but the `local-transcription` optional-dependencies
       extra is not installed.
    2. Required runtime config is present. This catches the "module is
       installed but the user forgot to set DALSTON_BASE_URL /
       ELEVENLABS_API_KEY / GOOGLE_APP_CREDENTIALS" case that otherwise
       only surfaces at first-transcribe.

    The check uses importlib.util.find_spec rather than a real import
    so it is cheap, has no side effects, and does not defeat the lazy
    import pattern used throughout core/. It never connects to external
    services.

    Args:
        config: Application configuration (thestill.utils.config.Config).

    Raises:
        ThestillError: With a specific remediation message when the
            configured provider cannot be used.
    """
    import importlib.util

    provider = (config.transcription_provider or "").lower()

    def _require_modules(modules: list[str], extra: str) -> None:
        missing = [m for m in modules if importlib.util.find_spec(m) is None]
        if missing:
            raise ThestillError(
                f"TRANSCRIPTION_PROVIDER={provider!r} requires {', '.join(missing)} "
                f"which is not installed. Install with: pip install '.[{extra}]' "
                f"— or switch TRANSCRIPTION_PROVIDER to a cloud provider "
                f"(dalston, google, elevenlabs).",
                provider=provider,
                missing_modules=missing,
                remediation_extra=extra,
            )

    def _require_config(field: str, env_var: str, provider_label: str) -> None:
        if not getattr(config, field, None):
            raise ThestillError(
                f"TRANSCRIPTION_PROVIDER={provider!r} requires {env_var} to be set. "
                f"Add {env_var}=... to your .env file.",
                provider=provider,
                missing_config=env_var,
                provider_label=provider_label,
            )

    if provider == "dalston":
        _require_config("dalston_base_url", "DALSTON_BASE_URL", "Dalston")
    elif provider == "google":
        _require_config("google_app_credentials", "GOOGLE_APP_CREDENTIALS", "Google Cloud Speech")
        _require_config("google_cloud_project_id", "GOOGLE_CLOUD_PROJECT_ID", "Google Cloud Speech")
    elif provider == "elevenlabs":
        _require_config("elevenlabs_api_key", "ELEVENLABS_API_KEY", "ElevenLabs")
    elif provider == "parakeet":
        _require_modules(["torch", "transformers", "librosa"], "local-transcription")
    elif provider in ("whisper", "whisperx", ""):
        # Empty string is the fallback branch in create_transcriber, which
        # uses WhisperTranscriber / WhisperXTranscriber.
        required = ["torch", "whisper"]
        if config.enable_diarization:
            required.append("whisperx")
        _require_modules(required, "local-transcription")
    else:
        raise ThestillError(
            f"Unknown TRANSCRIPTION_PROVIDER={provider!r}. "
            f"Valid values: dalston, google, elevenlabs, whisper, whisperx, parakeet.",
            provider=provider,
        )

    logger.info(
        "transcription_provider_validated",
        provider=provider,
        diarization=config.enable_diarization,
    )


def create_transcriber(
    config,
    path_manager=None,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    console: Optional[ConsoleOutput] = None,
    elevenlabs_use_async: bool = False,
    elevenlabs_async_threshold_mb: Optional[int] = None,
    elevenlabs_tag_audio_events: bool = False,
    elevenlabs_wait_for_webhook: bool = False,
) -> Transcriber:
    """
    Build a transcriber from config. Used by CLI, web worker, and MCP.

    Defaults favor the non-interactive callers (web, MCP). The CLI
    passes its own console and the ElevenLabs flags it needs for
    webhook-based async mode.

    Args:
        config: Application configuration.
        path_manager: PathManager for providers that persist pending
            operations (Dalston, ElevenLabs, Google chunked mode).
            Falls back to config.path_manager when available.
        progress_callback: Optional callback for stage-level progress.
            Only WhisperX currently forwards this.
        console: ConsoleOutput instance. Defaults to quiet (suitable
            for web workers and MCP, whose stdout is reserved for
            JSON-RPC or structured logs).
        elevenlabs_use_async: ElevenLabs async polling/webhook mode.
            Web worker uses sync; CLI uses async.
        elevenlabs_async_threshold_mb: ElevenLabs file-size threshold
            for switching to async (0 = always async when use_async).
        elevenlabs_tag_audio_events: ElevenLabs audio-event tagging
            (laughter, applause, etc.). CLI enables this.
        elevenlabs_wait_for_webhook: When true, ElevenLabs submits and
            returns None instead of polling — the webhook handler saves
            the transcript. Requires a running webhook server.

    Returns:
        A fully constructed Transcriber.

    Raises:
        ThestillError: When the configured provider is misconfigured.
        ImportError: When the provider's optional dependency is missing.
    """
    validate_transcription_provider(config)

    provider = config.transcription_provider.lower()
    effective_path_manager = path_manager or getattr(config, "path_manager", None)
    effective_console = console or ConsoleOutput(quiet=True)

    if provider == "google":
        from .google_transcriber import GoogleCloudTranscriber

        return GoogleCloudTranscriber(
            credentials_path=config.google_app_credentials or None,
            project_id=config.google_cloud_project_id or None,
            storage_bucket=config.google_storage_bucket or None,
            enable_diarization=config.enable_diarization,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            parallel_chunks=config.max_workers,
            path_manager=effective_path_manager,
            console=effective_console,
        )

    if provider == "elevenlabs":
        from .elevenlabs_transcriber import ElevenLabsTranscriber

        kwargs = dict(
            api_key=config.elevenlabs_api_key,
            base_url=config.elevenlabs_base_url or None,
            model=config.elevenlabs_model,
            enable_diarization=config.enable_diarization,
            num_speakers=config.max_speakers,
            path_manager=effective_path_manager,
            use_async=elevenlabs_use_async,
            tag_audio_events=elevenlabs_tag_audio_events,
            wait_for_webhook=elevenlabs_wait_for_webhook,
        )
        if elevenlabs_async_threshold_mb is not None:
            kwargs["async_threshold_mb"] = elevenlabs_async_threshold_mb
        return ElevenLabsTranscriber(**kwargs)

    if provider == "dalston":
        from .dalston_transcriber import DalstonTranscriber

        return DalstonTranscriber(
            base_url=config.dalston_base_url or None,
            api_key=config.dalston_api_key or None,
            model=config.dalston_model or None,
            enable_diarization=config.enable_diarization,
            num_speakers=config.max_speakers,
            path_manager=effective_path_manager,
        )

    if provider == "parakeet":
        from .parakeet_transcriber import ParakeetTranscriber

        return ParakeetTranscriber(config.whisper_device, console=effective_console)

    # Local Whisper / WhisperX (also the empty-string fallback).
    if config.enable_diarization:
        from .whisper_transcriber import WhisperXTranscriber

        return WhisperXTranscriber(
            model_name=config.whisper_model,
            device=config.whisper_device,
            enable_diarization=True,
            hf_token=config.huggingface_token,
            min_speakers=config.min_speakers,
            max_speakers=config.max_speakers,
            diarization_model=config.diarization_model,
            progress_callback=progress_callback,
            console=effective_console,
        )

    from .whisper_transcriber import WhisperTranscriber

    return WhisperTranscriber(
        config.whisper_model,
        config.whisper_device,
        console=effective_console,
    )
