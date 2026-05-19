## Why

APScheduler currently has no jobstore that enables multiple independent application instances to share job definitions without requiring a database or message broker. An S3-backed jobstore allows distributed apps to define jobs targeting specific instances (or groups via wildcards), using only an S3 bucket as infrastructure — cheap, serverless, and universally available in AWS environments.

## What Changes

- New `S3JobStore` class implementing `BaseJobStore`, using a single S3 bucket for persistence
- Jobs carry a `target_app_id` field (glob pattern) determining which app instance(s) execute them
- Each store instance is initialized with an `app_id`; it filters jobs by target match and ownership
- Wildcard targets (e.g., `"web-*"`, `"*"`) use broadcast semantics — all matching instances execute the job
- Execution receipts are written to S3 so job creators can observe execution status of jobs they delegated
- Polling-based discovery with local caching between polls

## Capabilities

### New Capabilities
- `s3-storage`: S3 bucket read/write operations for job persistence (PUT, GET, LIST, DELETE of job objects)
- `target-routing`: App-id-based job targeting with fnmatch glob patterns and broadcast execution semantics
- `execution-status`: Execution receipt tracking so job creators can observe remote execution outcomes

### Modified Capabilities

## Impact

- **New dependency**: `boto3` (AWS SDK for Python)
- **New files**: `apscheduler/jobstores/s3.py`
- **Tests**: New test module `tests/test_jobstores_s3.py`
- **No breaking changes**: Existing jobstores and scheduler behavior are unaffected
- **Serialization**: Uses pickle (consistent with existing Redis/MongoDB stores)
- **API surface**: Users specify target via job ID convention (`"job-name::target-pattern"`) or store default
