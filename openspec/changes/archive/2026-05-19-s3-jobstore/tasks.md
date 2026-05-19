## 1. Module Setup

- [x] 1.1 Create `apscheduler/jobstores/s3.py` with S3JobStore class skeleton extending BaseJobStore
- [x] 1.2 Add `boto3` to optional dependencies in `setup.py` (as `s3` extra)

## 2. Core S3 Operations

- [x] 2.1 Implement constructor (`__init__`) accepting `bucket`, `prefix`, `app_id`, `pickle_protocol`, and optional `boto3_kwargs`
- [x] 2.2 Implement `start()` to initialize the boto3 S3 client
- [x] 2.3 Implement `shutdown()` to clear local cache
- [x] 2.4 Implement internal `_put_job(job_id, envelope)` to PUT a pickled envelope to S3
- [x] 2.5 Implement internal `_get_job(job_id)` to GET and unpickle an envelope from S3
- [x] 2.6 Implement internal `_delete_job(job_id)` to DELETE an object from S3
- [x] 2.7 Implement internal `_list_jobs()` to LIST all objects under `{prefix}/jobs/`
- [x] 2.8 Implement internal `_refresh_cache()` to LIST + GET all jobs and rebuild local cache

## 3. Target Routing

- [x] 3.1 Implement `_parse_job_id(job_id)` to split on `::` separator, returning `(job_id, target_app_id)`
- [x] 3.2 Implement `_job_targets_me(target_app_id)` using `fnmatch` to match against `self.app_id`
- [x] 3.3 Implement `_is_visible(envelope)` returning True if `created_by == self.app_id` OR target matches

## 4. BaseJobStore Interface

- [x] 4.1 Implement `add_job(job)` — parse target from ID, build envelope, check for conflicts, PUT to S3
- [x] 4.2 Implement `update_job(job)` — verify exists, PUT updated envelope, detect trigger advancement
- [x] 4.3 Implement `remove_job(job_id)` — verify exists, DELETE from S3, update cache
- [x] 4.4 Implement `remove_all_jobs()` — LIST and DELETE all job objects
- [x] 4.5 Implement `lookup_job(job_id)` — return from local cache
- [x] 4.6 Implement `get_due_jobs(now)` — refresh cache, filter by target match + due time, sort ascending
- [x] 4.7 Implement `get_next_run_time()` — return earliest next_run_time from cache (target-matched jobs only)
- [x] 4.8 Implement `get_all_jobs()` — return visible jobs (created by me OR targeting me), sorted

## 5. Execution Status

- [x] 5.1 Implement `_write_execution_receipt(job_id)` to PUT receipt object at `{prefix}/executions/{job_id}/{timestamp}_{app_id}.pkl`
- [x] 5.2 Integrate receipt writing into `update_job()` when trigger advancement is detected
- [x] 5.3 Implement `get_executions(job_id)` to LIST and GET receipts, return sorted descending by timestamp

## 6. Tests

- [x] 6.1 Create `tests/test_jobstores_s3.py` with mocked S3 (using `moto` or `botocore.stub`)
- [x] 6.2 Test add/update/remove/lookup operations
- [x] 6.3 Test target routing: exact match, wildcard, broadcast, non-match
- [x] 6.4 Test visibility rules in `get_all_jobs()`
- [x] 6.5 Test `get_due_jobs()` filtering and sorting
- [x] 6.6 Test execution receipt writing and retrieval
- [x] 6.7 Test job ID parsing with and without `::` separator
- [x] 6.8 E2E test with two BackgroundScheduler instances sharing a bucket: cross-instance targeting, broadcast execution, and visibility rules

## 7. Broadcast Execution Fix

- [x] 7.1 Add `_last_executed` dict to store instance for local fire-time tracking
- [x] 7.2 Add `_is_broadcast(envelope)` helper to detect if a job targets multiple instances
- [x] 7.3 Update `get_due_jobs()` to compute broadcast due-ness from trigger schedule (`start_date + N*interval`) instead of S3's `next_run_time`
- [x] 7.4 Record `_last_executed` immediately in `get_due_jobs()` when returning a broadcast job (prevents duplicates between poll and update)
- [x] 7.5 Update `update_job()` to skip S3 write for broadcast jobs when `created_by != self.app_id`
- [x] 7.6 Fix `update_job()` to preserve existing `target_app_id` from envelope instead of re-parsing from job ID
- [x] 7.7 Update e2e test `test_broadcast_execution` to verify both instances actually execute
- [x] 7.8 Add unit test for local tracking preventing duplicate execution and non-creator S3 write skip
