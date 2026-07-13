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
NVIDIA Parakeet (TDT) transcriber for speech-to-text.

Loaded via NeMo's ``nemo.collections.asr`` so the model returns native
segment- and word-level timestamps. The previous implementation went
through HF ``transformers`` (`AutoModelForSpeechSeq2Seq` + greedy
``generate``) which discarded TDT's token-duration outputs and emitted
a single ``(start=0, end=0)`` stub segment — see spec #18.
"""

import time
from pathlib import Path
from typing import Iterable, List, Optional

from thestill.models.transcript import Segment, Transcript, Word
from thestill.models.transcription import TranscribeOptions
from thestill.utils.console import ConsoleOutput

from .transcriber import Transcriber


class ParakeetTranscriber(Transcriber):
    """
    Transcriber using NVIDIA's Parakeet TDT model via NeMo.

    Returns word- and segment-level timestamps natively, so cleanup can
    take the segment-preserving path (spec #18) without a forced-alignment
    fallback. The v3 model is multilingual across ~25 European languages
    (no Icelandic/Norwegian/Irish — see spec #57) and auto-detects the
    spoken language; there is no language-selection input. Custom prompt
    support is not provided by the model and is silently ignored.
    """

    DEFAULT_MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v3"

    def __init__(
        self,
        device: str = "auto",
        console: Optional[ConsoleOutput] = None,
        model_name: Optional[str] = None,
    ):
        self.model_name = model_name or self.DEFAULT_MODEL_NAME
        self.device = self._resolve_device(device)
        self.console = console or ConsoleOutput()
        self._model = None

    def load_model(self) -> None:
        if self._model is not None:
            return

        try:
            import nemo.collections.asr as nemo_asr  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise ImportError(
                "Parakeet requires the NeMo ASR toolkit. "
                'Install with: pip install -e ".[local-transcription]" '
                "(or pip install nemo_toolkit[asr])"
            ) from exc

        self.console.info(f"Loading Parakeet model: {self.model_name}")
        try:
            model = nemo_asr.models.ASRModel.from_pretrained(model_name=self.model_name)
            model = model.to(self.device)
            model.eval()
            self._model = model
            self.console.success("Model loaded successfully")
        except Exception as exc:
            self.console.error(f"Error loading Parakeet model: {exc}")
            raise

    def transcribe_audio(
        self,
        audio_path: str,
        output_path: Optional[str] = None,
        *,
        options: TranscribeOptions,
    ) -> Optional[Transcript]:
        # Parakeet TDT v3 supports ~25 European languages and decodes
        # whatever it hears — there is no language-selection input on
        # the model itself, so we record the caller-supplied language
        # tag for downstream consumers (cleaning, summarisation) and
        # leave actual decoding to the model.
        try:
            self.load_model()

            self.console.info(f"Starting transcription of: {Path(audio_path).name}")
            start_time = time.time()

            audio_duration = self._get_audio_duration_minutes(audio_path)
            self.console.info(f"Audio duration: {audio_duration:.1f} minutes")

            # NeMo accepts paths directly and handles long-form audio
            # internally for the v3 model (24-min context). The toolkit
            # decodes audio itself, so we don't pre-load via librosa.
            results = self._model.transcribe(
                [audio_path],
                timestamps=True,
            )
            if not results:
                self.console.error("Parakeet returned no hypotheses")
                return None

            hypothesis = results[0]
            processing_time = time.time() - start_time
            self.console.success(f"Transcription completed in {processing_time:.1f} seconds")

            transcript = self._format_transcript(
                hypothesis,
                processing_time,
                audio_path,
                language=options.language or "en",
            )

            if output_path:
                self._save_transcript(transcript, output_path)

            return transcript

        except Exception as exc:
            self.console.error(f"Error transcribing {audio_path}: {exc}")
            return None

    def _format_transcript(
        self,
        hypothesis,
        processing_time: float,
        audio_path: str,
        language: str = "en",
    ) -> Transcript:
        """
        Map a NeMo Hypothesis (or dict) into thestill's Transcript shape.

        Newer NeMo versions return a Hypothesis object with ``.text`` and
        ``.timestamp`` fields. Older versions returned a plain dict. We
        accept either; if timestamps are missing we degrade to a single
        zero-duration stub so cleanup falls back to the legacy path
        rather than crashing.

        ``language`` is the caller-supplied language tag stored on the
        Transcript. If the hypothesis itself carries a detected language
        (some NeMo builds expose ``langs``/``language`` on the
        hypothesis), prefer that.
        """
        text = _extract_text(hypothesis)
        timestamps = _extract_timestamps(hypothesis)
        detected_language = _extract_language(hypothesis)
        effective_language = detected_language or language or "en"

        word_entries = list(timestamps.get("word", []) or [])
        segment_entries = list(timestamps.get("segment", []) or [])

        words_by_segment: List[List[Word]] = []
        if segment_entries and word_entries:
            words_by_segment = _bucket_words_into_segments(word_entries, segment_entries)
        elif segment_entries:
            words_by_segment = [[] for _ in segment_entries]

        segments: List[Segment] = []
        if segment_entries:
            for idx, raw_seg in enumerate(segment_entries):
                seg_text = _str_field(raw_seg, ("segment", "text"))
                seg_start = _float_field(raw_seg, ("start", "start_time", "start_offset"))
                seg_end = _float_field(raw_seg, ("end", "end_time", "end_offset"))
                seg_words = words_by_segment[idx] if idx < len(words_by_segment) else []
                segments.append(
                    Segment(
                        id=idx,
                        start=seg_start if seg_start is not None else 0.0,
                        end=seg_end if seg_end is not None else (seg_start or 0.0),
                        text=(seg_text or "").strip(),
                        words=seg_words,
                    )
                )
        else:
            # No timestamps in the hypothesis — keep behaviour explicit so
            # ``has_usable_segment_structure`` correctly routes to legacy.
            self.console.warning(
                "Parakeet hypothesis lacked timestamps; emitting stub segment "
                "(cleanup will use the legacy path for this episode)"
            )
            segments = [Segment(id=0, start=0.0, end=0.0, text=text, words=[])]

        return Transcript(
            audio_file=audio_path,
            language=effective_language,
            text=text,
            segments=segments,
            processing_time=processing_time,
            model_used=self.model_name,
            timestamp=time.time(),
        )

    def estimate_processing_time(self, audio_duration_minutes: float) -> float:
        if self.device == "cuda":
            ratio = 0.08
        else:
            ratio = 0.2
        return audio_duration_minutes * ratio

    def generate_prompt_from_podcast_info(self, podcast_title: str, episode_title: str = "") -> str:
        # Parakeet has no biasing/prompt input. Kept for interface parity.
        del podcast_title, episode_title
        return ""


# ---------------------------------------------------------------------------
# Hypothesis-shape helpers
#
# NeMo has changed the hypothesis surface twice; supporting both keeps the
# transcriber working across the version range pinned in extras without
# wedging on a single tuple of internals.
# ---------------------------------------------------------------------------


def _extract_text(hypothesis) -> str:
    if hypothesis is None:
        return ""
    if isinstance(hypothesis, str):
        return hypothesis.strip()
    text = getattr(hypothesis, "text", None)
    if text is None and isinstance(hypothesis, dict):
        text = hypothesis.get("text")
    return (text or "").strip()


def _extract_timestamps(hypothesis) -> dict:
    ts = getattr(hypothesis, "timestamp", None)
    if ts is None and isinstance(hypothesis, dict):
        ts = hypothesis.get("timestamp") or hypothesis.get("timestamps")
    return ts or {}


def _extract_language(hypothesis) -> Optional[str]:
    """Return a hypothesis-detected language code, or None if absent.

    Some NeMo builds attach the model's source-language tag to each
    hypothesis (``.langs`` / ``.language``). When present it's the most
    accurate value; otherwise fall back to the caller-supplied tag.
    """
    for attr in ("langs", "language", "lang"):
        value = getattr(hypothesis, attr, None)
        if value is None and isinstance(hypothesis, dict):
            value = hypothesis.get(attr)
        if value:
            if isinstance(value, (list, tuple)) and value:
                value = value[0]
            return str(value).strip() or None
    return None


def _str_field(item, keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        if isinstance(item, dict) and key in item:
            return item[key]
        value = getattr(item, key, None)
        if value is not None:
            return value
    return None


def _float_field(item, keys: Iterable[str]) -> Optional[float]:
    raw = _str_field(item, keys)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _bucket_words_into_segments(word_entries: list, segment_entries: list) -> List[List[Word]]:
    """
    Assign each word entry to its enclosing segment by start time.

    NeMo emits parallel `word` and `segment` lists with absolute times;
    we group words into the segment whose [start, end] window contains
    them. Falls back to nearest-segment if a word sits exactly on the
    boundary or floats outside any window (rounding edge cases).
    """
    seg_windows: List[tuple] = []
    for raw_seg in segment_entries:
        s = _float_field(raw_seg, ("start", "start_time", "start_offset")) or 0.0
        e = _float_field(raw_seg, ("end", "end_time", "end_offset")) or s
        seg_windows.append((s, e))

    buckets: List[List[Word]] = [[] for _ in seg_windows]

    for raw_word in word_entries:
        text = _str_field(raw_word, ("word", "text"))
        if not text:
            continue
        start = _float_field(raw_word, ("start", "start_time", "start_offset"))
        end = _float_field(raw_word, ("end", "end_time", "end_offset"))
        word = Word(word=text, start=start, end=end)

        idx = _segment_index_for(start, seg_windows)
        if idx is None:
            # Floats can land just outside; pin to the nearest window.
            if start is None or not seg_windows:
                continue
            idx = min(
                range(len(seg_windows)),
                key=lambda i: min(abs(start - seg_windows[i][0]), abs(start - seg_windows[i][1])),
            )
        buckets[idx].append(word)

    return buckets


def _segment_index_for(start: Optional[float], windows: List[tuple]) -> Optional[int]:
    """
    Return the segment whose window owns ``start``.

    Lower bound inclusive, upper bound exclusive — so a word landing
    exactly on the seam between two segments belongs to the later one
    (which *starts* there). The very last segment treats its upper
    bound inclusively so a word ending at the audio's end still lands.
    """
    if start is None or not windows:
        return None
    last = len(windows) - 1
    for idx, (s, e) in enumerate(windows):
        if idx == last:
            if s <= start <= e:
                return idx
        elif s <= start < e:
            return idx
    return None
