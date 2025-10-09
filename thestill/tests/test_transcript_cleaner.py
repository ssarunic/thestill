#!/usr/bin/env python3
"""
Quick test script for transcript cleaning with overlapping chunking.
Tests the implementation without requiring a full podcast processing pipeline.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from thestill.core.transcript_cleaner import TranscriptCleaner, TranscriptCleanerConfig
from thestill.core.llm_provider import OllamaProvider, OpenAIProvider


def create_long_sample_transcript(num_words: int = 50000) -> str:
    """Generate a sample transcript with known issues to clean"""

    # Sample paragraph with errors
    sample = """
[00:15] Um, so, like, the thing is, you know, uh, we're talking about,
like, the um, quantum computing stuff, right? And, uh, Sara Johnson,
she said that, like, Open AI is, um, working on this stuff. You know,
I mean, it's like, really important for the future, right?

[00:45] So, uh, Doctor Smith mentioned that, um, N A S A has been,
like, collaborating with, you know, the team at M I T on this project.
And, um, they're using, like, L L M models for, um, natural language
processing, you know what I mean?

[01:15] The C E O of Tech Corp, um, like, mentioned that they're,
you know, gonna be releasing, like, a new product called, um, ChatGTP
or something like that. And, uh, it's gonna be, like, really cool,
I think, you know?

[01:45] So anyway, um, the main takeaway is that, like, artificial
intelligence is, you know, uh, really important for, um, the future
of technology. And, like, we need to, uh, make sure that, you know,
we're building it, um, responsibly, right?
"""

    # Repeat to create longer text
    num_repeats = num_words // 200  # ~200 words per sample
    long_text = sample * num_repeats

    return long_text


def test_basic_cleaning():
    """Test basic cleaning functionality"""
    print("\n" + "=" * 60)
    print("TEST 1: Basic Cleaning (Short Text)")
    print("=" * 60)

    # Create short sample
    text = """
[00:15] Um, so, like, Sara Johnson from Open AI said that, uh,
the L L M models are, you know, really powerful.
"""

    print("\n📝 ORIGINAL TEXT:")
    print(text)

    # Create provider (use Ollama by default)
    try:
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="gemma3:4b"
        )
        print(f"\n✅ Using Ollama with gemma3:4b")
    except Exception as e:
        print(f"\n❌ Ollama not available: {e}")
        print("Please ensure Ollama is running: ollama serve")
        return False

    # Create config
    config = TranscriptCleanerConfig(
        chunk_size=20000,
        overlap_pct=0.15,
        extract_entities=True
    )

    # Create cleaner
    cleaner = TranscriptCleaner(provider=provider, config=config)

    # Clean
    try:
        result = cleaner.clean_transcript(text)

        print("\n✨ CLEANED TEXT:")
        print(result['cleaned_text'])

        print("\n📊 STATISTICS:")
        print(f"  - Processing time: {result['processing_time']:.1f}s")
        print(f"  - Chunks processed: {result['chunks_processed']}")
        print(f"  - Original tokens: {result['original_tokens']}")
        print(f"  - Final tokens: {result['final_tokens']}")
        print(f"  - Token change: {result['final_tokens'] - result['original_tokens']}")

        if result['entities']:
            print(f"\n🏷️  ENTITIES EXTRACTED:")
            for entity in result['entities']:
                print(f"  - {entity['term']} ({entity['type']})")

        return True

    except Exception as e:
        print(f"\n❌ Error during cleaning: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_chunking():
    """Test overlapping chunking with longer text"""
    print("\n" + "=" * 60)
    print("TEST 2: Overlapping Chunking (Long Text)")
    print("=" * 60)

    # Create long sample (50K words)
    text = create_long_sample_transcript(50000)

    print(f"\n📝 Generated transcript: {len(text):,} characters (~{len(text)//4:,} tokens)")

    # Create provider
    try:
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="gemma3:4b"
        )
    except Exception as e:
        print(f"\n❌ Ollama not available: {e}")
        return False

    # Create config
    config = TranscriptCleanerConfig(
        chunk_size=20000,  # Force chunking
        overlap_pct=0.15,
        extract_entities=True
    )

    # Create cleaner
    cleaner = TranscriptCleaner(provider=provider, config=config)

    # Clean
    try:
        result = cleaner.clean_transcript(text)

        print("\n📊 CHUNKING STATISTICS:")
        print(f"  - Chunks processed: {result['chunks_processed']}")
        print(f"  - Processing time: {result['processing_time']:.1f}s")
        print(f"  - Time per chunk: {result['processing_time'] / result['chunks_processed']:.1f}s")
        print(f"  - Original tokens: {result['original_tokens']:,}")
        print(f"  - Final tokens: {result['final_tokens']:,}")
        print(f"  - Token reduction: {((result['original_tokens'] - result['final_tokens']) / result['original_tokens'] * 100):.1f}%")

        if result['entities']:
            print(f"\n🏷️  ENTITIES EXTRACTED ({len(result['entities'])}):")
            for entity in result['entities'][:10]:  # Show first 10
                print(f"  - {entity['term']} ({entity['type']})")
            if len(result['entities']) > 10:
                print(f"  ... and {len(result['entities']) - 10} more")

        # Show sample of cleaned text
        print("\n✨ SAMPLE OF CLEANED TEXT (first 500 chars):")
        print(result['cleaned_text'][:500] + "...")

        return True

    except Exception as e:
        print(f"\n❌ Error during cleaning: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_without_entity_extraction():
    """Test cleaning without entity extraction (faster)"""
    print("\n" + "=" * 60)
    print("TEST 3: Fast Mode (No Entity Extraction)")
    print("=" * 60)

    text = create_long_sample_transcript(30000)

    print(f"\n📝 Generated transcript: {len(text):,} characters")

    try:
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="gemma3:4b"
        )
    except Exception as e:
        print(f"\n❌ Ollama not available: {e}")
        return False

    # Config without entity extraction
    config = TranscriptCleanerConfig(
        chunk_size=20000,
        overlap_pct=0.15,
        extract_entities=False  # Disabled for speed
    )

    cleaner = TranscriptCleaner(provider=provider, config=config)

    try:
        result = cleaner.clean_transcript(text)

        print("\n📊 FAST MODE STATISTICS:")
        print(f"  - Chunks processed: {result['chunks_processed']}")
        print(f"  - Processing time: {result['processing_time']:.1f}s")
        print(f"  - Entities extracted: {len(result['entities'])} (should be 0)")

        return True

    except Exception as e:
        print(f"\n❌ Error during cleaning: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("TRANSCRIPT CLEANER TEST SUITE")
    print("=" * 60)

    # Check if Ollama is running
    print("\n🔍 Checking Ollama availability...")
    try:
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            model="gemma3:4b"
        )
        print("✅ Ollama is available")
    except Exception as e:
        print(f"❌ Ollama is not available: {e}")
        print("\nTo run these tests, please:")
        print("1. Install Ollama: https://ollama.ai")
        print("2. Start Ollama: ollama serve")
        print("3. Pull model: ollama pull gemma3:4b")
        return 1

    # Run tests
    tests = [
        ("Basic Cleaning", test_basic_cleaning),
        ("Overlapping Chunking", test_chunking),
        ("Fast Mode", test_without_entity_extraction)
    ]

    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"\n❌ Test '{test_name}' failed with exception: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    for test_name, success in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{status} - {test_name}")

    passed = sum(1 for _, success in results if success)
    total = len(results)

    print(f"\nTotal: {passed}/{total} tests passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
