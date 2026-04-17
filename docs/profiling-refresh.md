# Profiling `thestill refresh`

This page explains how to collect hard data about where `thestill refresh`
spends its time. Use it when evaluating the phase-1/2 optimizations in
[spec #19](../specs/19-refresh-performance.md).

## What gets measured

Refresh emits three kinds of structured events. All events go through
`structlog`, so set `LOG_FORMAT=json` to make them pipeable.

### 1. `feed_phase_timing`

One event per measured block. Fields:

| Field          | Source                                           | Notes                                  |
|----------------|--------------------------------------------------|----------------------------------------|
| `phase`        | `http_fetch`, `parse`, `persist`                 | Which block of work                    |
| `duration_ms`  | Wall clock                                       | Float, 2-decimal ms                    |
| `podcast_slug` | Only set where available                         | Join key                               |
| `url`          | For HTTP phases                                  | Full feed URL                          |
| `bytes`        | For HTTP / parse phases                          | Response body size                     |
| `status_code`  | For `http_fetch`                                 | HTTP response code                     |
| `entries`      | For `parse`                                      | Feedparser entry count                 |
| `episode_count`| For `persist`                                    | Episodes saved this call               |
| `error`        | For failed `http_fetch`                          | Exception repr                         |

After the parse-once refactor, each RSS podcast emits exactly one
`http_fetch` and one `parse` per refresh. If you see more than one of
either per podcast in a run, something has regressed.

### 2. `feed_refresh_summary`

One event per podcast refreshed. Emitted from `feed_manager.get_new_episodes`
regardless of success or failure.

| Field          | Notes                                           |
|----------------|-------------------------------------------------|
| `podcast_slug` | Join key                                        |
| `source_type`  | `RSSMediaSource`, `YouTubeMediaSource`, …       |
| `duration_ms`  | Full per-podcast wall time                      |
| `new_episodes` | Newly discovered episodes                       |
| `had_error`    | `True` if the feed raised                       |

### 3. `feed_refresh_batch_summary`

One event per `refresh` invocation. End-to-end totals.

| Field                          | Notes                                  |
|--------------------------------|----------------------------------------|
| `duration_ms`                  | Full batch wall time                   |
| `total_podcasts`               | Podcasts processed                     |
| `podcasts_with_new_episodes`   | Subset with ≥1 new episode             |
| `total_new_episodes`           | Sum of new episodes                    |
| `podcasts_with_errors`         | Subset that raised                     |

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

HTTP-fetch duration distribution:

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

Verify there's only one `http_fetch` per podcast per refresh (sanity
check that the parse-once refactor still holds):

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
- `http_fetch` events per RSS podcast = 2 (double-fetch).
- `http_fetch` dominates `duration_ms` per podcast.
- `persist` is small (tens of ms).
- `parse` is small-to-moderate (tens of ms for small feeds, hundreds
  for huge archives).

If the data matches, phase-1 work from spec #19 is justified:

1. Parse-once refactor → halves `http_fetch` event count.
2. `ThreadPoolExecutor` → batch `duration_ms` drops roughly N× for pool
   size N (bounded by per-host cap).
3. Conditional GET → unchanged feeds return 304 and `http_fetch`
   `duration_ms` drops to near-zero for them.
