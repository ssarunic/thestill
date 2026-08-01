# AWS CloudWatch Logs Insights Query Examples

This guide provides query examples for analyzing thestill logs in AWS CloudWatch Logs Insights.

## Prerequisites

- Logs ingested with `LOG_FORMAT=cloudwatch` or `LOG_FORMAT=json`
- CloudWatch Log Group created (e.g., `/ecs/thestill`)
- AWS Console access or AWS CLI configured

## CloudWatch Log Structure

Thestill logs use the following CloudWatch-optimized format:

```json
{
  "message": "task_completed_successfully",
  "@timestamp": "2026-01-25T16:34:22.186Z",
  "level": "INFO",
  "episode_id": "abc123",
  "task_id": "9f1c2e3a-4b5d-4e6f-8a9b-0c1d2e3f4a5b",
  "stage": "transcribe",
  "retry_count": 0,
  "worker_id": "3fb0e2a4"
}
```

The `message` field holds the structlog event name (e.g. `task_processing_started`, `download_completed`, `http_request_completed`). Generated IDs (`request_id`, `command_id`, `worker_id`) are bare 8-character UUID slices such as `a7f2c1d9`.

## Basic Queries

### Recent Logs

Get the 25 most recent log entries:

```sql
fields @timestamp, level, message
| sort @timestamp desc
| limit 25
```

### All Logs for a Specific Episode

Trace all operations for a single episode:

```sql
fields @timestamp, level, message, stage
| filter episode_id = "abc123"
| sort @timestamp asc
```

### All Logs from a Specific Worker

Debug what a specific worker is doing:

```sql
fields @timestamp, level, message, episode_id
| filter worker_id = "a7f2c1d9"
| sort @timestamp desc
| limit 100
```

### Error Logs Only

Find all errors in the system:

```sql
fields @timestamp, message, episode_id, error, error_type
| filter level = "ERROR"
| sort @timestamp desc
| limit 200
```

### Logs from Specific Request

Trace a complete HTTP request:

```sql
fields @timestamp, level, message, episode_id, duration_ms
| filter request_id = "abc123"
| sort @timestamp asc
```

## Episode Tracing

### Complete Episode Journey

Trace an episode through the entire pipeline:

```sql
fields @timestamp, level, message, worker_id, stage
| filter episode_id = "abc-123"
| sort @timestamp asc
```

**Expected log sequence** (each stage repeats the `task_processing_started` / `task_completed_successfully` pair with a different `stage` value):

1. `task_processing_started` (stage=download)
2. `downloading_episode` / `download_completed`
3. `task_completed_successfully` (stage=download)
4. `task_processing_started` (stage=downsample) ... `task_completed_successfully`
5. `task_processing_started` (stage=transcribe) ... `task_completed_successfully`
6. `task_processing_started` (stage=clean) ... `task_completed_successfully`
7. `task_processing_started` (stage=summarize) ... `task_completed_successfully`

Handlers also emit human-readable messages such as `Download completed for episode: <title>` and `Transcription completed for episode: <title>`.

### Episode Processing in Last Hour

There is no single canonical "episode done" event — `task_completed_successfully` fires once per stage. Filter on the final pipeline stage (`summarize`) to count fully processed episodes:

```sql
fields @timestamp, message, episode_id, stage
| filter message = "task_completed_successfully" and stage = "summarize"
| filter @timestamp > ago(1h)
| sort @timestamp desc
```

### Episodes by Podcast

Find all episodes for a specific podcast:

```sql
fields @timestamp, message, episode_id
| filter podcast_id = "9f1c2e3a-4b5d-4e6f-8a9b-0c1d2e3f4a5b"
| filter @timestamp > ago(24h)
| sort @timestamp desc
```

## Failure Analysis

### All Transcription Failures

Task failures are logged as `task_fatal_error` (dead-lettered) or `task_transient_error` (will retry), with the bound `stage` field identifying the pipeline stage:

```sql
fields @timestamp, message, episode_id, error, error_class
| filter message in ["task_fatal_error", "task_transient_error"] and stage = "transcribe"
| sort @timestamp desc
```

### Error Count by Type (Last 24 Hours)

Aggregate errors by exception type. The `error_type` field holds the Python exception class name (e.g. `TransientError`, `FatalError`, `TranscriptCleaningError`, `ConnectionError`) on CLI and HTTP failure events (`cli_command_failed`, `http_request_failed`); `mcp_stdio_failed` uses category strings (`protocol_error`, `resource_not_found`, `validation_error`, `tool_execution_error`, `unknown_error`). Task worker failures use a three-way `error_class` (`fatal`, `infra`, `item`) instead, but note it's only emitted on `task_transient_error` and `task_unexpected_error` — `task_fatal_error` log lines carry `error` but not `error_class`:

```sql
filter level = "ERROR"
| stats count(*) as error_count by error_type
| sort error_count desc
```

### Failed Episodes by Hour

Track failure trends:

```sql
filter level = "ERROR"
| stats count(*) as failures by bin(1h)
```

### Errors by Worker

Identify problematic workers:

