"""Build the audio fixtures of tests/drift-alignment.spec.ts (spec #116).

Two copies of one "episode", as a host with dynamic ad insertion serves them:

- ``transcribed.mp3``: the copy that was transcribed.
      episode 0-20 s | ad X (6 s) | episode 20-40 s             = 46 s
- ``listener.mp3``: what a listener is served later.
      pre-roll (5 s) | episode 0-20 s | ad Y (10 s) | episode 20-40 s = 55 s

So transcript time 0-20 s plays 5 s later in the listener's copy and
transcript time 26-46 s plays 9 s later. ``track.sync`` is the sync track of
``transcribed.mp3``, built by the same code the pipeline runs.

The audio is pink noise (every frame distinct, so every second gets a
landmark), encoded once and spliced on frame boundaries without re-encoding,
which is what makes the episode's frames byte-identical in both copies. The
bit reservoir is off so a splice does not corrupt the frames after it.

Run from the repository root (needs ffmpeg):

    ./venv/bin/python thestill/web/frontend/tests/fixtures/drift/make_fixture.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[5]))

from thestill.core.sync_track import build_sync_track, iter_mp3_frames  # noqa: E402

FRAME_S = 576 / 16000  # MPEG-2 Layer III at 16 kHz


def encode(seed: int, seconds: int, out: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:r=16000:a=0.3:seed={seed}",
            "-ac", "1", "-c:a", "libmp3lame", "-b:a", "16k", "-reservoir", "0",
            "-write_xing", "0", "-id3v2_version", "0", "-map_metadata", "-1",
            str(out),
        ],
        check=True,
    )  # fmt: skip


def frames(path: Path) -> list[bytes]:
    return [frame.data for frame in iter_mp3_frames([path.read_bytes()])]


def seconds(n: float) -> int:
    return round(n / FRAME_S)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        encode(1, 41, work / "episode.mp3")
        encode(2, 22, work / "ads.mp3")
        episode = frames(work / "episode.mp3")
        ads = frames(work / "ads.mp3")

    first, second = episode[: seconds(20)], episode[seconds(20) : seconds(40)]
    ad_x = ads[: seconds(6)]
    ad_y = ads[seconds(6) : seconds(16)]
    pre_roll = ads[seconds(16) : seconds(21)]

    transcribed = b"".join(first + ad_x + second)
    listener = b"".join(pre_roll + first + ad_y + second)
    (HERE / "transcribed.mp3").write_bytes(transcribed)
    (HERE / "listener.mp3").write_bytes(listener)

    track = build_sync_track([transcribed], "e2e")
    (HERE / "track.sync").write_bytes(track.to_bytes())
    print(
        f"transcribed {len(transcribed)} B, listener {len(listener)} B, "
        f"track {len(track.anchors)} anchors over {track.duration_s:.2f} s"
    )


if __name__ == "__main__":
    main()
