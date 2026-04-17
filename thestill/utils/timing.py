# Copyright 2025-2026 Thestill
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
Timing utilities for structured performance instrumentation.

Emits structlog `feed_phase_timing` events that can be aggregated with jq or
pandas to measure per-phase duration across refresh runs.
"""

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator

from structlog import get_logger

logger = get_logger(__name__)


@contextmanager
def log_phase_timing(phase: str, event: str = "feed_phase_timing", **context: Any) -> Iterator[Dict[str, Any]]:
    """
    Measure wall-clock duration of a code block and emit a structured log event.

    The yielded dict can be mutated inside the `with` block to attach
    result-dependent context (e.g. bytes fetched, episode counts) that is
    only known after the measured work completes.

    Args:
        phase: Short phase label (e.g. "http_fetch", "parse", "persist").
        event: Structlog event name. Defaults to "feed_phase_timing".
        **context: Initial keyword context attached to the emitted event.

    Yields:
        Dict of context that will be emitted with the event. Mutate it to
        add post-hoc fields before the block exits.

    Example:
        with log_phase_timing("http_fetch", podcast_slug=slug) as ctx:
            resp = requests.get(url)
            ctx["status_code"] = resp.status_code
            ctx["bytes"] = len(resp.content)
    """
    ctx: Dict[str, Any] = dict(context)
    start = time.perf_counter()
    try:
        yield ctx
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        # Callers may override the phase label via ``ctx["phase"]`` — pop
        # before logging so the kwarg doesn't collide with ``phase=...``.
        emitted_phase = ctx.pop("phase", phase)
        logger.info(event, phase=emitted_phase, duration_ms=duration_ms, **ctx)
