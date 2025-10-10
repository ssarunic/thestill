#!/usr/bin/env python3
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
Test script for transcript formatter
"""

import json
from pathlib import Path
from thestill.core.transcript_formatter import TranscriptFormatter

def main():
    # Load a test transcript
    transcript_path = Path("data/transcripts/The_Prof_G_Pod_with_Scott_Galloway_How_to_AI-Proof_Your_Career,_Spot_Market_Hype,_and_Raise_Critical_Thinkers_—_ft._Greg_Shove_e884173f_transcript.json")

    with open(transcript_path, 'r', encoding='utf-8') as f:
        transcript_data = json.load(f)

    print(f"Loaded transcript: {transcript_path.name}")
    print(f"Segments: {len(transcript_data.get('segments', []))}")

    # Create formatter
    formatter = TranscriptFormatter()

    # Format to markdown
    output_path = Path("data/test_formatted.md")

    markdown = formatter.format_to_file(
        str(transcript_path),
        str(output_path),
        episode_title="How to AI-Proof Your Career, Spot Market Hype, and Raise Critical Thinkers — ft. Greg Shove"
    )

    print("\n" + "="*50)
    print("FORMATTED MARKDOWN (first 1000 chars):")
    print("="*50)
    print(markdown[:1000])
    print("...")

    print(f"\nFull formatted transcript saved to: {output_path}")
    print(f"Size reduction: {len(json.dumps(transcript_data))} → {len(markdown)} chars")
    print(f"Reduction: {((len(json.dumps(transcript_data)) - len(markdown)) / len(json.dumps(transcript_data)) * 100):.1f}%")

if __name__ == "__main__":
    main()
