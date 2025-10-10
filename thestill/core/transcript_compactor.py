# Copyright 2025 thestill.ai
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
Compact transcript converter - converts verbose Whisper JSON to clean Markdown.
This significantly reduces token usage for LLM processing while preserving all meaningful content.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import timedelta


class TranscriptCompactor:
    """Converts Whisper's verbose JSON transcripts into compact Markdown format"""

    def __init__(self):
        self.min_segment_gap = 2.0  # Merge segments within 2 seconds of same speaker

    def compact_transcript(self, transcript_path: str, output_md_path: str = None,
                          output_json_path: str = None) -> Dict:
        """
        Convert full Whisper JSON to:
        1. Pruned JSON (removed word-level data, probabilities, etc.)
        2. Clean Markdown (human/LLM readable format)

        Returns dict with:
            - pruned_json: stripped JSON with only essential data
            - markdown: formatted markdown text
            - token_savings_estimate: percentage reduction
        """
        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                full_transcript = json.load(f)

            # Create pruned JSON
            pruned = self._prune_json(full_transcript)

            # Convert to Markdown
            markdown = self._to_markdown(pruned)

            # Calculate token savings
            original_size = len(json.dumps(full_transcript))
            markdown_size = len(markdown)
            savings = ((original_size - markdown_size) / original_size) * 100

            result = {
                "pruned_json": pruned,
                "markdown": markdown,
                "token_savings_estimate": round(savings, 1),
                "original_chars": original_size,
                "markdown_chars": markdown_size
            }

            # Save outputs if paths provided
            if output_json_path:
                self._save_pruned_json(pruned, output_json_path)

            if output_md_path:
                self._save_markdown(markdown, output_md_path)

            return result

        except Exception as e:
            print(f"Error compacting transcript: {e}")
            raise

    def _prune_json(self, full_transcript: Dict) -> Dict:
        """Remove verbose fields while keeping essential metadata and segments"""

        segments = []
        for seg in full_transcript.get("segments", []):
            pruned_seg = {
                "start": round(seg.get("start", 0)),  # Round to seconds
                "end": round(seg.get("end", 0)),
                "text": seg.get("text", "").strip()
            }

            # Keep speaker info if available (from diarization)
            if "speaker" in seg:
                pruned_seg["speaker"] = seg["speaker"]

            segments.append(pruned_seg)

        # Merge consecutive segments from same speaker
        merged_segments = self._merge_segments(segments)

        return {
            "metadata": {
                "audio_file": full_transcript.get("audio_file", ""),
                "language": full_transcript.get("language", "en"),
                "duration": full_transcript.get("segments", [{}])[-1].get("end", 0) if full_transcript.get("segments") else 0,
                "model": full_transcript.get("model_used", "unknown")
            },
            "segments": merged_segments
        }

    def _merge_segments(self, segments: List[Dict]) -> List[Dict]:
        """Merge consecutive segments from same speaker within time threshold"""
        if not segments:
            return []

        merged = []
        current = segments[0].copy()

        for i in range(1, len(segments)):
            seg = segments[i]

            # Check if same speaker and close in time
            same_speaker = (current.get("speaker") == seg.get("speaker")) or \
                          ("speaker" not in current and "speaker" not in seg)
            time_gap = seg["start"] - current["end"]

            if same_speaker and time_gap <= self.min_segment_gap:
                # Merge: extend text and update end time
                current["text"] += " " + seg["text"]
                current["end"] = seg["end"]
            else:
                # Different speaker or gap too large: save current, start new
                merged.append(current)
                current = seg.copy()

        # Add final segment
        merged.append(current)

        return merged

    def _to_markdown(self, pruned_transcript: Dict) -> str:
        """Convert pruned JSON to clean, readable Markdown"""

        metadata = pruned_transcript.get("metadata", {})
        segments = pruned_transcript.get("segments", [])

        # Extract episode title and show from audio filename if available
        audio_file = metadata.get("audio_file", "")
        show_name, episode_title = self._parse_filename(audio_file)

        # Build Markdown
        lines = []

        # Header with metadata
        if show_name or episode_title:
            lines.append(f"# {show_name} — {episode_title}\n")
        else:
            lines.append("# Podcast Episode\n")

        if audio_file:
            lines.append(f"**Audio:** {audio_file}\n")

        duration_str = self._format_duration(metadata.get("duration", 0))
        lines.append(f"**Duration:** {duration_str}")
        lines.append(f"**Language:** {metadata.get('language', 'en')}\n")
        lines.append("---\n")

        # Process segments
        current_section_time = 0
        section_interval = 300  # New section header every 5 minutes

        for i, seg in enumerate(segments):
            start = seg["start"]
            text = seg["text"].strip()
            speaker = seg.get("speaker", "Speaker")

            if not text:
                continue

            # Add section headers at intervals
            if start - current_section_time >= section_interval or i == 0:
                timestamp = self._format_timestamp(start)
                lines.append(f"\n## {timestamp}\n")
                current_section_time = start

            # Detect special segments (ads, intro, outro)
            segment_type = self._detect_segment_type(text, start, metadata.get("duration", 0))

            if segment_type == "ad":
                lines.append(f"**[AD] {speaker}:** {text}\n")
            elif segment_type == "intro" and i < 5:  # Only mark intro in first few segments
                lines.append(f"**[INTRO] {speaker}:** {text}\n")
            elif segment_type == "outro":
                lines.append(f"**[OUTRO] {speaker}:** {text}\n")
            else:
                lines.append(f"**{speaker}:** {text}\n")

        return "\n".join(lines)

    def _parse_filename(self, audio_path: str) -> tuple:
        """Extract show name and episode title from filename"""
        try:
            filename = Path(audio_path).stem

            # Try to split on common patterns
            # Example: "The_Rest_Is_Politics_451._Is_Trump_Destroying_the_UN__ceee9a87"
            parts = filename.split("_")

            # Look for episode number pattern
            show_parts = []
            episode_parts = []
            found_episode_num = False

            for part in parts:
                # Check if this looks like an episode number
                if any(char.isdigit() for char in part) and len(part) <= 5:
                    found_episode_num = True
                    episode_parts.append(part)
                elif not found_episode_num:
                    show_parts.append(part)
                else:
                    episode_parts.append(part)

            show_name = " ".join(show_parts).replace("_", " ").strip()
            episode_title = " ".join(episode_parts).replace("_", " ").strip()

            # Remove GUID suffix if present (8 hex chars at end)
            if episode_title and len(episode_title) > 8:
                parts = episode_title.rsplit(" ", 1)
                if len(parts[-1]) == 8 and all(c in "0123456789abcdef" for c in parts[-1].lower()):
                    episode_title = parts[0]

            return show_name, episode_title

        except:
            return "Unknown Show", "Unknown Episode"

    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS"""
        td = timedelta(seconds=int(seconds))
        hours = td.seconds // 3600
        minutes = (td.seconds % 3600) // 60
        secs = td.seconds % 60

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"

    def _detect_segment_type(self, text: str, start_time: float, total_duration: float) -> str:
        """Detect if segment is ad, intro, or outro based on content and timing"""
        text_lower = text.lower()

        # Ad detection keywords
        ad_keywords = [
            "brought to you by",
            "sponsor",
            "discount code",
            "promo code",
            "visit our website",
            "use code",
            "special offer",
            "sign up at",
            "go to ",
            ".com",
            "get % off",
            "subscribe at"
        ]

        if any(keyword in text_lower for keyword in ad_keywords):
            return "ad"

        # Intro detection (first 2 minutes)
        if start_time < 120:
            intro_keywords = ["welcome to", "this is", "i'm your host", "thanks for listening"]
            if any(keyword in text_lower for keyword in intro_keywords):
                return "intro"

        # Outro detection (last 2 minutes)
        if total_duration > 0 and (total_duration - start_time) < 120:
            outro_keywords = ["thanks for listening", "see you next", "subscribe", "follow us"]
            if any(keyword in text_lower for keyword in outro_keywords):
                return "outro"

        return "content"

    def _save_pruned_json(self, pruned: Dict, output_path: str):
        """Save pruned JSON to file"""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(pruned, f, indent=2, ensure_ascii=False)
            print(f"Pruned JSON saved to: {output_path}")
        except Exception as e:
            print(f"Error saving pruned JSON: {e}")

    def _save_markdown(self, markdown: str, output_path: str):
        """Save Markdown to file"""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(markdown)
            print(f"Markdown saved to: {output_path}")
        except Exception as e:
            print(f"Error saving Markdown: {e}")
