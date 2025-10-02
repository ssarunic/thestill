"""
Evaluators for transcript quality and post-processing performance.
Uses LLM to generate structured evaluation reports.
"""

import json
from pathlib import Path
from typing import Dict
from .llm_provider import LLMProvider


class TranscriptEvaluator:
    """Evaluates the quality of raw ASR transcripts"""

    SYSTEM_PROMPT = """You are an evaluator of raw transcripts produced by automatic speech recognition (ASR) systems.
Analyse the transcript and return a structured JSON report following the schema below.

Schema:
{
  "accuracy": {
    "name_errors": {"count": "integer", "examples": ["string"]},
    "entity_errors": {"count": "integer", "examples": ["string"]},
    "word_errors": {"count": "integer", "examples": ["string"]},
    "faithfulness_issues": {"count": "integer", "examples": ["string"]}
  },
  "completeness": {
    "missing_sections": {"count": "integer", "examples": ["string"]}
  },
  "structure": {
    "ads_detected": "boolean",
    "intro_detected": "boolean",
    "outro_detected": "boolean",
    "speaker_turns_marked": "boolean",
    "timestamps_present": "boolean"
  },
  "scores": {
    "accuracy": "integer (0-10)",
    "completeness": "integer (0-10)",
    "entity_handling": "integer (0-10)",
    "structural_clarity": "integer (0-10)"
  },
  "summary": {
    "strengths": ["string"],
    "weaknesses": ["string"],
    "verdict": "string"
  }
}

Return ONLY valid JSON following this exact schema. Do not include any explanatory text before or after the JSON."""

    def __init__(self, provider: LLMProvider):
        """
        Initialize transcript evaluator with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI or Ollama)
        """
        self.provider = provider

    def evaluate(
        self,
        transcript_data: Dict,
        output_path: str = None
    ) -> Dict:
        """
        Evaluate the quality of a raw transcript.

        Args:
            transcript_data: The raw transcript JSON from transcriber
            output_path: Optional path to save the evaluation report

        Returns:
            Dict containing the structured evaluation report
        """
        print(f"Evaluating transcript quality with {self.provider.get_model_name()}...")

        transcript_json = json.dumps(transcript_data, indent=2)

        try:
            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": f"Evaluate this transcript:\n\n{transcript_json}"}
            ]

            evaluation_json = self.provider.chat_completion(
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"}
            )

            evaluation = json.loads(evaluation_json)

            # Save if output path provided
            if output_path:
                self._save_evaluation(evaluation, output_path)

            print("Transcript evaluation completed successfully")
            return evaluation

        except Exception as e:
            print(f"Error during transcript evaluation: {e}")
            raise

    def _save_evaluation(self, evaluation: Dict, output_path: str):
        """Save the evaluation report"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(evaluation, f, indent=2, ensure_ascii=False)

        print(f"Transcript evaluation saved to {output_path}")


class PostProcessorEvaluator:
    """Evaluates the quality of post-processed transcripts"""

    SYSTEM_PROMPT = """You are an evaluator of processed transcripts enhanced by an LLM.
Analyse the transcript and return a structured JSON report following the schema below.

Schema:
{
  "fidelity": {
    "meaning_preserved": "boolean",
    "invented_content": {"count": "integer", "examples": ["string"]},
    "name_entity_corrections": {"count": "integer", "examples": ["string"]}
  },
  "formatting": {
    "speaker_labels_clear": "boolean",
    "ads_marked": "boolean",
    "intro_marked": "boolean",
    "outro_marked": "boolean",
    "headings_present": "boolean",
    "timestamps_consistent": "boolean"
  },
  "enhancements": {
    "notable_quotes": {"count": "integer", "examples": ["string"]},
    "social_snippets": {"count": "integer", "examples": ["string"]},
    "markdown_used": "boolean"
  },
  "scores": {
    "fidelity": "integer (0-10)",
    "formatting_clarity": "integer (0-10)",
    "readability": "integer (0-10)",
    "enhancements_value": "integer (0-10)"
  },
  "summary": {
    "strengths": ["string"],
    "weaknesses": ["string"],
    "verdict": "string"
  }
}

