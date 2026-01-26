# Google Cloud Logging Query Examples

This guide provides query examples for analyzing thestill logs in Google Cloud Logging using the GCP log format.

## Prerequisites

- Logs ingested with `LOG_FORMAT=gcp`
- Google Cloud Project with Cloud Logging enabled
- Deployed on Cloud Run, GKE, or Compute Engine

## GCP Log Structure

Thestill logs use the following GCP Cloud Logging format:

```json
{
  "message": "Episode processed",
  "time": "2026-01-25T16:34:04.073930Z",
  "severity": "INFO",
  "episode_id": 123,
  "duration_ms": 4500,
  "worker_id": "worker-transcribe-2",
  "correlation_id": "episode-abc-123",
  "logging.googleapis.com/sourceLocation": {
    "file": "thestill/core/transcriber.py",
    "line": "142",
    "function": "transcribe"
  }
}
```

## Query Language

Google Cloud Logging uses a simplified query language. Access it via:

- **Console**: <https://console.cloud.google.com/logs>
- **gcloud CLI**: `gcloud logging read`
- **API**: Cloud Logging API

## Basic Queries

### All Logs for a Specific Episode

Trace all operations for a single episode:

```
jsonPayload.episode_id=123
```

**CLI Command:**

```bash
gcloud logging read "jsonPayload.episode_id=123" \
  --format=json \
  --freshness=7d \
  --order=asc
```

### All Logs from a Specific Worker

Debug what a specific worker is doing:

```
jsonPayload.worker_id="worker-a7f2"
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.worker_id="worker-a7f2"' \
  --format=json \
  --limit=100
```

### Error Logs Only

Find all errors in the system:

```
severity=ERROR
```

**CLI Command:**

```bash
gcloud logging read "severity=ERROR" \
  --format=json \
  --freshness=1d
```

### Logs from Specific Service

Filter by Cloud Run service or GKE deployment:

```
resource.type="cloud_run_revision"
resource.labels.service_name="thestill"
```

## Episode Tracing

### Complete Episode Journey

Trace an episode through the entire pipeline:

```
jsonPayload.correlation_id="episode-abc-123"
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.correlation_id="episode-abc-123"' \
  --format=json \
  --order=asc
```

**Expected log sequence:**

1. Download started
2. Download complete
3. Downsample started
4. Downsample complete
5. Transcribe started
6. Transcribe complete
7. Clean transcript started
8. Clean transcript complete
9. Summarize started
10. Summarize complete

### Episode Processing in Last Hour

Find recent episode processing:

```
jsonPayload.message="Episode processed"
timestamp>="2026-01-25T15:00:00Z"
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.message="Episode processed" AND timestamp>="2026-01-25T15:00:00Z"' \
  --format=json
```

## Failure Analysis

### All Transcription Failures

Find episodes that failed transcription:

```
severity=ERROR
jsonPayload.error_type="TranscriptionFailed"
```

**CLI Command:**

```bash
gcloud logging read 'severity=ERROR AND jsonPayload.error_type="TranscriptionFailed"' \
  --format=json \
  --freshness=7d
```

### Failed Episodes in Last 24 Hours

```
severity=ERROR
jsonPayload.error_type="TranscriptionFailed"
timestamp>="2026-01-24T16:00:00Z"
```

**CLI Command:**

```bash
gcloud logging read 'severity=ERROR AND jsonPayload.error_type="TranscriptionFailed" AND timestamp>="2026-01-24T16:00:00Z"' \
  --format=json
```

### Worker Retry Attempts

Find logs with retry attempts:

```
jsonPayload.attempt>1
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.attempt>1' \
  --format=json \
  --limit=50
```

## Performance Monitoring

### Slow Transcriptions

Find transcriptions taking longer than 5 minutes (300000ms):

```
jsonPayload.stage="transcribe"
jsonPayload.duration_ms>=300000
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.stage="transcribe" AND jsonPayload.duration_ms>=300000' \
  --format=json \
  --freshness=7d
```

### Recent Processing Times

Get processing durations for analysis:

