# Copyright 2025 thestill.me
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

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

import torch
from pydub import AudioSegment


class ParakeetTranscriber:
    """
    Transcriber using NVIDIA's Parakeet model for speech-to-text.
    Provides an interface compatible with WhisperTranscriber.
    """

    def __init__(self, device: str = "auto"):
        self.model_name = "nvidia/parakeet-tdt-1.1b"
        self.device = self._resolve_device(device)
        self._model = None
        self._processor = None

    def _resolve_device(self, device: str) -> str:
        """Resolve 'auto' device to actual device"""
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
            else:
                return "cpu"
        return device

    def load_model(self):
        """Lazy load the Parakeet model"""
        if self._model is None:
            try:
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

                print(f"Loading Parakeet model: {self.model_name}")
                self._processor = AutoProcessor.from_pretrained(self.model_name)
                self._model = AutoModelForSpeechSeq2Seq.from_pretrained(
                    self.model_name,
                    torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                    low_cpu_mem_usage=True,
                )
                self._model.to(self.device)
                print("Model loaded successfully")
            except ImportError:
                raise ImportError(
                    "Parakeet requires the transformers library. " "Install with: pip install transformers"
                )
            except Exception as e:
                print(f"Error loading Parakeet model: {e}")
                raise

    def transcribe_audio(
        self, audio_path: str, output_path: str = None, custom_prompt: str = None, preprocess_audio: bool = False
    ) -> Optional[Dict]:
        """
        Transcribe audio file using Parakeet model.

        Note: Parakeet doesn't support custom prompts or word-level timestamps
        like Whisper does. These parameters are accepted for API compatibility.
        """
        try:
            self.load_model()

            print(f"Starting transcription of: {Path(audio_path).name}")
            start_time = time.time()

            audio_duration = self._get_audio_duration(audio_path)
            print(f"Audio duration: {audio_duration:.1f} minutes")

            # Load and preprocess audio
            import librosa

            audio_array, sample_rate = librosa.load(audio_path, sr=16000, mono=True)

            # Process audio in chunks if too long (Parakeet has input length limits)
            max_duration = 30  # seconds
            chunk_size = max_duration * sample_rate

            if len(audio_array) > chunk_size:
                transcripts = []
                num_chunks = int(len(audio_array) / chunk_size) + 1

                for i in range(num_chunks):
                    start = i * chunk_size
                    end = min((i + 1) * chunk_size, len(audio_array))
                    chunk = audio_array[start:end]

                    if len(chunk) > 0:
                        chunk_text = self._transcribe_chunk(chunk, sample_rate)
                        if chunk_text:
                            transcripts.append(chunk_text)

                full_text = " ".join(transcripts)
            else:
                full_text = self._transcribe_chunk(audio_array, sample_rate)

            processing_time = time.time() - start_time
            print(f"Transcription completed in {processing_time:.1f} seconds")

            transcript_data = self._format_transcript(full_text, processing_time, audio_path)

            if output_path:
                self._save_transcript(transcript_data, output_path)

            return transcript_data

        except Exception as e:
            print(f"Error transcribing {audio_path}: {e}")
            return None

    def _transcribe_chunk(self, audio_array, sample_rate: int) -> str:
        """Transcribe a single audio chunk"""
        inputs = self._processor(audio_array, sampling_rate=sample_rate, return_tensors="pt").to(self.device)

        with torch.no_grad():
            generated_ids = self._model.generate(**inputs)

        transcription = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]

        return transcription.strip()

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in minutes"""
        try:
            audio = AudioSegment.from_file(audio_path)
            return len(audio) / (1000 * 60)  # Convert ms to minutes
        except:
            return 0.0

    def _format_transcript(self, text: str, processing_time: float, audio_path: str) -> Dict:
        """
        Format Parakeet output into a structure compatible with Whisper's output.
        Note: Parakeet doesn't provide word-level timestamps or segments like Whisper.
        """
        return {
            "audio_file": audio_path,
            "language": "en",
            "text": text,
            "segments": [
                {"id": 0, "start": 0.0, "end": 0.0, "text": text, "words": []}  # No timestamp information available
            ],
            "processing_time": processing_time,
            "model_used": self.model_name,
            "timestamp": time.time(),
        }

    def _save_transcript(self, transcript_data: Dict, output_path: str):
        """Save transcript to JSON file"""
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(transcript_data, f, indent=2, ensure_ascii=False)
            print(f"Transcript saved to: {output_path}")
        except Exception as e:
            print(f"Error saving transcript: {e}")

    def get_transcript_text(self, transcript_data: Dict) -> str:
        """
        Extract plain text from transcript data.
        Compatible with WhisperTranscriber interface.
        """
        if not transcript_data:
            return ""

        # For Parakeet, we just return the text directly since we don't have timestamps
        return transcript_data.get("text", "")

    def estimate_processing_time(self, audio_duration_minutes: float) -> float:
        """Estimate transcription time based on audio duration"""
        # Parakeet is typically faster than Whisper on GPU
        if self.device == "cuda":
            ratio = 0.08  # ~8% of audio length on GPU
        else:
            ratio = 0.2  # ~20% of audio length on CPU

        return audio_duration_minutes * ratio

    def generate_prompt_from_podcast_info(self, podcast_title: str, episode_title: str = "") -> str:
        """
        Parakeet doesn't support custom prompts like Whisper does.
        This method is provided for API compatibility but returns empty string.
        """
        return ""
