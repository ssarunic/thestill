# Elastic (ECS) Query Examples

This guide provides query examples for analyzing thestill logs in Elasticsearch/Kibana using the Elastic Common Schema (ECS) format.

## Prerequisites

- Logs ingested with `LOG_FORMAT=ecs`
- Elasticsearch cluster with thestill logs indexed
- Kibana for visualization (optional)

## ECS Log Structure

Thestill logs use the following ECS field structure:

```json
{
  "@timestamp": "2026-01-25T16:34:22.186Z",
  "log.level": "info",
  "message": "task_completed_successfully",
  "ecs.version": "1.6.0",
  "episode_id": 123,
  "task_id": 456,
  "stage": "transcribe",
  "retry_count": 0,
  "worker_id": "3fb0e2a4"
}
```

The `message` field holds the structlog event name (e.g. `task_processing_started`, `download_completed`, `http_request_completed`). Correlation fields are per-layer: `request_id` (HTTP/MCP), `command_id` (CLI), and `worker_id`/`task_id`/`episode_id`/`stage`/`retry_count` (task worker). Generated IDs are bare 8-character UUID slices such as `a7f2c1d9`.

## Basic Queries

### All Logs for a Specific Episode

Trace all operations for a single episode:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "term": {
      "episode_id": 123
    }
  },
  "sort": [
    { "@timestamp": "asc" }
  ]
}
```

**Kibana Query (KQL):**

```
episode_id: 123
```

### All Logs from a Specific Worker

Debug what a specific worker is doing:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "term": {
      "worker_id": "a7f2c1d9"
    }
  },
  "sort": [
    { "@timestamp": "desc" }
  ],
  "size": 100
}
```

**Kibana Query (KQL):**

```
worker_id: "a7f2c1d9"
```

### Error Logs Only

Find all errors in the system:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "term": {
      "log.level": "error"
    }
  },
  "sort": [
    { "@timestamp": "desc" }
  ]
}
```

**Kibana Query (KQL):**

```
log.level: error
```

## Episode Tracing

### Complete Episode Journey

Trace an episode through the entire pipeline using the `episode_id` bound by the task worker:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "term": {
      "episode_id": "abc-123"
    }
  },
  "sort": [
    { "@timestamp": "asc" }
  ],
  "size": 1000
}
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

### Episode Processing Time

Calculate total processing time for an episode:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "bool": {
      "must": [
        { "term": { "episode_id": 123 } },
        { "exists": { "field": "duration_ms" } }
      ]
    }
  },
  "aggs": {
    "total_duration": {
      "sum": {
        "field": "duration_ms"
      }
    },
    "by_stage": {
      "terms": {
        "field": "stage.keyword"
      },
      "aggs": {
        "stage_duration": {
          "sum": {
            "field": "duration_ms"
          }
        }
      }
    }
  }
}
```

## Failure Analysis

### Group Errors by Type

Identify the most common error types. The `error_type` field holds the Python exception class name (e.g. `TransientError`, `FatalError`, `TranscriptCleaningError`, `ConnectionError`) on CLI and HTTP failure events (`cli_command_failed`, `http_request_failed`); `mcp_stdio_failed` uses category strings (`protocol_error`, `validation_error`, ...). Task worker failures are the events `task_fatal_error` / `task_transient_error` and carry `error_class` (`infra` vs `item`) instead:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "term": {
      "log.level": "error"
    }
  },
  "aggs": {
    "error_types": {
      "terms": {
        "field": "error_type.keyword",
        "size": 10
      }
    }
  }
}
```

**Kibana Visualization:**

Create a pie chart showing error distribution by type.

### Failed Episodes in Last 24 Hours

Find episodes that failed transcription (dead-lettered tasks in the transcribe stage):

```json
GET /thestill-logs-*/_search
{
  "query": {
    "bool": {
      "must": [
        { "term": { "message": "task_fatal_error" } },
        { "term": { "stage": "transcribe" } },
        {
          "range": {
            "@timestamp": {
              "gte": "now-24h"
            }
          }
        }
      ]
    }
  },
  "aggs": {
    "failed_episodes": {
      "cardinality": {
        "field": "episode_id"
      }
    }
  }
}
```

### Retry Patterns

Find episodes with multiple retry attempts using the bound `retry_count` field:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "range": {
      "retry_count": {
        "gt": 0
      }
    }
  },
  "aggs": {
    "episodes_with_retries": {
      "terms": {
        "field": "episode_id",
        "size": 20
      },
      "aggs": {
        "max_retry_count": {
          "max": {
            "field": "retry_count"
          }
        }
      }
    }
  }
}
```

## Performance Monitoring

### Average Processing Time by Stage

Analyze performance across pipeline stages:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "range": {
      "@timestamp": {
        "gte": "now-7d"
      }
    }
  },
  "aggs": {
    "by_stage": {
      "terms": {
        "field": "stage.keyword"
      },
      "aggs": {
        "avg_duration": {
          "avg": {
            "field": "duration_ms"
          }
        },
        "percentiles_duration": {
          "percentiles": {
            "field": "duration_ms",
            "percents": [50, 90, 95, 99]
          }
        }
      }
    }
  }
}
```

### Slow Transcriptions

Find transcriptions taking longer than 5 minutes:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "bool": {
      "must": [
        { "term": { "stage": "transcribe" } },
        {
          "range": {
            "duration_ms": {
              "gte": 300000
            }
          }
        }
      ]
    }
  },
  "sort": [
    { "duration_ms": "desc" }
  ]
}
```