```
jsonPayload.duration_ms>0
timestamp>="2026-01-25T00:00:00Z"
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.duration_ms>0 AND timestamp>="2026-01-25T00:00:00Z"' \
  --format=json \
  > processing_times.json
```

Then analyze with Python:

```python
import json
import statistics

with open('processing_times.json') as f:
    logs = json.load(f)

durations = [
    log['jsonPayload']['duration_ms']
    for log in logs
    if 'duration_ms' in log.get('jsonPayload', {})
]

print(f"Average: {statistics.mean(durations):.2f}ms")
print(f"Median: {statistics.median(durations):.2f}ms")
print(f"P95: {statistics.quantiles(durations, n=20)[18]:.2f}ms")
```

## Multi-Worker Tracing

### All Workers Processing an Episode

Find which workers handled an episode:

```
jsonPayload.correlation_id="episode-abc-123"
jsonPayload.worker_id:*
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.correlation_id="episode-abc-123"' \
  --format="value(jsonPayload.worker_id, jsonPayload.stage, timestamp)" \
  --order=asc
```

### Worker Handoff Timeline

Visualize when an episode moved between workers:

```
jsonPayload.correlation_id="episode-abc-123"
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.correlation_id="episode-abc-123"' \
  --format="table(timestamp, jsonPayload.worker_id, jsonPayload.stage, jsonPayload.message)" \
  --order=asc
```

## Advanced Queries

### Combine Multiple Conditions

Find errors for a specific episode:

```
jsonPayload.episode_id=123
severity=ERROR
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.episode_id=123 AND severity=ERROR' \
  --format=json
```

### Regular Expression Matching

Find logs matching a pattern:

```
jsonPayload.message=~".*timeout.*"
severity>=WARNING
```

**CLI Command:**

```bash
gcloud logging read 'jsonPayload.message=~".*timeout.*" AND severity>=WARNING' \
  --format=json \
  --freshness=1d
```

### Exclude Test Logs

Filter out test/development logs:

```
NOT labels."env"="test"
severity>=INFO
```

## Log-Based Metrics

### Create Metric for Error Rate

Create a log-based metric to track error rates:

```bash
gcloud logging metrics create thestill_error_rate \
  --description="Thestill error rate" \
  --log-filter='severity=ERROR AND resource.type="cloud_run_revision" AND resource.labels.service_name="thestill"'
```

### Create Metric for Processing Time

Track average processing duration:

```bash
gcloud logging metrics create thestill_processing_duration \
  --description="Thestill processing duration" \
  --log-filter='jsonPayload.duration_ms>0' \
  --value-extractor='EXTRACT(jsonPayload.duration_ms)'
```

### Create Metric for Episode Count

Count processed episodes:

```bash
gcloud logging metrics create thestill_episodes_processed \
  --description="Episodes processed count" \
  --log-filter='jsonPayload.message="Episode processed"'
```

## Alerting

### Alert on High Error Rate

Create an alert policy for error rate:

```bash
# First create the metric (see above)
# Then create alert policy via Console or API
```

**Alert Condition:**

- Metric: `logging.googleapis.com/user/thestill_error_rate`
- Condition: Rate > 10 errors/minute
- Duration: 5 minutes

### Alert on Stuck Episodes

Create alert for episodes with no progress:

**Log-based alert:**

- Filter: `jsonPayload.message="Processing started"`
- Condition: No matching logs for specific episode_id in 30 minutes

### Alert on Worker Failures

Alert when workers crash or fail:

```bash
gcloud logging metrics create thestill_worker_failures \
  --description="Worker failure count" \
  --log-filter='severity=ERROR AND jsonPayload.error_type="WorkerCrashed"'
```

## Logs Explorer Features

### Group By Worker

In Logs Explorer Console, group logs by worker:

1. Use query: `jsonPayload.worker_id:*`
2. Click "Group by" → Select `jsonPayload.worker_id`
3. View logs organized by worker

### Histogram View

View log volume over time:

1. Run your query
2. Click "Histogram" tab
3. Adjust time granularity (1min, 5min, 1hour)

