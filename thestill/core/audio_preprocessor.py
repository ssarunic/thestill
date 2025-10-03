"""
Audio preprocessing module for optimizing audio files before transcription.

This module detects high-quality audio files and downsamples them to optimal
settings for Whisper/Parakeet transcription models (16kHz, 16-bit, mono).
"""

import os
from pathlib import Path
from typing import Optional, Tuple
from pydub import AudioSegment


class AudioPreprocessor:
    """Preprocesses audio files for optimal transcription performance"""

    # Optimal settings for Whisper/Parakeet models
    TARGET_SAMPLE_RATE = 16000  # 16kHz
    TARGET_SAMPLE_WIDTH = 2      # 16-bit (2 bytes)
    TARGET_CHANNELS = 1          # Mono

    def __init__(self, logger=None):
        """
        Initialize the audio preprocessor.

        Args:
            logger: Optional logger instance for output
        """
        self.logger = logger

    def _log(self, message: str):
        """Log a message using logger if available, otherwise print"""
        if self.logger:
            self.logger.info(message)
        else:
            print(message)

    def needs_preprocessing(self, audio_path: str) -> Tuple[bool, dict]:
        """
        Check if audio file needs preprocessing.

        Args:
            audio_path: Path to the audio file

        Returns:
            Tuple of (needs_preprocessing: bool, metadata: dict)
        """
        try:
            audio = AudioSegment.from_file(audio_path)

            metadata = {
                'sample_rate': audio.frame_rate,
                'sample_width': audio.sample_width,
                'channels': audio.channels,
                'duration_seconds': len(audio) / 1000.0,
                'format': Path(audio_path).suffix.lstrip('.')
            }

            # Check if any parameter exceeds optimal settings
            needs_processing = (
                audio.frame_rate > self.TARGET_SAMPLE_RATE or
                audio.sample_width > self.TARGET_SAMPLE_WIDTH or
                audio.channels > self.TARGET_CHANNELS
            )

            return needs_processing, metadata

        except Exception as e:
            self._log(f"Error analyzing audio file: {e}")
            return False, {}

    def clip_audio(self, audio_path: str, duration_seconds: int, output_suffix: str = "_clipped") -> Optional[str]:
        """
        Clip audio file to specified duration for testing/debugging.

        Args:
            audio_path: Path to the original audio file
            duration_seconds: Number of seconds to keep from the start
            output_suffix: Suffix to add to the clipped file

        Returns:
            Path to clipped file, or None if clipping failed
        """
        try:
            self._log(f"Clipping audio to {duration_seconds} seconds for testing...")

            # Load audio
            audio = AudioSegment.from_file(audio_path)

            # Get duration in milliseconds
            duration_ms = duration_seconds * 1000
            original_duration_s = len(audio) / 1000.0

            # Clip to specified duration
            clipped_audio = audio[:duration_ms]

            # Generate output path
            input_path = Path(audio_path)
            output_path = input_path.parent / f"{input_path.stem}{output_suffix}{input_path.suffix}"

            # Export in same format as input
            self._log(f"Saving clipped audio to: {output_path.name}")
            clipped_audio.export(str(output_path), format=input_path.suffix.lstrip('.'))

            self._log(f"Clipping complete! Duration reduced from {original_duration_s:.1f}s to {duration_seconds}s")

            return str(output_path)

        except Exception as e:
            self._log(f"Error clipping audio: {e}")
            return None

    def preprocess_audio(self, audio_path: str, output_suffix: str = "_preprocessed") -> Optional[str]:
        """
        Preprocess audio file by downsampling to optimal settings.

        Args:
            audio_path: Path to the original audio file
            output_suffix: Suffix to add to the preprocessed file

        Returns:
            Path to preprocessed file, or None if preprocessing failed
        """
        try:
            # Check if preprocessing is needed
            needs_processing, metadata = self.needs_preprocessing(audio_path)

            if not needs_processing:
                self._log(f"Audio file already optimized (Sample rate: {metadata.get('sample_rate', 'unknown')}Hz, "
                         f"Bit depth: {metadata.get('sample_width', 'unknown') * 8}-bit, "
                         f"Channels: {metadata.get('channels', 'unknown')})")
                return audio_path

            original_size = os.path.getsize(audio_path)
            self._log(f"Preprocessing audio file (Original: {metadata.get('sample_rate')}Hz, "
                     f"{metadata.get('sample_width') * 8}-bit, "
                     f"{metadata.get('channels')} channel(s), "
                     f"{original_size / (1024 * 1024):.2f} MB)")

            # Load audio
            audio = AudioSegment.from_file(audio_path)

            # Convert to mono if stereo
            if audio.channels > 1:
                self._log(f"Converting from {audio.channels} channels to mono...")
                audio = audio.set_channels(self.TARGET_CHANNELS)

            # Downsample if needed
            if audio.frame_rate > self.TARGET_SAMPLE_RATE:
                self._log(f"Downsampling from {audio.frame_rate}Hz to {self.TARGET_SAMPLE_RATE}Hz...")
                audio = audio.set_frame_rate(self.TARGET_SAMPLE_RATE)

            # Set sample width if needed
            if audio.sample_width > self.TARGET_SAMPLE_WIDTH:
                self._log(f"Converting from {audio.sample_width * 8}-bit to {self.TARGET_SAMPLE_WIDTH * 8}-bit...")
                audio = audio.set_sample_width(self.TARGET_SAMPLE_WIDTH)

            # Generate output path
            input_path = Path(audio_path)
            output_path = input_path.parent / f"{input_path.stem}{output_suffix}.wav"

            # Export as WAV (lossless, widely compatible)
            self._log(f"Saving preprocessed audio to: {output_path.name}")
            audio.export(
                str(output_path),
                format="wav",
                parameters=[
                    "-ar", str(self.TARGET_SAMPLE_RATE),
                    "-ac", str(self.TARGET_CHANNELS),
                    "-sample_fmt", "s16"  # 16-bit signed integer
                ]
            )

            # Report size reduction
            new_size = os.path.getsize(str(output_path))
            size_reduction = ((original_size - new_size) / original_size) * 100

            self._log(f"Preprocessing complete! New size: {new_size / (1024 * 1024):.2f} MB "
                     f"(Reduced by {size_reduction:.1f}%)")
            self._log(f"Optimized settings: {self.TARGET_SAMPLE_RATE}Hz, "
                     f"{self.TARGET_SAMPLE_WIDTH * 8}-bit, mono")

            return str(output_path)

        except Exception as e:
            self._log(f"Error preprocessing audio: {e}")
            return None

    def cleanup_preprocessed_file(self, file_path: str):
        """
        Remove a preprocessed audio file.

        Args:
            file_path: Path to the preprocessed file to remove
        """
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                self._log(f"Cleaned up preprocessed file: {Path(file_path).name}")
        except Exception as e:
            self._log(f"Error cleaning up preprocessed file: {e}")
