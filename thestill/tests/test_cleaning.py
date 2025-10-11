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
Test script for transcript cleaning processor
"""

import json
from pathlib import Path
from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor
from thestill.core.llm_provider import create_llm_provider
from thestill.utils.config import load_config

def main():
    # Load config
    config = load_config()

    # Create LLM provider
    llm_provider = create_llm_provider(
        provider_type=config.llm_provider,
        openai_api_key=config.openai_api_key,
        openai_model=config.openai_model,
        ollama_base_url=config.ollama_base_url,
        ollama_model=config.ollama_model,
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
        anthropic_api_key=config.anthropic_api_key,
        anthropic_model=config.anthropic_model
    )

    print(f"Using {config.llm_provider.upper()} provider with model: {llm_provider.get_model_name()}")

    # Load a test transcript
    transcript_path = Path("/Users/sasasarunic/_Sources/thestill/data/transcripts/The_Prof_G_Pod_with_Scott_Galloway_How_to_AI-Proof_Your_Career,_Spot_Market_Hype,_and_Raise_Critical_Thinkers_—_ft._Greg_Shove_e884173f_transcript.json")

    with open(transcript_path, 'r', encoding='utf-8') as f:
        transcript_data = json.load(f)

    print(f"\nLoaded transcript: {transcript_path.name}")

    # Create cleaning processor
    cleaning_processor = TranscriptCleaningProcessor(llm_provider)

    # Test cleaning
    output_path = Path("/Users/sasasarunic/_Sources/thestill/data/summaries/test_cleaned")

    result = cleaning_processor.clean_transcript(
        transcript_data=transcript_data,
        podcast_title="The Prof G Pod with Scott Galloway",
        podcast_description="Scott Galloway brings his no-mercy insights to the latest in business, tech, and politics.",
        episode_title="How to AI-Proof Your Career, Spot Market Hype, and Raise Critical Thinkers — ft. Greg Shove",
        episode_description="Scott and Greg Shove discuss AI, career advice, and parenting in the modern age.",
        output_path=str(output_path),
        save_corrections=True
    )

    print("\n" + "="*50)
    print("CLEANING RESULTS:")
    print("="*50)
    print(f"Processing time: {result['processing_time']:.1f}s")
    print(f"Corrections found: {len(result['corrections'])}")
    print(f"Speakers identified: {len(result['speaker_mapping'])}")

    print("\nSpeaker Mapping:")
    for speaker, name in result['speaker_mapping'].items():
        print(f"  {speaker} -> {name}")

    print(f"\nFirst 500 chars of cleaned transcript:")
    print(result['cleaned_markdown'][:500])
    print("...")

    print("\nFiles saved to:")
    print(f"  - {output_path}.md")
    print(f"  - {output_path}.json")
    print(f"  - {output_path.parent / (output_path.name + '_corrections.json')}")
    print(f"  - {output_path.parent / (output_path.name + '_speakers.json')}")

if __name__ == "__main__":
    main()
