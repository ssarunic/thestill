# Cloud Logging Deployment Guide

This guide covers deploying thestill with cloud-native logging on AWS Elastic (ECS) and Google Cloud Platform (Cloud Logging).

## Overview

Thestill supports multiple log formats optimized for cloud observability platforms:

- **ECS (Elastic Common Schema)**: For AWS Elastic Stack (Elasticsearch, Kibana)
- **GCP (Google Cloud Logging)**: For Google Cloud Platform
- **JSON**: Generic structured logging for any platform
- **Console**: Colored output for local development

## Configuration

All logging configuration is controlled via environment variables:

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `LOG_LEVEL` | DEBUG, INFO, WARNING, ERROR, CRITICAL | INFO | Minimum log level to emit |
| `LOG_FORMAT` | console, json, ecs, gcp, auto | auto | Output format (auto=console for TTY, json otherwise) |
| `LOG_FILE` | file path | none | Optional file output path |
| `SERVICE_NAME` | string | thestill | Service name for GCP logs |
| `SERVICE_VERSION` | string | 1.0.0 | Service version for GCP logs |

## AWS Elastic (ECS)

### ECS Task Definition

Configure your ECS task definition with the ECS log format:

```json
{
  "family": "thestill-transcription",
  "containerDefinitions": [
    {
      "name": "thestill",
      "image": "your-registry/thestill:latest",
      "environment": [
        {
          "name": "LOG_FORMAT",
          "value": "ecs"
        },
        {
          "name": "LOG_LEVEL",
          "value": "INFO"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/thestill",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "thestill"
        }
      }
    }
  ]
}
```

### Filebeat Shipping to Elastic

If shipping logs to Elasticsearch via Filebeat:

```yaml
# filebeat.yml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/thestill/*.log
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["https://your-elastic-cluster:9200"]
  index: "thestill-logs-%{+yyyy.MM.dd}"
```

### ECS Log Format

Logs are emitted in Elastic Common Schema format:

```json
{
  "@timestamp": "2026-01-25T16:34:22.186Z",
  "log.level": "info",
  "message": "Episode processed",
  "ecs.version": "1.6.0",
  "episode_id": 123,
  "duration_ms": 4500
}
```

## AWS CloudWatch Logs (Simple)

CloudWatch Logs is a simpler, cheaper alternative to the Elastic Stack for AWS deployments. It requires no external dependencies and works with standard JSON output.

### Why CloudWatch vs ECS Format?

| Feature | CloudWatch | ECS (Elastic Stack) |
|---------|------------|---------------------|
| Cost | Pay per ingestion ($0.50/GB) | Self-managed or Elastic Cloud |
| Setup | No additional infrastructure | Elasticsearch + Kibana + Filebeat |
| Query | Logs Insights (SQL-like) | Elasticsearch DSL |
| Dashboards | CloudWatch dashboards | Kibana |
| Alerting | CloudWatch Alarms | Elastic Alerting |
| Dependencies | None | ecs-logging package |

**Use CloudWatch when**: Simple setup, AWS-native, cost-sensitive deployments.

**Use ECS when**: Need Kibana visualizations, complex aggregations, existing Elastic infrastructure.

### ECS Task Definition

Configure your ECS task definition with CloudWatch log format:

```json
{
  "family": "thestill-transcription",
  "containerDefinitions": [
    {
      "name": "thestill",
      "image": "your-registry/thestill:latest",
      "environment": [
        {
          "name": "LOG_FORMAT",
          "value": "cloudwatch"
        },
        {
          "name": "LOG_LEVEL",
          "value": "INFO"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/thestill",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "thestill",
          "awslogs-create-group": "true"
        }
      }
    }
  ]
}
```

### Fargate Deployment

```yaml
# task-definition.yaml
containerDefinitions:
  - name: thestill
    image: your-registry/thestill:latest
    environment:
      - name: LOG_FORMAT
        value: cloudwatch
      - name: LOG_LEVEL
        value: INFO
    logConfiguration:
      logDriver: awslogs
      options:
        awslogs-group: /fargate/thestill
        awslogs-region: us-east-1
        awslogs-stream-prefix: thestill
```