**Kibana Query (KQL):**

```
stage: "transcribe" AND duration_ms >= 300000
```

### Worker Performance Comparison

Compare processing times across workers:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "exists": {
      "field": "worker_id"
    }
  },
  "aggs": {
    "by_worker": {
      "terms": {
        "field": "worker_id.keyword",
        "size": 50
      },
      "aggs": {
        "avg_duration": {
          "avg": {
            "field": "duration_ms"
          }
        },
        "total_processed": {
          "value_count": {
            "field": "episode_id"
          }
        }
      }
    }
  }
}
```

## Time-Based Analysis

### Episodes Processed Per Hour

Track system throughput. There is no single canonical "episode done" event — `task_completed_successfully` fires once per stage, so filter on the final pipeline stage (`summarize`) to count fully processed episodes:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "bool": {
      "must": [
        { "term": { "message": "task_completed_successfully" } },
        { "term": { "stage": "summarize" } },
        {
          "range": {
            "@timestamp": {
              "gte": "now-24h"
            }
          }
        }
      ]
    }
  },
  "aggs": {
    "episodes_per_hour": {
      "date_histogram": {
        "field": "@timestamp",
        "calendar_interval": "hour"
      },
      "aggs": {
        "unique_episodes": {
          "cardinality": {
            "field": "episode_id"
          }
        }
      }
    }
  }
}
```

### Peak Usage Times

Identify when the system is busiest:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "range": {
      "@timestamp": {
        "gte": "now-7d"
      }
    }
  },
  "aggs": {
    "by_hour_of_day": {
      "terms": {
        "script": {
          "source": "doc['@timestamp'].value.getHour()",
          "lang": "painless"
        },
        "size": 24
      },
      "aggs": {
        "log_count": {
          "value_count": {
            "field": "@timestamp"
          }
        }
      }
    }
  }
}
```

## Multi-Worker Tracing

### Find All Workers Processing an Episode

Trace which workers handled an episode — each stage may run on a different worker, so join on the shared `episode_id`:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "term": {
      "episode_id": "abc-123"
    }
  },
  "aggs": {
    "workers": {
      "terms": {
        "field": "worker_id.keyword"
      },
      "aggs": {
        "stages": {
          "terms": {
            "field": "stage.keyword"
          }
        }
      }
    }
  }
}
```

### Worker Handoff Timeline

Visualize when an episode moved between workers:

```json
GET /thestill-logs-*/_search
{
  "query": {
    "term": {
      "episode_id": "abc-123"
    }
  },
  "sort": [
    { "@timestamp": "asc" }
  ],
  "_source": ["@timestamp", "worker_id", "stage", "message"]
}
```

## Alerting Queries

### High Error Rate

Alert when error rate exceeds threshold:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "range": {
      "@timestamp": {
        "gte": "now-5m"
      }
    }
  },
  "aggs": {
    "error_rate": {
      "filters": {
        "filters": {
          "errors": {
            "term": { "log.level": "error" }
          },
          "total": {
            "match_all": {}
          }
        }
      }
    }
  }
}
```

Alert if `errors / total > 0.1` (10% error rate).

### Stuck Episodes

Find episodes with no progress in 30 minutes:

```json
GET /thestill-logs-*/_search
{
  "size": 0,
  "query": {
    "range": {
      "@timestamp": {
        "gte": "now-2h",
        "lte": "now-30m"
      }
    }
  },
  "aggs": {
    "episodes": {
      "terms": {
        "field": "episode_id",
        "size": 100
      },
      "aggs": {
        "last_seen": {
          "max": {
            "field": "@timestamp"
          }
        }
      }
    }
  }
}
```

## Kibana Dashboard Examples

### Episode Processing Dashboard

Create a dashboard with:

1. **Total Episodes Processed** (Metric visualization)
2. **Processing Time Trend** (Line chart over time)
3. **Error Rate** (Gauge showing errors/hour)
4. **Top Error Types** (Pie chart)
5. **Worker Activity** (Heat map of worker activity)
6. **Slow Episodes** (Data table of episodes > 5min)

### Operations Dashboard

1. **System Health** (Green/Yellow/Red based on error rate)
2. **Active Workers** (Count of unique worker_id in last 5min)
3. **Throughput** (Episodes/hour line chart)
4. **Queue Depth** (Episodes waiting vs processing)
5. **Recent Errors** (Log table filtered to errors)

## Best Practices

1. **Use episode_id / task_id** for multi-worker tracing (and request_id / command_id for HTTP / CLI)
2. **Add stage field** to all processing logs
3. **Include duration_ms** for performance analysis
4. **Use keyword fields** for aggregations (add `.keyword` suffix)
5. **Set appropriate time ranges** to limit query scope
6. **Create index patterns** matching your log naming scheme

## See Also

- [Cloud Deployment Guide](logging-cloud-deployment.md)
- [GCP Queries Documentation](logging-gcp-queries.md)
- [Elasticsearch Query DSL](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html)
- [Kibana Query Language](https://www.elastic.co/guide/en/kibana/current/kuery-query.html)
