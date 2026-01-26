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
  "message": "Episode processed",
  "@timestamp": "2026-01-25T16:34:22.186Z",
  "timestamp": "2026-01-25T16:34:22.186Z",
  "level": "INFO",
  "episode_id": "abc123",
  "duration_ms": 4500,
  "worker_id": "worker-transcribe-2",
  "request_id": "r-abc123"
}
```

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
fields @timestamp, level, message, duration_ms
| filter episode_id = "abc123"
| sort @timestamp asc
```

### All Logs from a Specific Worker

Debug what a specific worker is doing:

```sql
fields @timestamp, level, message, episode_id
| filter worker_id = "worker-a7f2"
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
fields @timestamp, level, message, worker_id, duration_ms
| filter episode_id = "abc-123"
| sort @timestamp asc
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

```sql
fields @timestamp, message, episode_id, duration_ms
| filter message = "Episode processed"
| filter @timestamp > ago(1h)
| sort @timestamp desc
```

### Episodes by Podcast

Find all episodes for a specific podcast:

```sql
fields @timestamp, message, episode_id, duration_ms
| filter podcast_id = 42
| filter @timestamp > ago(24h)
| sort @timestamp desc
```

## Failure Analysis

### All Transcription Failures

Find episodes that failed transcription:

```sql
fields @timestamp, message, episode_id, error, error_type
| filter level = "ERROR" and message like /transcri/
| sort @timestamp desc
```

### Error Count by Type (Last 24 Hours)

Aggregate errors by type:

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

### Average Processing Time by Stage

Analyze performance across pipeline stages:

```sql
filter duration_ms > 0
| stats avg(duration_ms) as avg_ms,
        max(duration_ms) as max_ms,
        min(duration_ms) as min_ms,
        count(*) as count
  by message
| sort avg_ms desc
```

### Slow Transcriptions (> 5 minutes)

Find transcriptions taking longer than 5 minutes:

```sql
fields @timestamp, episode_id, duration_ms
| filter message like /transcri/ and duration_ms > 300000
| sort duration_ms desc
```

### P95 Duration by Operation

Calculate percentiles:

```sql
filter duration_ms > 0
| stats pct(duration_ms, 50) as p50,
        pct(duration_ms, 95) as p95,
        pct(duration_ms, 99) as p99
  by message
```

### Worker Performance Comparison

Compare processing times across workers:

```sql
filter duration_ms > 0
| stats avg(duration_ms) as avg_duration,
        count(*) as task_count
  by worker_id
| sort avg_duration desc
```

## Time-Based Analysis

### Episodes Processed Per Hour

Track system throughput:

```sql
filter message = "Episode processed"
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
  --filter-pattern '{ $.level = "ERROR" && $.message = "*transcri*" }' \
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
          "title": "Processing Times",
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