### Lambda Deployment

For Lambda functions, logs go to CloudWatch automatically:

```python
# handler.py
import os

# Set environment variables before importing thestill
os.environ["LOG_FORMAT"] = "cloudwatch"
os.environ["LOG_LEVEL"] = "INFO"

from thestill.logging import configure_structlog, get_logger

configure_structlog()
logger = get_logger(__name__)

def handler(event, context):
    logger.info("Processing event", event_type=event.get("type"))
    # ... your code here
```

### Log Group Setup

Create log group with retention policy:

```bash
# Create log group
aws logs create-log-group --log-group-name /ecs/thestill

# Set retention to 30 days (cost optimization)
aws logs put-retention-policy \
  --log-group-name /ecs/thestill \
  --retention-in-days 30

# Create metric filter for error rate alerting
aws logs put-metric-filter \
  --log-group-name /ecs/thestill \
  --filter-name ErrorCount \
  --filter-pattern '{ $.level = "ERROR" }' \
  --metric-transformations \
    metricName=ThestillErrors,metricNamespace=Thestill,metricValue=1
```

### CloudWatch Alarm for Errors

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name ThestillHighErrorRate \
  --metric-name ThestillErrors \
  --namespace Thestill \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789:alerts
```

### CloudWatch Log Format

Logs are emitted with CloudWatch-optimized field names:

```json
{
  "message": "Episode processed",
  "@timestamp": "2026-01-25T16:34:22.186Z",
  "timestamp": "2026-01-25T16:34:22.186Z",
  "level": "INFO",
  "episode_id": 123,
  "duration_ms": 4500,
  "request_id": "abc123",
  "worker_id": "worker-1"
}
```

Key differences from generic JSON format:

- `message` field (alias for `event`, CloudWatch convention)
- `@timestamp` field (CloudWatch convention)
- Uppercase `level` (INFO, ERROR, WARNING)

For query examples, see [logging-cloudwatch-queries.md](logging-cloudwatch-queries.md).

## Google Cloud Platform (GCP)

### Cloud Run Deployment

Configure Cloud Run services with GCP log format:

```bash
gcloud run deploy thestill \
  --image=gcr.io/your-project/thestill:latest \
  --set-env-vars="LOG_FORMAT=gcp,LOG_LEVEL=INFO,SERVICE_NAME=thestill,SERVICE_VERSION=1.0.0" \
  --region=us-central1
```

Or using a YAML service definition:

```yaml
# cloud-run.yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: thestill
spec:
  template:
    spec:
      containers:
        - image: gcr.io/your-project/thestill:latest
          env:
            - name: LOG_FORMAT
              value: gcp
            - name: LOG_LEVEL
              value: INFO
            - name: SERVICE_NAME
              value: thestill
            - name: SERVICE_VERSION
              value: "1.0.0"
```

Deploy with:

```bash
gcloud run services replace cloud-run.yaml --region=us-central1
```

### GKE (Google Kubernetes Engine)

For GKE deployments, logs are automatically collected by Cloud Logging:

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: thestill
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: thestill
          image: gcr.io/your-project/thestill:latest
          env:
            - name: LOG_FORMAT
              value: gcp
            - name: LOG_LEVEL
              value: INFO
            - name: SERVICE_NAME
              value: thestill
            - name: SERVICE_VERSION
              value: "1.0.0"
```

### GCP Log Format

Logs are emitted in Google Cloud Logging format:

```json
{
  "message": "Episode processed",
  "time": "2026-01-25T16:34:04.073930Z",
  "severity": "INFO",
  "episode_id": 123,
  "duration_ms": 4500,
  "logging.googleapis.com/sourceLocation": {
    "file": "thestill/core/transcriber.py",
    "line": "142",
    "function": "transcribe"
  }
}
```

## Correlation ID Tracing

### Adding Correlation IDs

Use structlog's context variables to add correlation IDs for tracing requests across workers:

```python
import structlog
from thestill.logging import get_logger

logger = get_logger(__name__)

# Bind correlation ID to logger context
structlog.contextvars.bind_contextvars(
    correlation_id="abc-123",
    episode_id=episode.id,
    worker_id=worker.id
)

# All subsequent logs will include these fields
logger.info("Starting transcription")
logger.info("Audio downloaded")
logger.info("Transcription complete")

# Clear context when done
structlog.contextvars.clear_contextvars()
```

