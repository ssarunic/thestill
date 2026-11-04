"""Shared fixtures for the spec #110 transcript editor tests.

One raw transcript with word timings and two diarization labels, written
through the real ``PathManager`` layout next to a cleaned sidecar built
from it, so replay tests exercise the same anchors the pipeline writes.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.models.podcast import Episode
from thestill.models.transcript import Segment, Transcript, Word
from thestill.models.transcript_edits import TranscriptEdit
from thestill.utils.path_manager import PathManager

PODCAST_ID = "pppppppp-pppp-4ppp-8ppp-pppppppppppp"
EPISODE_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
RAW_REL = "show/ep.json"
SIDECAR_REL = "show/ep_cleaned.json"
MARKDOWN_REL = "show/ep_cleaned.md"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def _segment(seg_id: int, start: float, speaker: str, text: str) -> Segment:
    words: List[Word] = []
    at = start
    for token in text.split():
        words.append(Word(word=token, start=round(at, 2), end=round(at + 0.4, 2)))
        at += 0.5
    return Segment(id=seg_id, start=start, end=round(at, 2), text=text, speaker=speaker, words=words)


def make_raw() -> Transcript:
    """Four raw segments, two voices, half-second words."""
    segments = [
        _segment(0, 0.0, "SPEAKER_00", "Welcome back to the show everyone"),
        _segment(1, 4.0, "SPEAKER_01", "Thanks for having me Ada"),
        _segment(2, 8.0, "SPEAKER_00", "Today we talk about OKRs and why they fail"),
        _segment(3, 14.0, "SPEAKER_01", "They fail when they become a roadmap"),
    ]
    return Transcript(
        audio_file="ep.wav",
        language="en",
        text=" ".join(s.text for s in segments),
        segments=segments,
        processing_time=0.0,
        model_used="fixture",
        timestamp=0.0,
        diarization_enabled=True,
        speakers_detected=2,
    )


def make_sidecar(raw: Transcript, *, with_ad: bool = True) -> AnnotatedTranscript:
    """The cleaned sidecar: SPEAKER_00 mapped to a name, SPEAKER_01 left as a
    label (the canary the editor exists to fix), plus an ad break."""
    annotated = AnnotatedTranscript.from_raw(raw, episode_id=EPISODE_ID)
    for segment in annotated.segments:
        if segment.speaker == "SPEAKER_00":
            segment.speaker = "Ada Lovelace"
    if with_ad:
        annotated.segments.insert(
            2,
            AnnotatedSegment(
                id=99,
                start=7.0,
                end=8.0,
                speaker=None,
                text="This episode is brought to you by Sponsor Corp",
                kind="ad_break",
                sponsor="Sponsor Corp",
                source_segment_ids=[1],
                source_word_span=None,
            ),
        )
    for index, segment in enumerate(annotated.segments):
        segment.id = index
    return annotated


def write_transcripts(storage: Path, raw: Transcript, sidecar: AnnotatedTranscript) -> PathManager:
    paths = PathManager(str(storage))
    raw_path = paths.raw_transcript_file(RAW_REL)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw.model_dump_json(), encoding="utf-8")
    sidecar_path = paths.clean_transcript_file(SIDECAR_REL)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(sidecar.model_dump_json(indent=2), encoding="utf-8")
    paths.clean_transcript_file(MARKDOWN_REL).write_text(
        sidecar.to_blended_markdown(exclude_kinds={"ad_break"}), encoding="utf-8"
    )
    return paths


def make_episode(**overrides) -> Episode:
    fields = dict(
        id=EPISODE_ID,
        external_id="e1",
        title="OKRs",
        slug="okrs",
        description="",
        audio_url="https://example.com/ep.mp3",
        raw_transcript_path=RAW_REL,
        clean_transcript_path=MARKDOWN_REL,
        clean_transcript_json_path=SIDECAR_REL,
        summary_path="show/ep_summary.md",
    )
    fields.update(overrides)
    return Episode(**fields)


def seed_episode_rows(db_path: str) -> None:
    """A podcast and episode row for the FK the op log carries."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, description, language)"
            " VALUES (?, 'https://x.test/f.xml', 'Show', 'show', '', 'en')",
            (PODCAST_ID,),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, duration,"
            " raw_transcript_path, clean_transcript_path, clean_transcript_json_path, summary_path)"
            " VALUES (?, ?, 'e1', 'OKRs', 'okrs', 'https://example.com/ep.mp3', 3600, ?, ?, ?, 'show/ep_summary.md')",
            (EPISODE_ID, PODCAST_ID, RAW_REL, MARKDOWN_REL, SIDECAR_REL),
        )
        conn.commit()
    finally:
        conn.close()