### Source Location Navigation

Click on source location in log entry to see:

- File path
- Line number
- Function name

Useful for debugging specific code paths.

## Export to BigQuery

### Create BigQuery Sink

Export logs to BigQuery for advanced analysis:

```bash
gcloud logging sinks create thestill-bigquery-sink \
  bigquery.googleapis.com/projects/YOUR_PROJECT/datasets/thestill_logs \
  --log-filter='resource.type="cloud_run_revision" AND resource.labels.service_name="thestill"'
```

### Query in BigQuery

Once exported, run SQL queries:

```sql
-- Average processing time by stage
SELECT
  jsonPayload.stage,
  AVG(jsonPayload.duration_ms) as avg_duration_ms,
  COUNT(*) as count
FROM `YOUR_PROJECT.thestill_logs.cloudrun_googleapis_com_stdout_*`
WHERE jsonPayload.duration_ms IS NOT NULL
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY jsonPayload.stage
ORDER BY avg_duration_ms DESC
```

```sql
-- Episode failure analysis
SELECT
  jsonPayload.error_type,
  COUNT(DISTINCT jsonPayload.episode_id) as failed_episodes,
  COUNT(*) as total_failures
FROM `YOUR_PROJECT.thestill_logs.cloudrun_googleapis_com_stderr_*`
WHERE severity = 'ERROR'
  AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY jsonPayload.error_type
ORDER BY failed_episodes DESC
```

## Best Practices

### 1. Use Correlation IDs

Always query with correlation_id for tracing:

```
jsonPayload.correlation_id="episode-abc-123"
```

### 2. Limit Time Range

Always specify time range to improve query performance:

```
timestamp>="2026-01-25T00:00:00Z"
timestamp<"2026-01-26T00:00:00Z"
```

### 3. Use Appropriate Severity Levels

Filter by severity to reduce noise:

```
severity>=WARNING  # Only warnings, errors, critical
```

### 4. Save Common Queries

Save frequently used queries in Cloud Console for quick access.

### 5. Use Log-Based Metrics for Dashboards

Create metrics for important KPIs and add to Cloud Monitoring dashboards.

## Troubleshooting

### No Logs Appearing

1. Check Cloud Run/GKE has logging enabled
2. Verify `LOG_FORMAT=gcp` is set
3. Check service account has `logging.logWriter` role
4. Verify logs are going to stderr (not stdout for errors)

### Missing Fields

If custom fields (episode_id, worker_id) are missing:

1. Verify structlog is properly configured
2. Check field binding: `structlog.contextvars.bind_contextvars(episode_id=123)`
3. Ensure fields are logged with key-value pairs

### Query Too Slow

1. Narrow time range
2. Add resource filters (`resource.type`, `resource.labels.service_name`)
3. Use log-based metrics for aggregated data
4. Export to BigQuery for complex analysis

## Dashboard Examples

### Cloud Monitoring Dashboard

Create a dashboard with:

1. **Error Rate** (Line chart from `thestill_error_rate` metric)
2. **Processing Duration** (Heatmap from `thestill_processing_duration` metric)
3. **Episodes Processed** (Counter from `thestill_episodes_processed` metric)
4. **Active Workers** (MQL query counting unique worker_ids)
5. **Recent Errors** (Logs panel filtered to severity=ERROR)

### Example MQL Query

Monitor active workers in last 5 minutes:

```
fetch cloud_run_revision
| metric 'logging.googleapis.com/user/thestill_worker_activity'
| filter resource.service_name == 'thestill'
| group_by 5m, [value_worker_count_mean: mean(value.worker_count)]
| every 5m
```

## See Also

- [Cloud Deployment Guide](logging-cloud-deployment.md)
- [Elastic Queries Documentation](logging-elastic-queries.md)
- [Cloud Logging Query Language](https://cloud.google.com/logging/docs/view/logging-query-language)
- [Log-Based Metrics](https://cloud.google.com/logging/docs/logs-based-metrics)
- [Exporting Logs](https://cloud.google.com/logging/docs/export)