### Multi-Worker Tracing

For distributed processing across multiple workers:

```python
# Worker A: Download audio
structlog.contextvars.bind_contextvars(
    correlation_id=episode.id,
    worker_id="worker-download-1"
)
logger.info("Audio download started")

# Worker B: Transcribe audio (use same correlation_id)
structlog.contextvars.bind_contextvars(
    correlation_id=episode.id,
    worker_id="worker-transcribe-2"
)
logger.info("Transcription started")
```

Query both workers' logs using the shared `correlation_id` field.

## Log Retention

### AWS CloudWatch Logs

Set retention policies on log groups:

```bash
aws logs put-retention-policy \
  --log-group-name /ecs/thestill \
  --retention-in-days 30
```

### GCP Cloud Logging

Configure retention in Cloud Logging settings:

```bash
# Create a custom bucket with retention
gcloud logging buckets create thestill-logs \
  --location=global \
  --retention-days=30

# Route logs to the bucket
gcloud logging sinks create thestill-sink \
  --log-filter='resource.type="cloud_run_revision" resource.labels.service_name="thestill"' \
  --destination=logging.googleapis.com/projects/YOUR_PROJECT/locations/global/buckets/thestill-logs
```

## Testing Locally

Test cloud formatters locally before deploying:

```bash
# Test ECS format
LOG_FORMAT=ecs ./venv/bin/thestill status

# Test GCP format
LOG_FORMAT=gcp SERVICE_NAME=thestill ./venv/bin/thestill status

# Run validation script
./venv/bin/python test_cloud_logging.py
```

## Best Practices

### 1. Use Correlation IDs

Always add correlation IDs for multi-step operations:

```python
structlog.contextvars.bind_contextvars(
    correlation_id=episode.id,
    user_id=user.id,
    request_id=request.id
)
```

### 2. Structured Logging

Use key-value pairs instead of string formatting:

```python
# Good
logger.info("Episode processed", episode_id=123, duration_ms=4500)

# Bad
logger.info(f"Episode {123} processed in {4500}ms")
```

### 3. Log Levels

Use appropriate log levels:

- **DEBUG**: Detailed diagnostic information (not logged in production)
- **INFO**: General informational messages (default)
- **WARNING**: Warning messages for recoverable issues
- **ERROR**: Error messages for failures
- **CRITICAL**: Critical failures requiring immediate attention

### 4. Sensitive Data

Avoid logging sensitive information:

```python
# Bad
logger.info("User logged in", password=user.password)

# Good
logger.info("User logged in", user_id=user.id)
```

The logging system automatically redacts common sensitive fields, but explicit redaction is better.

## Troubleshooting

### Logs not appearing in CloudWatch

1. Verify ECS task has correct IAM permissions (`logs:CreateLogStream`, `logs:PutLogEvents`)
2. Check log group exists: `aws logs describe-log-groups --log-group-name-prefix /ecs/thestill`
3. Check `LOG_FORMAT=cloudwatch` is set in environment
4. Verify logs are going to stderr (default for structlog)

### Logs not appearing in Elastic

1. Check Filebeat is running and connected to Elasticsearch
2. Verify index patterns in Kibana
3. Check `LOG_FORMAT=ecs` is set in environment

### Logs not appearing in GCP Cloud Logging

1. Verify Cloud Run/GKE has logging permissions
2. Check `LOG_FORMAT=gcp` is set in environment
3. Check stderr output is being captured (Cloud Run/GKE default)

### Invalid JSON in logs

1. Ensure no print() statements in code (use `ruff` to check)
2. Verify all third-party libraries use structlog or stdlib logging
3. Run local validation: `./venv/bin/python test_cloud_logging.py`

## See Also

- [CloudWatch Queries Documentation](logging-cloudwatch-queries.md)
- [Elastic Queries Documentation](logging-elastic-queries.md)
- [GCP Queries Documentation](logging-gcp-queries.md)
- [Logging Configuration](configuration.md#logging)
