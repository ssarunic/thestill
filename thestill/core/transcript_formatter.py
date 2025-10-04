"""
Convert JSON transcript to tidy Markdown format for efficient LLM processing.
Performs format-only cleanup, no language editing yet.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple


class TranscriptFormatter:
    """Format JSON transcripts into clean, readable Markdown"""

    def __init__(self):
        self.timecode_interval = 300  # Show timecode every 5 minutes (300 seconds)

    def format_transcript(self, transcript_data: Dict, episode_title: str = "") -> str:
        """
        Convert JSON transcript to tidy Markdown.

        Args:
            transcript_data: Raw transcript JSON from transcriber
            episode_title: Title for the markdown header

        Returns:
            Clean Markdown string ready for LLM processing
        """
        # Step 1: Extract metadata
        metadata = self._extract_metadata(transcript_data)

        # Step 2: Normalise and merge segments by speaker
        speaker_blocks = self._merge_segments_by_speaker(transcript_data.get("segments", []))

        # Step 3: Build Markdown
        markdown = self._build_markdown(
            title=episode_title or "Transcript",
            metadata=metadata,
            speaker_blocks=speaker_blocks
        )

        return markdown

    def _extract_metadata(self, transcript_data: Dict) -> Dict:
        """Extract metadata from JSON"""
        metadata = transcript_data.get("metadata", {})

        audio_file = metadata.get("audio_file", "Unknown")
        language = metadata.get("language", "en")

        # Get duration from metadata or calculate from last segment
        duration = metadata.get("duration")
        if not duration and "segments" in transcript_data:
            segments = transcript_data["segments"]
            if segments:
                duration = segments[-1].get("end", 0)

        return {
            "audio_file": audio_file,
            "language": language,
            "duration": duration or 0
        }

    def _normalise_text(self, text: str) -> str:
        """
        Apply safe, format-only cleanups to text.
        No language editing, just formatting fixes.
        """
        if not text:
            return ""

        # Trim leading/trailing spaces
        text = text.strip()

        # Collapse multiple spaces to one
        text = re.sub(r'\s+', ' ', text)

        # Fix space before punctuation (tokenisation artefacts)
        text = re.sub(r'\s+([,\.!?;:])', r'\1', text)

        # Replace inconsistent smart quotes with straight quotes
        text = text.replace('"', '"').replace('"', '"')
        text = text.replace(''', "'").replace(''', "'")

        # Normalise ellipses to three dots
        text = re.sub(r'\.{2,}', '...', text)

        return text

    def _merge_segments_by_speaker(self, segments: List[Dict]) -> List[Tuple[float, str, str]]:
        """
        Group consecutive segments by speaker and add timecodes at intervals.

        Returns:
            List of (timestamp, speaker_id, merged_text) tuples
        """
        if not segments:
            return []

        speaker_blocks = []
        current_speaker = None
        current_text = []
        block_start_time = 0
        last_timecode = 0

        for segment in segments:
            speaker = segment.get("speaker", "SPEAKER_UNKNOWN")
            text = self._normalise_text(segment.get("text", ""))
            start_time = segment.get("start", 0)

            # Check if we should add a timecode
            # Add timecode when:
            # 1. Speaker changes, OR
            # 2. Enough time has passed (timecode_interval)
            should_add_timecode = (
                current_speaker != speaker or
                (start_time - last_timecode) >= self.timecode_interval
            )

            # If speaker changed or timecode interval reached, save previous block
            if should_add_timecode and current_text:
                merged = " ".join(current_text)
                speaker_blocks.append((block_start_time, current_speaker, merged))
                current_text = []
                block_start_time = start_time
                last_timecode = start_time

            # Update current speaker and accumulate text
            current_speaker = speaker
            current_text.append(text)

        # Add final block
        if current_text:
            merged = " ".join(current_text)
            speaker_blocks.append((block_start_time, current_speaker, merged))

        return speaker_blocks

    def _format_duration(self, seconds: float) -> str:
        """Format duration as hh:mm:ss or mm:ss"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    def _format_timecode(self, seconds: float) -> str:
        """Format timecode as mm:ss for headings"""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def _format_timecode_inline(self, seconds: float) -> str:
        """Format timecode as [HH:MM:SS] or [MM:SS] for inline use"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"
        else:
            return f"[{minutes:02d}:{secs:02d}]"

    def _build_markdown(self, title: str, metadata: Dict, speaker_blocks: List[Tuple[float, str, str]]) -> str:
        """Build final Markdown document"""
        lines = []

        # Header
        lines.append(f"# {title}")
        lines.append(f"**Audio:** {metadata['audio_file']}")
        lines.append(f"**Duration:** {self._format_duration(metadata['duration'])}")
        lines.append(f"**Language:** {metadata['language']}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Content blocks with inline timestamps
        for timestamp, speaker, text in speaker_blocks:
            # Format: `[HH:MM:SS]` **SPEAKER:** text
            timecode = self._format_timecode_inline(timestamp)
            lines.append(f"`{timecode}` **{speaker}:** {text}")
            lines.append("")  # Blank line between speakers

        return "\n".join(lines)

    def format_to_file(self, transcript_json_path: str, output_md_path: str, episode_title: str = "") -> str:
        """
        Load JSON transcript and save as formatted Markdown.

        Args:
            transcript_json_path: Path to JSON transcript
            output_md_path: Path to save Markdown output
            episode_title: Title for the markdown header

        Returns:
            The formatted Markdown string
        """
        # Load JSON
        with open(transcript_json_path, 'r', encoding='utf-8') as f:
            transcript_data = json.load(f)

        # Format to Markdown
        markdown = self.format_transcript(transcript_data, episode_title)

        # Save to file
        output_path = Path(output_md_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown)

        print(f"Formatted transcript saved to: {output_path}")
        return markdown
