# Profiling `thestill refresh`

This page explains how to collect hard data about where `thestill refresh`
spends its time. Use it when evaluating the phase-1/2 optimizations in
[spec #19](../specs/19-refresh-performance.md).

## What gets measured

Refresh emits three kinds of structured events. All events go through
`structlog`, so set `LOG_FORMAT=json` to make them pipeable.

### 1. `feed_phase_timing`

One event per measured block. Fields:

| Field                  | Source                                                     | Notes                                  |
|------------------------|------------------------------------------------------------|----------------------------------------|
| `phase`                | `http_fetch`, `conditional_get_hit`, `parse`, `persist_batch` | Which block of work                 |
| `duration_ms`          | Wall clock                                                 | Float, 2-decimal ms                    |
| `podcast_slug`         | For `http_fetch` / `conditional_get_hit` only              | Join key (not on `parse` or `persist_batch`) |
| `url`                  | For `http_fetch` / `conditional_get_hit` / `parse`         | Full feed URL                          |
| `bytes`                | For `http_fetch` (200 responses) and `parse`               | Response body size                     |
| `status_code`          | For `http_fetch` / `conditional_get_hit`                   | HTTP response code                     |
| `conditional`          | For `http_fetch` / `conditional_get_hit`                   | `True` when cache validators were sent |
| `entries`              | For `parse`                                                | Feedparser entry count                 |
| `podcasts`             | For `persist_batch`                                        | Changed podcasts in the batch          |
| `new_episodes`         | For `persist_batch`                                        | New episode rows in the batch          |
| `image_updates`        | For `persist_batch`                                        | Episode image updates in the batch     |
| `audio_url_updates`    | For `persist_batch`                                        | Episode audio-URL updates in the batch |
| `alternate_enclosures` | For `persist_batch`                                        | Alternate-enclosure rows in the batch  |
| `error`                | For failed `http_fetch`                                    | Exception repr                         |
| `failure_kind`         | For failed `http_fetch`                                    | Structured failure classification      |

The `conditional_get_hit` phase is the same measured block as
`http_fetch`: when a conditional GET returns HTTP 304, the phase label is
`conditional_get_hit` instead of `http_fetch`, and the `parse` phase is
skipped entirely for that podcast.

`persist_batch` wraps the single batch-wide transaction that saves all
changed podcasts at the end of a refresh, so it is emitted once per
refresh invocation (when there is anything to save), not once per
podcast — hence no `podcast_slug`.

After the parse-once refactor, each RSS podcast emits exactly one
`http_fetch` **or** one `conditional_get_hit` per refresh, and one
`parse` only in the `http_fetch` case. If you see more than one
fetch-or-hit event, or more than one `parse`, per podcast in a run,
something has regressed.

### 2. `feed_refresh_summary`

One event per podcast refreshed. Emitted from the `finally` block of
`PodcastFeedManager._refresh_single_podcast`, so it fires regardless of
success or failure.

| Field                 | Notes                                           |
|-----------------------|-------------------------------------------------|
| `podcast_slug`        | Join key                                        |
| `source_type`         | `RSSMediaSource`, `YouTubeMediaSource`, …       |
| `duration_ms`         | Full per-podcast wall time                      |
| `new_episodes`        | Newly discovered episodes                       |
| `had_error`           | `True` if the feed raised                       |
| `failure_kind`        | Structured failure classification, `null` on success |
| `conditional_get_hit` | `True` if the feed answered 304 Not Modified    |

### 3. `feed_refresh_batch_summary`

One event per `refresh` invocation. End-to-end totals.

| Field                          | Notes                                  |
|--------------------------------|----------------------------------------|
| `duration_ms`                  | Full batch wall time                   |
| `total_podcasts`               | Podcasts processed                     |
| `podcasts_with_new_episodes`   | Subset with ≥1 new episode             |
| `total_new_episodes`           | Sum of new episodes                    |
| `podcasts_with_errors`         | Subset that raised                     |
| `conditional_get_hits`         | Subset that answered 304 Not Modified  |
| `max_workers`                  | Refresh worker-pool size for the run   |

## Running a profiled refresh

### Quick check (structured logs only)

```bash
LOG_FORMAT=json LOG_LEVEL=INFO \
    ./venv/bin/thestill refresh 2>&1 \
    | tee refresh.log.ndjson
```

### Inspect with `jq`

Per-podcast wall time, slowest first:

```bash
jq -r 'select(.event == "feed_refresh_summary")
       | [.duration_ms, .podcast_slug, .new_episodes, .had_error]
       | @tsv' refresh.log.ndjson \
    | sort -k1 -rn | head -20
```

HTTP-fetch duration distribution (304s are excluded automatically —
they emit `conditional_get_hit` instead of `http_fetch`, so they leave
the filter; use `.phase == "conditional_get_hit"` to look at them
separately):

```bash
jq -r 'select(.event == "feed_phase_timing" and .phase == "http_fetch")
       | .duration_ms' refresh.log.ndjson \
    | sort -n | awk '
        { a[NR]=$1 }
        END {
            print "count:", NR
            print "p50:  ", a[int(NR*0.50)]
            print "p95:  ", a[int(NR*0.95)]
            print "max:  ", a[NR]
        }'
```

Verify each podcast emits exactly one fetch event per refresh — either
one `http_fetch` or, on a 304, one `conditional_get_hit` with no `parse`
(sanity check that the parse-once refactor still holds):

```bash
jq -r 'select(.event == "feed_phase_timing") | [.podcast_slug, .phase] | @tsv' \
    refresh.log.ndjson | sort | uniq -c | sort -rn | head
```

Batch summary:

```bash
jq 'select(.event == "feed_refresh_batch_summary")' refresh.log.ndjson
```

### Aggregate in pandas (optional)

```python
import pandas as pd

df = pd.read_json("refresh.log.ndjson", lines=True)

phases = df[df.event == "feed_phase_timing"]
phases.groupby("phase")["duration_ms"].describe()

summary = df[df.event == "feed_refresh_summary"]
summary.sort_values("duration_ms", ascending=False).head(10)
```

## Deeper profile with `pyinstrument`

Use when the structured data points at a phase but you need a flamegraph
to see where *inside* that phase the time goes (e.g. `feedparser.parse`
vs SQLite commit vs `requests` internals). `pyinstrument` reports
wall-clock and handles I/O wait correctly, which `cProfile` does not.

```bash
./venv/bin/pip install pyinstrument
./venv/bin/python -m pyinstrument \
    -o refresh.html --renderer html \
    -m thestill refresh
```

Open `refresh.html` and look for:

- **`socket.recv` / `ssl.read`** — network time. If this dominates,
  phase-1 (parallelization, conditional GET) is the right response.
- **`feedparser.parse`** — XML parse time. Bigger feeds (huge archives)
  can surprise here.
- **`sqlite3.Connection.commit`** — per-podcast commits accumulating.
  Phase-2 (batch DB writes) is the right response.

## HTTP-level breakdown (optional)

If `http_fetch` is dominant but you want to separate DNS, connect, TTFB,
and download, enable `urllib3` debug logging once:

```python
import logging
logging.getLogger("urllib3").setLevel(logging.DEBUG)
```

Or monkeypatch `requests.get` to record `response.elapsed` plus
`response.raw._connection` timings. Usually the `http_fetch` duration is
good enough — only reach for this if something looks pathological.

## Interpreting the data

Expected shape of a healthy-but-unoptimized run:

- `feed_refresh_batch_summary.duration_ms` ≈ sum of per-podcast
  `duration_ms` (serial execution).
- `http_fetch` events per RSS podcast = 2 (double-fetch). This describes
  the historical pre-refactor state — current code fetches once per
  podcast (`media_source.py` `fetch_and_parse`), so today the expected
  count is exactly 1 (`http_fetch` or `conditional_get_hit`).
- `http_fetch` dominates `duration_ms` per podcast.
- `persist_batch` is small (tens of ms) and appears once per refresh.
- `parse` is small-to-moderate (tens of ms for small feeds, hundreds
  for huge archives).

If the data matches, phase-1 work from spec #19 is justified:

1. Parse-once refactor → halves `http_fetch` event count.
2. `ThreadPoolExecutor` → batch `duration_ms` drops roughly N× for pool
   size N (bounded by per-host cap).
3. Conditional GET → unchanged feeds return 304, emitting a near-zero
   `conditional_get_hit` phase event instead of `http_fetch` and
   skipping `parse` entirely for that podcast.
