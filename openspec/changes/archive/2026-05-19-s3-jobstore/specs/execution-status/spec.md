## ADDED Requirements

### Requirement: Write execution receipt on trigger advancement
The S3JobStore SHALL write an execution receipt to S3 when `update_job()` detects that `next_run_time` has advanced forward compared to the cached version.

#### Scenario: Trigger advances after execution
- **WHEN** `update_job(job)` is called and the job's `next_run_time` is later than the previously cached `next_run_time`
- **THEN** the store SHALL PUT an object at `{prefix}/executions/{job_id}/{timestamp}_{app_id}.pkl` containing `{app_id, timestamp, job_id}`

#### Scenario: Job updated without trigger advancement
- **WHEN** `update_job(job)` is called and the job's `next_run_time` has not advanced (e.g., only name changed)
- **THEN** the store SHALL NOT write an execution receipt

### Requirement: Retrieve execution receipts for a job
The S3JobStore SHALL provide a method `get_executions(job_id)` that lists execution receipts for a given job.

#### Scenario: Listing executions for a job with history
- **WHEN** `get_executions(job_id)` is called for a job that has been executed 3 times
- **THEN** the store SHALL return a list of 3 receipt dicts sorted by timestamp descending, each containing `{app_id, timestamp, job_id}`

#### Scenario: Listing executions for a job with no history
- **WHEN** `get_executions(job_id)` is called for a job that has never been executed
- **THEN** the store SHALL return an empty list

### Requirement: Execution receipts are append-only
Each execution by each app instance SHALL create a separate S3 object. Receipts SHALL NOT overwrite each other.

#### Scenario: Broadcast job executed by two instances
- **WHEN** a broadcast job is executed by both `web-1` and `web-2` at the same scheduled time
- **THEN** two distinct receipt objects SHALL exist: `{timestamp}_web-1.pkl` and `{timestamp}_web-2.pkl`