Return ONLY valid JSON following this exact schema. Do not include any explanatory text before or after the JSON."""

    def __init__(self, provider: LLMProvider):
        """
        Initialize post-processor evaluator with an LLM provider.

        Args:
            provider: LLMProvider instance (OpenAI or Ollama)
        """
        self.provider = provider

    def evaluate(
        self,
        processed_content: Dict,
        original_transcript: Dict = None,
        output_path: str = None
    ) -> Dict:
        """
        Evaluate the quality of a post-processed transcript.

        Args:
            processed_content: The processed transcript from EnhancedPostProcessor
            original_transcript: Optional original transcript for comparison
            output_path: Optional path to save the evaluation report

        Returns:
            Dict containing the structured evaluation report
        """
        print(f"Evaluating post-processing quality with {self.provider.get_model_name()}...")

        # Build the user message
        user_message = f"Evaluate this processed transcript:\n\n{json.dumps(processed_content, indent=2)}"

        if original_transcript:
            user_message += f"\n\nOriginal transcript for comparison:\n\n{json.dumps(original_transcript, indent=2)}"

        try:
            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ]

            evaluation_json = self.provider.chat_completion(
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"}
            )

            evaluation = json.loads(evaluation_json)

            # Save if output path provided
            if output_path:
                self._save_evaluation(evaluation, output_path)

            print("Post-processing evaluation completed successfully")
            return evaluation

        except Exception as e:
            print(f"Error during post-processing evaluation: {e}")
            raise

    def _save_evaluation(self, evaluation: Dict, output_path: str):
        """Save the evaluation report"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(evaluation, f, indent=2, ensure_ascii=False)

        print(f"Post-processing evaluation saved to {output_path}")


def print_evaluation_summary(evaluation: Dict, eval_type: str = "transcript"):
    """Pretty print an evaluation summary to console"""
    print(f"\n{'='*60}")
    print(f"📊 {eval_type.upper()} EVALUATION REPORT")
    print(f"{'='*60}\n")

    if eval_type == "transcript":
        # Transcript evaluation format
        scores = evaluation.get("scores", {})
        print("Scores:")
        print(f"  Accuracy: {scores.get('accuracy', 0)}/10")
        print(f"  Completeness: {scores.get('completeness', 0)}/10")
        print(f"  Entity Handling: {scores.get('entity_handling', 0)}/10")
        print(f"  Structural Clarity: {scores.get('structural_clarity', 0)}/10")

        accuracy = evaluation.get("accuracy", {})
        print(f"\nAccuracy Issues:")
        print(f"  Name errors: {accuracy.get('name_errors', {}).get('count', 0)}")
        print(f"  Entity errors: {accuracy.get('entity_errors', {}).get('count', 0)}")
        print(f"  Word errors: {accuracy.get('word_errors', {}).get('count', 0)}")

    else:
        # Post-processor evaluation format
        scores = evaluation.get("scores", {})
        print("Scores:")
        print(f"  Fidelity: {scores.get('fidelity', 0)}/10")
        print(f"  Formatting Clarity: {scores.get('formatting_clarity', 0)}/10")
        print(f"  Readability: {scores.get('readability', 0)}/10")
        print(f"  Enhancements Value: {scores.get('enhancements_value', 0)}/10")

        enhancements = evaluation.get("enhancements", {})
        print(f"\nEnhancements:")
        print(f"  Notable quotes: {enhancements.get('notable_quotes', {}).get('count', 0)}")
        print(f"  Social snippets: {enhancements.get('social_snippets', {}).get('count', 0)}")

    summary = evaluation.get("summary", {})
    print(f"\nVerdict: {summary.get('verdict', 'N/A')}")

    if summary.get("strengths"):
        print(f"\nStrengths:")
        for strength in summary["strengths"]:
            print(f"  ✓ {strength}")

    if summary.get("weaknesses"):
        print(f"\nWeaknesses:")
        for weakness in summary["weaknesses"]:
            print(f"  ✗ {weakness}")

    print(f"\n{'='*60}\n")