def make_edit(
    op: str,
    anchor: dict,
    args: dict,
    *,
    raw_sha256: str,
    seq: int = 0,
    reverts: Optional[str] = None,
    edit_id: Optional[str] = None,
    session_id: str = "s1",
    created_at: datetime = NOW,
    actor_user_id: Optional[str] = None,
) -> TranscriptEdit:
    return TranscriptEdit(
        id=edit_id or str(uuid.uuid4()),
        episode_id=EPISODE_ID,
        seq=seq,
        op=op,
        raw_sha256=raw_sha256,
        anchor=anchor,
        args=args,
        before={},
        reverts=reverts,
        session_id=session_id,
        report_id=None,
        actor_user_id=actor_user_id,
        actor_kind="cli",
        created_at=created_at,
    )


def segment_anchor(segment: AnnotatedSegment) -> dict:
    """The anchor the editor sends: text, not its hash."""
    return {
        "source_segment_ids": list(segment.source_segment_ids),
        "source_word_span": segment.source_word_span.model_dump() if segment.source_word_span else None,
        "start_s": segment.start,
        "end_s": segment.end,
        "text": segment.text,
    }


class FakeEditRepository:
    """In-memory ``TranscriptEditRepository`` for replay tests."""

    def __init__(self) -> None:
        self.rows: List[TranscriptEdit] = []
        self.refreshed: dict = {}

    def append(self, edit: TranscriptEdit, *, expected_last_seq: int) -> Optional[TranscriptEdit]:
        if self.last_seq(edit.episode_id) != expected_last_seq:
            return None
        stored = edit.model_copy(update={"seq": expected_last_seq + 1})
        self.rows.append(stored)
        return stored

    def list_for_episode(self, episode_id: str) -> List[TranscriptEdit]:
        return sorted((r for r in self.rows if r.episode_id == episode_id), key=lambda r: r.seq)

    def last_seq(self, episode_id: str) -> int:
        rows = self.list_for_episode(episode_id)
        return rows[-1].seq if rows else 0

    def get(self, edit_id: str) -> Optional[TranscriptEdit]:
        return next((r for r in self.rows if r.id == edit_id), None)

    def list_by_actor(self, actor_user_id: str, *, since: datetime) -> List[TranscriptEdit]:
        return [r for r in self.rows if r.actor_user_id == actor_user_id and r.created_at >= since]

    def episodes_with_edits(self):
        out = {}
        for row in self.rows:
            seq, newest = out.get(row.episode_id, (0, row.created_at))
            out[row.episode_id] = (max(seq, row.seq), max(newest, row.created_at))
        return [(episode_id, seq, newest) for episode_id, (seq, newest) in out.items()]

    def mark_refreshed(self, episode_id: str, *, seq: int, at: datetime) -> None:
        self.refreshed[episode_id] = (seq, at)

    def episodes_pending_refresh(self, *, idle_before: datetime):
        out = []
        for episode_id, seq, newest in self.episodes_with_edits():
            done = self.refreshed.get(episode_id, (0, None))[0]
            if seq > done and newest <= idle_before:
                out.append((episode_id, seq))
        return out
