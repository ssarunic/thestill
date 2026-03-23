"""
Pure metric functions for transcript quality evaluation.

No I/O, no thestill imports. Accepts plain str/dict/list arguments.
"""

import re
from typing import Optional


def normalize_text(text: str) -> str:
    """Strip timestamps, speaker labels, markdown, and normalize whitespace for WER comparison."""
    # Remove blockquotes (ad breaks)
    text = re.sub(r"^>.*$", "", text, flags=re.MULTILINE)
    # Remove timestamps: [HH:MM:SS] or [MM:SS]
    text = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", "", text)
    # Remove speaker labels: **Speaker Name:** or **[Clip]:**
    text = re.sub(r"\*\*[^*]+?\*\*:?", "", text)
    # Remove markdown bold/italic remnants
    text = re.sub(r"\*+", "", text)
    # Remove [AD BREAK], [Clip], [Soundbite], [?] markers
    text = re.sub(r"\[(?:AD BREAK|Clip|Soundbite|\?)\]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def compute_wer(hypothesis: str, reference: str) -> Optional[float]:
    """Compute Word Error Rate using jiwer. Returns None if either text is empty."""
    if not hypothesis.strip() or not reference.strip():
        return None
    try:
        import jiwer

        return jiwer.wer(reference, hypothesis)
    except ImportError:
        # Fallback: simple word-level error rate
        ref_words = reference.split()
        hyp_words = hypothesis.split()
        if not ref_words:
            return None
        # Levenshtein on word lists
        return _word_error_rate(ref_words, hyp_words)


def _word_error_rate(ref: list[str], hyp: list[str]) -> float:
    """Simple WER via word-level edit distance (fallback if jiwer unavailable)."""
    n, m = len(ref), len(hyp)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            prev, dp[j] = dp[j], min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
    return dp[m] / n if n > 0 else 0.0


def compute_content_retention(raw_segments: list[dict], cleaned_text: str) -> float:
    """Fraction of raw transcript words found in cleaned text (word-boundary match)."""
    raw_words = []
    for seg in raw_segments:
        raw_words.extend(seg.get("text", "").lower().split())
    if not raw_words:
        return 0.0
    cleaned_words = set(cleaned_text.lower().split())
    found = sum(1 for w in raw_words if w in cleaned_words)
    return found / len(raw_words)


def check_first_timestamp(cleaned_text: str, raw_first_ts: float, tolerance_s: float = 5.0) -> dict:
    """Check if cleaned transcript starts near the raw transcript's first timestamp."""
    match = re.search(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]", cleaned_text)
    if not match:
        return {"ok": False, "cleaned_first_ts": -1, "raw_first_ts": raw_first_ts, "delta_s": -1}
    if match.group(3) is not None:
        cleaned_ts = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    else:
        cleaned_ts = int(match.group(1)) * 60 + int(match.group(2))
    delta = abs(cleaned_ts - raw_first_ts)
    return {
        "ok": delta <= tolerance_s,
        "cleaned_first_ts": cleaned_ts,
        "raw_first_ts": raw_first_ts,
        "delta_s": delta,
    }


def extract_entity_vocab(podcast_facts, episode_facts) -> list[str]:
    """Flatten facts into a vocabulary list for entity accuracy checking.

    Filters out short words (< 4 chars) to avoid false positives.
    """
    vocab = set()
    if podcast_facts:
        for field in ["hosts", "keywords", "sponsors", "production_team", "known_guests"]:
            for item in getattr(podcast_facts, field, []) or []:
                # Extract the name part before any parenthetical or dash description
                name = re.split(r"\s*[-–(]", item)[0].strip()
                if len(name) >= 4:
                    vocab.add(name)
    if episode_facts:
        for field in ["guests", "topics_keywords", "ad_sponsors"]:
            for item in getattr(episode_facts, field, []) or []:
                name = re.split(r"\s*[-–(]", item)[0].strip()
                if len(name) >= 4:
                    vocab.add(name)
    return sorted(vocab)


def compute_entity_accuracy(text: str, vocab: list[str]) -> dict:
    """Check which vocabulary entities appear in text.

    Returns dict with found/total/ratio and lists of found/missed entities.
    """
    if not vocab:
        return {"found": 0, "total": 0, "ratio": 1.0, "found_list": [], "missed_list": []}
    text_lower = text.lower()
    found, missed = [], []
    for entity in vocab:
        if entity.lower() in text_lower:
            found.append(entity)
        else:
            missed.append(entity)
    total = len(vocab)
    return {
        "found": len(found),
        "total": total,
        "ratio": len(found) / total if total > 0 else 1.0,
        "found_list": found,
        "missed_list": missed,
    }


def compute_timestamp_alignment(dalston_words: list[dict], elevenlabs_words: list[dict]) -> Optional[dict]:
    """Compare word-level timestamps between two transcriptions.

    Matches words by text (case-insensitive), computes timing deltas.
    Returns None if either word list is empty.
    """
    if not dalston_words or not elevenlabs_words:
        return None

    # Build lookup: word_text -> list of (start, end) from ElevenLabs
    el_lookup: dict[str, list[float]] = {}
    for w in elevenlabs_words:
        word_text = (w.get("word") or w.get("text", "")).strip().lower()
        start = w.get("start", 0.0)
        if word_text and start is not None:
            el_lookup.setdefault(word_text, []).append(start)

    deltas = []
    el_consumed: dict[str, int] = {}  # track which EL index we've used per word

    for w in dalston_words:
        word_text = (w.get("word") or w.get("text", "")).strip().lower()
        dal_start = w.get("start", 0.0)
        if not word_text or dal_start is None:
            continue
        el_starts = el_lookup.get(word_text)
        if not el_starts:
            continue
        # Use next unconsumed occurrence
        idx = el_consumed.get(word_text, 0)
        if idx >= len(el_starts):
            continue
        el_consumed[word_text] = idx + 1
        deltas.append(abs(dal_start - el_starts[idx]))

    if not deltas:
        return None

    deltas.sort()
    n = len(deltas)
    return {
        "matched_words": n,
        "mean_delta_s": sum(deltas) / n,
        "median_delta_s": (deltas[n // 2 - 1] + deltas[n // 2]) / 2 if n % 2 == 0 else deltas[n // 2],
        "p90_delta_s": deltas[int(n * 0.9)],
        "max_delta_s": deltas[-1],
    }


def extract_words_from_transcript(transcript_data: dict) -> list[dict]:
    """Extract flat word list from a transcript JSON (segments -> words)."""
    words = []
    for seg in transcript_data.get("segments", []):
        for w in seg.get("words", []):
            words.append(w)
    return words


def aggregate_episode_metrics(
    *,
    raw_dalston: Optional[dict],
    raw_elevenlabs: Optional[dict],
    clean_d_text: Optional[str],
    clean_b_text: Optional[str],
    existing_cleaned_text: Optional[str],
    podcast_facts,
    episode_facts,
    timings: dict,
) -> dict:
    """Compute all metrics for one episode. Returns a dict suitable for JSON serialization."""
    result: dict = {"timings": timings}

    # Extract raw texts
    dal_text = ""
    dal_segments = []
    dal_words = []
    if raw_dalston:
        dal_text = raw_dalston.get("text", "")
        dal_segments = raw_dalston.get("segments", [])
        dal_words = extract_words_from_transcript(raw_dalston)
        raw_first_ts = dal_segments[0].get("start", 0.0) if dal_segments else 0.0
        result["raw_duration_s"] = dal_segments[-1].get("end", 0.0) if dal_segments else 0.0
        result["raw_segments"] = len(dal_segments)
    else:
        raw_first_ts = 0.0

    el_text = ""
    el_words = []
    if raw_elevenlabs:
        el_text = raw_elevenlabs.get("text", "")
        el_words = extract_words_from_transcript(raw_elevenlabs)

    # Vocab for entity checks
    vocab = extract_entity_vocab(podcast_facts, episode_facts)
    result["entity_vocab_size"] = len(vocab)

    # Normalize texts for WER
    dal_norm = normalize_text(dal_text)
    el_norm = normalize_text(el_text)

    # WER: Dalston vs ElevenLabs (raw transcription accuracy)
    result["wer_dalston_vs_elevenlabs"] = compute_wer(dal_norm, el_norm)

    # Per-variant metrics
    variants = {}
    for name, text in [("clean_d", clean_d_text), ("clean_b", clean_b_text), ("existing", existing_cleaned_text)]:
        if text is None:
            variants[name] = None
            continue
        v: dict = {}
        text_norm = normalize_text(text)

        # WER vs ElevenLabs
        v["wer_vs_elevenlabs"] = compute_wer(text_norm, el_norm) if el_norm else None

        # Content retention vs raw Dalston
        v["content_retention"] = compute_content_retention(dal_segments, text)

        # First timestamp check
        v["first_timestamp"] = check_first_timestamp(text, raw_first_ts)

        # Entity accuracy
        v["entity_accuracy"] = compute_entity_accuracy(text, vocab)

        v["char_count"] = len(text)
        variants[name] = v

    result["variants"] = variants

    # Timestamp alignment (Dalston vs ElevenLabs word-level)
    result["timestamp_alignment"] = compute_timestamp_alignment(dal_words, el_words)

    return result