```sql
filter level = "ERROR"
| stats count(*) as errors by worker_id
| sort errors desc
```

## Performance Monitoring

Pipeline task-stage events (`task_processing_started`, `task_completed_successfully`, `downloading_episode`, `download_completed`, etc., emitted from the task worker) do **not** carry a `duration_ms` field today, so `stage`/`worker_id` cannot be joined against `duration_ms` for per-stage or per-worker pipeline timing. `duration_ms` is only emitted by HTTP requests (`http_request_completed`/`http_request_failed`), MCP tool calls (`mcp_stdio_completed`/`mcp_request_completed`), and feed-refresh summaries (`feed_refresh_summary`, `feed_refresh_batch_summary`).

### Average Duration by Event (HTTP/MCP/Feed Refresh)

Analyze performance across the event types that actually carry `duration_ms`:

```sql
filter duration_ms > 0
| stats avg(duration_ms) as avg_ms,
        max(duration_ms) as max_ms,
        min(duration_ms) as min_ms,
        count(*) as count
  by message
| sort avg_ms desc
```

### Slow Feed Refreshes (> 30 seconds)

Find per-podcast refresh cycles taking longer than 30 seconds:

```sql
fields @timestamp, podcast_slug, duration_ms
| filter message = "feed_refresh_summary" and duration_ms > 30000
| sort duration_ms desc
```

### P95 Duration by Operation

Calculate percentiles across HTTP/MCP/feed-refresh events:

```sql
filter duration_ms > 0
| stats pct(duration_ms, 50) as p50,
        pct(duration_ms, 95) as p95,
        pct(duration_ms, 99) as p99
  by message
```

### Slow API Endpoints by P95

Compare HTTP endpoint latency:

```sql
filter message = "http_request_completed"
| stats avg(duration_ms) as avg_duration,
        pct(duration_ms, 95) as p95_duration,
        count(*) as request_count
  by endpoint
| sort p95_duration desc
```

## Time-Based Analysis

### Episodes Processed Per Hour

Track system throughput (stage completions per hour; add `and stage = "summarize"` to count only fully finished episodes):

```sql
filter message = "task_completed_successfully"
| stats count_distinct(episode_id) as episodes by bin(1h)
```

### Request Rate Over Time

Monitor traffic patterns:

```sql
filter ispresent(request_id)
| stats count(*) as requests by bin(5m)
```

### Error Rate Trend

Track error rate over time:

```sql
stats sum(level = "ERROR") as errors,
      count(*) as total,
      (sum(level = "ERROR") / count(*)) * 100 as error_rate
by bin(1h)
```

## Multi-Worker Tracing

### All Workers Processing an Episode

Find which workers handled an episode:

```sql
filter episode_id = "abc-123"
| stats earliest(@timestamp) as first_seen,
        latest(@timestamp) as last_seen,
        count(*) as log_count
  by worker_id
```

### Worker Activity Timeline

Visualize worker activity:

```sql
stats count(*) as tasks by worker_id, bin(1h)
```

## CLI Command Tracing

### Track CLI Command Execution

Find all logs from a CLI command:

```sql
fields @timestamp, level, message, duration_s
| filter command_id = "abc12345"
| sort @timestamp asc
```

### CLI Command Performance

Analyze command execution times:

```sql
filter message = "cli_command_completed"
| stats avg(duration_s) as avg_duration,
        max(duration_s) as max_duration,
        count(*) as executions
  by command_name
```

## HTTP Request Analysis

### Slow API Endpoints

Find slow HTTP requests:

```sql
fields @timestamp, endpoint, duration_ms, status_code
| filter message = "http_request_completed" and duration_ms > 1000
| sort duration_ms desc
```

### API Error Rate by Endpoint

Track errors per endpoint:

```sql
filter message = "http_request_completed"
| stats sum(status_code >= 400) as errors,
        count(*) as total,
        (sum(status_code >= 400) / count(*)) * 100 as error_rate
  by endpoint
| sort error_rate desc
```

### Request Volume by Endpoint

Monitor endpoint usage:

```sql
filter message = "http_request_started"
| stats count(*) as requests by endpoint
| sort requests desc
```

## AWS CLI Examples

### Run Query via CLI

```bash
# Start query
QUERY_ID=$(aws logs start-query \
  --log-group-name /ecs/thestill \
  --start-time $(date -d '24 hours ago' +%s) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, level, message | filter episode_id = "abc123" | sort @timestamp asc' \
  --output text --query 'queryId')

# Wait and get results
sleep 5
aws logs get-query-results --query-id $QUERY_ID
```

### Query Errors in Last Hour

```bash
aws logs start-query \
  --log-group-name /ecs/thestill \
  --start-time $(date -d '1 hour ago' +%s) \
  --end-time $(date +%s) \
  --query-string 'filter level = "ERROR" | stats count(*) as errors by error_type'
```

## Alerting with Metric Filters

### High Error Rate Alert

Create a metric filter for error rate:

```bash
aws logs put-metric-filter \
  --log-group-name /ecs/thestill \
  --filter-name HighErrorRate \
  --filter-pattern '{ $.level = "ERROR" }' \
  --metric-transformations \
    metricName=ErrorCount,metricNamespace=Thestill,metricValue=1,defaultValue=0
```

