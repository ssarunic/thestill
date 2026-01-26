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

"""
Device resolution utilities for compute device selection.

Provides functions to detect and select appropriate compute devices
(CUDA, MPS, CPU) for machine learning workloads.
"""

from typing import Optional, Tuple

import torch

from thestill.utils.console import ConsoleOutput


def is_cuda_available() -> bool:
    """Check if CUDA (NVIDIA GPU) is available."""
    return torch.cuda.is_available()


def is_mps_available() -> bool:
    """Check if MPS (Apple Metal) is available."""
    return hasattr(torch.backends, "mps") and torch.backends.mps.is_available()


def resolve_device(device: str) -> str:
    """
    Resolve 'auto' device to actual device (cuda/cpu).

    Note: MPS is not returned for 'auto' because some models have
    compatibility issues with Metal Performance Shaders.

    Args:
        device: Device string - 'auto', 'cuda', 'mps', or 'cpu'

    Returns:
        Resolved device string
    """
    if device == "auto":
        if is_cuda_available():
            return "cuda"
        # MPS has issues with some models, default to CPU for safety
        return "cpu"
    return device


def resolve_hybrid_devices(
    device: str, verbose: bool = False, console: Optional[ConsoleOutput] = None
) -> Tuple[str, str, str]:
    """
    Resolve device for multi-stage pipelines (transcription, alignment, diarization).

    On Mac with MPS available:
    - Transcription: CPU (Faster-Whisper/CTranslate2 has MPS issues)
    - Alignment: MPS (Wav2Vec2 works well with Metal)
    - Diarization: MPS (pyannote benefits from GPU parallelism)

    On CUDA systems: all stages use CUDA.
    On CPU-only systems: all stages use CPU.

    Args:
        device: Device string - 'auto', 'cuda', 'mps', or 'cpu'
        verbose: If True, print device selection messages (deprecated, use console instead)
        console: ConsoleOutput instance for user-facing messages (optional)

    Returns:
        Tuple of (transcription_device, alignment_device, diarization_device)
    """
    cuda_available = is_cuda_available()
    mps_available = is_mps_available()

    # Use console if provided, otherwise fall back to verbose print
    output = console if console else (ConsoleOutput() if verbose else None)

    if device == "auto":
        if cuda_available:
            return ("cuda", "cuda", "cuda")
        elif mps_available:
            if output:
                output.info(
                    "🍎 Mac detected: using hybrid device strategy "
                    "(CPU for transcription, MPS for alignment/diarization)"
                )
            return ("cpu", "mps", "mps")
        return ("cpu", "cpu", "cpu")

    elif device == "mps":
        if mps_available:
            if output:
                output.info(
                    "🍎 MPS requested: using hybrid device strategy "
                    "(CPU for transcription, MPS for alignment/diarization)"
                )
            return ("cpu", "mps", "mps")
        if output:
            output.warning("MPS requested but not available, falling back to CPU")
        return ("cpu", "cpu", "cpu")

    elif device == "cuda":
        if cuda_available:
            return ("cuda", "cuda", "cuda")
        if output:
            output.warning("CUDA requested but not available, falling back to CPU")
        return ("cpu", "cpu", "cpu")

    # Explicit device (e.g., "cpu")
    return (device, device, device)
