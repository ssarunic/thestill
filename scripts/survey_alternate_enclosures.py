#!/usr/bin/env python
"""Survey the top-500 feeds for video <podcast:alternateEnclosure> adoption.

Re-fetches every RSS feed from a prior survey snapshot, recomputes video
alt-enclosure stats with the same parsing the production pipeline uses
(``ET.fromstring`` + ``.//item`` + the podcastindex namespace), writes a fresh
snapshot, and diffs it against the baseline to surface feeds that *started* (or
stopped) publishing video links.

Usage:
    ./venv/bin/python scripts/survey_alternate_enclosures.py \
        --baseline data/alternate_enclosure_survey.json \
        --out data/alternate_enclosure_survey_2026-05-22.json
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests

PODCAST_NS = {"podcast": "https://podcastindex.org/namespace/1.0"}
# Browser-ish UA: some podcast hosts (Buzzsprout etc.) block unknown agents.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT = 30
WORKERS = 24

# Carry these straight from the baseline entry (stable identity / source metadata).
CARRY_FIELDS = (
    "apple_url",
    "artist",
    "name",
    "primary_genre",
    "rss_url",
    "source_genre",
    "source_rank",
    "track_id",
)


def is_video_mime(mime: Optional[str]) -> bool:
    """Match the baseline: ``video/*`` plus HLS playlists count as video."""
    if not mime:
        return False
    mime = mime.lower().strip()
    return mime.startswith("video/") or mime in {
        "application/x-mpegurl",
        "application/vnd.apple.mpegurl",
    }


def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def survey_feed(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch one feed and recompute its alt-enclosure stats."""
    result: Dict[str, Any] = {k: entry.get(k) for k in CARRY_FIELDS}
    result.update(
        episode_count=0,
        episodes_with_alt=0,
        alt_enclosure_count=0,
        video_alt_count=0,
        has_default_video=False,
        max_height=None,
        mime_types={},
        sample_urls=[],
        fetch_error=None,
    )

    rss_url = entry.get("rss_url")
    if not rss_url:
        result["fetch_error"] = "no_rss_url"
        return result

    try:
        resp = requests.get(
            rss_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        result["fetch_error"] = f"request_error:{type(exc).__name__}"
        return result

    if resp.status_code != 200:
        result["fetch_error"] = f"http_{resp.status_code}"
        return result

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        result["fetch_error"] = f"parse_error:{exc}"
        return result

    mime_types: Dict[str, int] = {}
    sample_urls: List[Dict[str, Any]] = []
    max_height: Optional[int] = None

    for item in root.findall(".//item"):
        result["episode_count"] += 1
        alts = item.findall("podcast:alternateEnclosure", PODCAST_NS)
        episode_has_video = False

        for alt in alts:
            mime = alt.get("type")
            if not is_video_mime(mime):
                continue

            result["alt_enclosure_count"] += 1
            result["video_alt_count"] += 1
            episode_has_video = True
            key = (mime or "").lower().strip()
            mime_types[key] = mime_types.get(key, 0) + 1

            default = alt.get("default")
            if default and default.lower() == "true":
                result["has_default_video"] = True

            height = _int_or_none(alt.get("height"))
            if height is not None and (max_height is None or height > max_height):
                max_height = height

            sources = alt.findall("podcast:source", PODCAST_NS)
            if len(sample_urls) < 3:
                sample_urls.append(
                    {
                        "mime_type": key,
                        "source_uri": sources[0].get("uri") if sources else None,
                        "source_count": len(sources),
                        "height": height,
                        "bitrate": _int_or_none(alt.get("bitrate")),
                        "default": default,
                    }
                )

        if episode_has_video:
            result["episodes_with_alt"] += 1

    result["mime_types"] = mime_types
    result["sample_urls"] = sample_urls
    result["max_height"] = max_height
    return result


def run_survey(baseline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    done = 0
    total = len(baseline)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(survey_feed, e): e for e in baseline}
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 25 == 0 or done == total:
                ok = sum(1 for r in results if not r["fetch_error"])
                vid = sum(1 for r in results if r["video_alt_count"] > 0)
                print(f"  ...{done}/{total} fetched (ok={ok}, video={vid})", file=sys.stderr, flush=True)
    # Preserve baseline ordering (by source_rank) for a stable, comparable file.
    results.sort(key=lambda r: (r.get("source_rank") is None, r.get("source_rank")))
    return results


def diff(baseline: List[Dict[str, Any]], fresh: List[Dict[str, Any]]) -> None:
    old = {p["track_id"]: p for p in baseline}
    new = {p["track_id"]: p for p in fresh}

    def has_video(p: Dict[str, Any]) -> bool:
        return bool(p) and (p.get("video_alt_count", 0) or 0) > 0

    newcomers = [tid for tid in new if has_video(new[tid]) and not has_video(old.get(tid, {}))]
    dropouts = [tid for tid in old if has_video(old[tid]) and not has_video(new.get(tid, {}))]

    print("\n" + "=" * 60)
    print("DIFF vs baseline")
    print("=" * 60)

    print(f"\nNEWCOMERS — started publishing video since baseline ({len(newcomers)}):")
    for tid in sorted(newcomers, key=lambda t: new[t].get("source_rank") or 9999):
        p = new[tid]
        print(
            f"  rank#{p.get('source_rank')} | {p['name']} | "
            f"{p['episodes_with_alt']}/{p['episode_count']} eps | {list(p['mime_types'])}"
        )

    print(f"\nDROPOUTS — no longer publishing video ({len(dropouts)}):")
    for tid in sorted(dropouts, key=lambda t: old[t].get("source_rank") or 9999):
        p = old[tid]
        print(f"  rank#{p.get('source_rank')} | {p['name']} (was {p['episodes_with_alt']}/{p['episode_count']})")

    print("\nADOPTION-RATIO CHANGES (feeds with video in both snapshots):")
    for tid in sorted(new, key=lambda t: new[t].get("source_rank") or 9999):
        n = new[tid]
        o = old.get(tid, {})
        if has_video(n) and has_video(o):
            no = n["episodes_with_alt"]
            oo = o.get("episodes_with_alt", 0)
            if no != oo:
                print(f"  {n['name']}: {oo} -> {no} eps with video (Δ{no - oo:+d})")

    # New fetch failures that previously worked (could mask a real change).
    new_failures = [tid for tid in new if new[tid]["fetch_error"] and not old.get(tid, {}).get("fetch_error")]
    if new_failures:
        print(f"\nNEW FETCH FAILURES ({len(new_failures)}) — could hide a change:")
        for tid in new_failures:
            p = new[tid]
            print(f"  rank#{p.get('source_rank')} | {p['name']} | {p['fetch_error']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="data/alternate_enclosure_survey.json")
    ap.add_argument("--out", default="data/alternate_enclosure_survey_2026-05-22.json")
    args = ap.parse_args()

    with open(args.baseline) as fh:
        baseline = json.load(fh)

    print(f"Re-surveying {len(baseline)} feeds (workers={WORKERS}, timeout={TIMEOUT}s)...", file=sys.stderr)
    fresh = run_survey(baseline)

    with open(args.out, "w") as fh:
        json.dump(fresh, fh, indent=2)
    print(f"Wrote {args.out}", file=sys.stderr)

    ok = sum(1 for r in fresh if not r["fetch_error"])
    vid = sum(1 for r in fresh if r["video_alt_count"] > 0)
    print(f"\nFresh snapshot: {ok}/{len(fresh)} fetched OK, {vid} with video alt-enclosures")

    diff(baseline, fresh)


if __name__ == "__main__":
    main()