Then create an alarm:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name ThestillHighErrorRate \
  --metric-name ErrorCount \
  --namespace Thestill \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789:alerts
```

### Slow Processing Alert

Metric filter for slow operations:

```bash
aws logs put-metric-filter \
  --log-group-name /ecs/thestill \
  --filter-name SlowProcessing \
  --filter-pattern '{ $.duration_ms > 300000 }' \
  --metric-transformations \
    metricName=SlowOperations,metricNamespace=Thestill,metricValue=1
```

### Transcription Failures

```bash
aws logs put-metric-filter \
  --log-group-name /ecs/thestill \
  --filter-name TranscriptionFailures \
  --filter-pattern '{ $.level = "ERROR" && $.stage = "transcribe" }' \
  --metric-transformations \
    metricName=TranscriptionFailures,metricNamespace=Thestill,metricValue=1
```

## Dashboard Examples

### Create CloudWatch Dashboard

```bash
aws cloudwatch put-dashboard \
  --dashboard-name ThestillOperations \
  --dashboard-body '{
    "widgets": [
      {
        "type": "log",
        "x": 0, "y": 0, "width": 12, "height": 6,
        "properties": {
          "title": "Recent Errors",
          "query": "fields @timestamp, message, error\\n| filter level = \"ERROR\"\\n| sort @timestamp desc\\n| limit 20",
          "region": "us-east-1",
          "view": "table"
        }
      },
      {
        "type": "metric",
        "x": 12, "y": 0, "width": 12, "height": 6,
        "properties": {
          "title": "Error Rate",
          "metrics": [["Thestill", "ErrorCount"]],
          "period": 300,
          "stat": "Sum"
        }
      },
      {
        "type": "log",
        "x": 0, "y": 6, "width": 24, "height": 6,
        "properties": {
          "title": "HTTP/MCP/Feed-Refresh Durations",
          "query": "filter duration_ms > 0\\n| stats avg(duration_ms) as avg, pct(duration_ms, 95) as p95 by message",
          "region": "us-east-1",
          "view": "table"
        }
      }
    ]
  }'
```

## Cost Optimization

### Log Retention

Set appropriate retention to control costs:

```bash
# 30 days for production
aws logs put-retention-policy \
  --log-group-name /ecs/thestill \
  --retention-in-days 30

# 7 days for development
aws logs put-retention-policy \
  --log-group-name /ecs/thestill-dev \
  --retention-in-days 7
```

### Query Cost Tips

1. **Always include time filters** to reduce data scanned
2. **Use `limit`** to cap result rows
3. **Use `stats`** for aggregations instead of returning all records
4. **Create metric filters** for frequently-needed metrics instead of running queries

### Estimate Query Costs

CloudWatch Logs Insights charges $0.005 per GB of data scanned. To estimate:

```bash
# Check log group size
aws logs describe-log-groups \
  --log-group-name-prefix /ecs/thestill \
  --query 'logGroups[].storedBytes'
```

## Best Practices

### 1. Use Correlation IDs

Always query with request_id, command_id, or episode_id for tracing:

```sql
filter request_id = "abc123" or episode_id = "abc123"
```

### 2. Limit Time Range

Narrow queries to reduce cost and improve performance:

```sql
filter @timestamp > ago(24h)
```

### 3. Use Stats for Aggregations

CloudWatch Logs Insights is optimized for aggregations:

```sql
stats count(*), avg(duration_ms), pct(duration_ms, 95) by message
```

### 4. Save Common Queries

Save frequently used queries in CloudWatch Console for quick access.

### 5. Use Field Indexes (Preview)

For high-cardinality fields like episode_id, consider field indexing:

```bash
aws logs put-index-policy \
  --log-group-identifier /ecs/thestill \
  --policy-document '{"Fields":["episode_id","request_id"]}'
```

## Troubleshooting

### No Logs Appearing

1. Check ECS task has correct IAM permissions:
   - `logs:CreateLogStream`
   - `logs:PutLogEvents`
2. Verify log group exists
3. Confirm `LOG_FORMAT=cloudwatch` is set
4. Check logs are going to stderr

### Missing Fields

If custom fields (episode_id, worker_id) are missing:

1. Verify structlog is properly configured
2. Check field binding: `structlog.contextvars.bind_contextvars(episode_id=123)`
3. Ensure fields are logged with key-value pairs

### Query Errors

1. Field names are case-sensitive
2. Use double quotes for string comparisons: `filter level = "ERROR"`
3. Use `ispresent()` to check if field exists: `filter ispresent(episode_id)`

## See Also

- [Cloud Deployment Guide](logging-cloud-deployment.md)
- [Logging Configuration](logging-configuration.md)
- [Elastic Queries Documentation](logging-elastic-queries.md)
- [GCP Queries Documentation](logging-gcp-queries.md)
- [CloudWatch Logs Insights Query Syntax](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html)
- [CloudWatch Logs Pricing](https://aws.amazon.com/cloudwatch/pricing/)
