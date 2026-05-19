# S3JobStore

A shared jobstore for APScheduler that uses an S3 bucket, enabling multiple application instances to define and execute jobs across a distributed system.

## Installation

```bash
# From the built wheel
pip install dist/apscheduler-*.whl[s3]

# Or build it yourself
make wheel
pip install dist/apscheduler-*.whl[s3]
```

## Quick Start

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.s3 import S3JobStore

scheduler = BackgroundScheduler(timezone='UTC',
                                job_defaults={'misfire_grace_time': 30})
scheduler.add_jobstore(
    S3JobStore(
        bucket='apscheduler-test',
        app_id='web-1',
        prefix='scheduler',
        endpoint_url='https://play.min.io:9000',
        aws_access_key_id='minioadmin',
        aws_secret_access_key='minioadmin',
    ),
    'shared'
)
scheduler.start()

# Add a job that only this instance executes
scheduler.add_job(my_task, 'interval', minutes=5, id='my-task', jobstore='shared')

# Add a job targeting a specific instance
scheduler.add_job(my_task, 'interval', minutes=5, id='process::worker-1', jobstore='shared')

# Add a broadcast job (all instances execute)
scheduler.add_job(my_task, 'interval', minutes=5, id='refresh::*', jobstore='shared')
```

> **Note**: The MinIO playground (`play.min.io`) is public and shared. Use a unique bucket name and don't store anything sensitive. For production, use your own S3 bucket or MinIO instance.

## Constructor Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `bucket` | Yes | — | S3 bucket name |
| `app_id` | Yes | — | Unique identifier for this application instance |
| `prefix` | No | `'apscheduler'` | Key prefix for all S3 objects |
| `pickle_protocol` | No | Highest available | Pickle protocol for serialization |
| `**boto3_kwargs` | No | — | Passed directly to `boto3.client('s3', ...)` |

### boto3_kwargs examples

```python
# AWS with explicit credentials
S3JobStore(bucket='my-bucket', app_id='web-1',
           region_name='us-east-1',
           aws_access_key_id='...',
           aws_secret_access_key='...')

# S3-compatible endpoint (MinIO, LocalStack)
S3JobStore(bucket='my-bucket', app_id='web-1',
           endpoint_url='http://localhost:9000',
           aws_access_key_id='minioadmin',
           aws_secret_access_key='minioadmin')
```

## Job Targeting

Targets are specified via the job ID using a `::` separator:

```
"job-name::target-pattern"
```

| Pattern | Meaning | Example |
|---------|---------|---------|
| No `::` | Targets self (`app_id`) | `id='cleanup'` → only this instance |
| Exact ID | Targets one instance | `id='task::worker-1'` → only worker-1 |
| Glob `*` | Broadcast to all | `id='refresh::*'` → every instance |
| Glob pattern | Targets matching | `id='sync::web-*'` → all web-* instances |

Patterns use Python's `fnmatch` (Unix glob semantics: `*`, `?`, `[seq]`).

### Broadcast Semantics

When a target matches multiple instances, **all** matching instances execute the job independently on the same schedule. Each instance computes due-ness from the trigger's schedule, so there are no race conditions regardless of S3 latency.

## Visibility Rules

| Method | Returns |
|--------|---------|
| `get_due_jobs(now)` | Jobs targeting me that are due |
| `get_all_jobs()` | Jobs I created OR jobs targeting me |
| `lookup_job(id)` | Any job by ID |

This means a creator can always see jobs it delegated to other instances.

## Execution Receipts

When a job's trigger advances (indicating execution), the store writes a receipt to S3:

```
s3://{bucket}/{prefix}/executions/{job_id}/{timestamp}_{app_id}.pkl
```

Query receipts programmatically:

```python
receipts = store.get_executions('my-job')
for r in receipts:
    print(f"{r['app_id']} executed at {r['timestamp']}")
```

## S3 Layout

```
s3://{bucket}/{prefix}/
├── jobs/
│   ├── {job_id}.pkl          # Job envelope (target, creator, state)
│   └── ...
└── executions/
    └── {job_id}/
        ├── {timestamp}_{app_id}.pkl
        └── ...
```

## Configuration Tips

**misfire_grace_time**: Set to at least 30s to account for S3 round-trip latency:

```python
scheduler = BackgroundScheduler(
    timezone='UTC',
    job_defaults={'misfire_grace_time': 30}
)
```

**replace_existing**: Use when restarting apps that re-declare jobs:

```python
scheduler.add_job(func, 'interval', minutes=5,
                  id='task::worker-1', jobstore='shared',
                  replace_existing=True)
```

**Polling frequency**: The scheduler's wakeup interval determines how often S3 is polled. For minute-level scheduling, the default is fine. For tighter intervals, be aware of S3 latency (~50-200ms per call).

## Limitations

- **Interval triggers only for broadcast**: The independent fire-time computation works with interval triggers. Other trigger types (cron, date) fall back to S3's `next_run_time` for broadcast jobs.
- **No competing execution**: Wildcard targets always mean "all matching run it". There's no "first one wins" mode.
- **No reassignment**: If a targeted instance goes down, its jobs stop running. No automatic failover.
- **Pickle serialization**: Job functions must be importable by reference (same as Redis/MongoDB stores).
- **In-memory tracking**: Broadcast deduplication state is lost on restart. An instance may re-execute one fire time after restart.

## Cost

With ≤20 jobs and minute-level polling:
- ~21 requests per poll (1 LIST + 20 GET)
- At 1 poll/minute: ~30K requests/month ≈ **$0.01/month**
