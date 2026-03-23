"""
Test episode registry and state models for transcript quality evaluation.
"""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class EpisodeSpec(BaseModel):
    index: int
    podcast_slug: str
    episode_slug: str
    label: str
    duration_category: str = "normal"  # short | normal | long


class StepState(BaseModel):
    status: str = "pending"  # pending | done | failed | skipped
    output_path: Optional[str] = None
    timing_s: Optional[float] = None
    error: Optional[str] = None


class EpisodeState(BaseModel):
    dalston_transcribe: StepState = StepState()
    elevenlabs_transcribe: StepState = StepState()
    clean_d: StepState = StepState()
    clean_b: StepState = StepState()
    metrics: StepState = StepState()


# fmt: off
TEST_EPISODES: list[EpisodeSpec] = [
    # --- Problematic (known content skipping, ratio < 0.5) ---
    EpisodeSpec(index=0, podcast_slug="lenny-s-podcast-product-career-growth",
        episode_slug="the-high-growth-handbook-molly-grahams-frameworks-for-leading-through-chaos-change-and-scale",
        label="PROB-1 Lenny/Molly Graham", duration_category="long"),
    EpisodeSpec(index=1, podcast_slug="the-artificial-intelligence-show",
        episode_slug="194-agentic-ai-timelines-generalists-vs-specialists-resume-tips-ai-learning-ownership-handling-model",
        label="PROB-2 AI Show #194", duration_category="normal"),
    EpisodeSpec(index=2, podcast_slug="the-mad-podcast-with-matt-turck",
        episode_slug="everything-gets-rebuilt-the-new-ai-agent-stack-harrison-chase-langchain",
        label="PROB-3 MAD/LangChain", duration_category="normal"),
    EpisodeSpec(index=3, podcast_slug="what-s-cooking-a-podcast-from-nory",
        episode_slug="uk-budget-deep-dive-with-kate-nicholls-higher-costs-zero-relief",
        label="PROB-4 Nory/UK Budget", duration_category="normal"),
    EpisodeSpec(index=4, podcast_slug="the-twenty-minute-vc-20vc-venture-capital-startup-funding-the-pitch",
        episode_slug="20vc-50-of-funds-will-go-out-of-business-why-growth-expectations-today-are-bs-and-will-not-last-why",
        label="PROB-5 20VC/Funds", duration_category="long"),

    # --- Normal (from prototype) ---
    EpisodeSpec(index=5, podcast_slug="how-i-ai",
        episode_slug="pms-who-use-ai-will-replace-those-who-dont-googles-ai-product-lead-on-the-new-pm-toolkit-marily",
        label="NORM-1 How I AI/Google PM", duration_category="normal"),
    EpisodeSpec(index=6, podcast_slug="the-pragmatic-engineer",
        episode_slug="how-ai-will-change-software-engineering-with-martin-fowler",
        label="NORM-2 Pragmatic/Fowler", duration_category="normal"),
    EpisodeSpec(index=7, podcast_slug="the-rest-is-science",
        episode_slug="how-to-drink-lava",
        label="NORM-3 Rest is Science/Lava", duration_category="short"),
    EpisodeSpec(index=8, podcast_slug="masters-of-scale",
        episode_slug="reid-hoffman-inflection-ais-sean-white-on-designing-ai-that-makes-us-better-humans",
        label="NORM-4 Masters of Scale/Hoffman", duration_category="short"),
    EpisodeSpec(index=9, podcast_slug="how-i-ai",
        episode_slug="how-zapiers-ea-built-an-army-of-ai-interns-to-automate-meeting-prep-strengthen-team-culture-and-scal",
        label="NORM-5 How I AI/Zapier", duration_category="normal"),
    EpisodeSpec(index=10, podcast_slug="the-twenty-minute-vc-20vc-venture-capital-startup-funding-the-pitch",
        episode_slug="20vc-0-260m-in-revenue-in-three-years-how-we-did-it-you-need-to-work-weekends-to-win-most-founders-a",
        label="NORM-6 20VC/$260M Revenue", duration_category="normal"),
    EpisodeSpec(index=11, podcast_slug="the-artificial-intelligence-show",
        episode_slug="188-ai-trends-for-2026-google-deepmind-ai-predictions-gemini-3-flash-ai-world-models-are-ai-job-loss",
        label="NORM-7 AI Show #188", duration_category="long"),
    EpisodeSpec(index=12, podcast_slug="the-twenty-minute-vc-20vc-venture-capital-startup-funding-the-pitch",
        episode_slug="20vc-brex-acquired-for-5-15bn-a16z-companies-are-2-3-ai-revenues-anthropic-inference-costs-skyrocket",
        label="NORM-8 20VC/Brex", duration_category="long"),

    # --- Additional diversity episodes ---
    EpisodeSpec(index=13, podcast_slug="product-thinking",
        episode_slug="episode-252-understanding-product-vs-project-management",
        label="SHORT-1 Product Thinking (7m)", duration_category="short"),
    EpisodeSpec(index=14, podcast_slug="the-rest-is-science",
        episode_slug="this-glass-was-made-by-lightning",
        label="NORM-9 Rest is Science/Lightning", duration_category="normal"),
    EpisodeSpec(index=15, podcast_slug="prof-g-markets",
        episode_slug="eu-strikes-deal-with-india-in-shift-from-u-s",
        label="MULTI-1 Prof G Markets (8sp)", duration_category="short"),
    EpisodeSpec(index=16, podcast_slug="the-rest-is-money",
        episode_slug="246-why-we-need-more-businesses-to-go-bust",
        label="MULTI-2 Rest is Money (8sp)", duration_category="normal"),
    EpisodeSpec(index=17, podcast_slug="tool-use-ai-conversations",
        episode_slug="advanced-claude-code-part-2-ft-eric-buess",
        label="NORM-10 Tool Use/Claude Code", duration_category="normal"),
    EpisodeSpec(index=18, podcast_slug="product-therapy",
        episode_slug="coaching-roadmaps",
        label="NORM-11 Product Therapy/Roadmaps", duration_category="normal"),
    EpisodeSpec(index=19, podcast_slug="the-prof-g-pod-with-scott-galloway",
        episode_slug="raging-moderates-trump-pulls-back-in-minneapolis-as-democrat",
        label="MULTI-3 Prof G Pod (7sp)", duration_category="normal"),
]
# fmt: on


def find_audio_file(ep: EpisodeSpec, data_root: Path) -> Optional[Path]:
    """Find downsampled audio WAV for an episode."""
    audio_dir = data_root / "downsampled_audio" / ep.podcast_slug
    matches = list(audio_dir.glob(f"{ep.episode_slug}*.wav"))
    return matches[0] if matches else None


def find_existing_transcript(ep: EpisodeSpec, data_root: Path, model_prefix: str) -> Optional[Path]:
    """Find an existing raw transcript produced by a specific model/provider.

    Args:
        model_prefix: prefix to match against the model_used field (e.g. "dalston", "scribe_v1")
    """
    raw_dir = data_root / "raw_transcripts" / ep.podcast_slug
    for path in raw_dir.glob(f"{ep.episode_slug}*_transcript.json"):
        try:
            with open(path) as f:
                data = json.load(f)
            model_used = data.get("model_used", "")
            if model_used.startswith(model_prefix):
                return path
        except (json.JSONDecodeError, OSError):
            continue
    return None
