## Context

APScheduler's jobstore interface (`BaseJobStore`) defines a contract for persisting and querying scheduled jobs. Existing stores (Redis, MongoDB, SQLAlchemy, ZooKeeper, RethinkDB) all assume a single scheduler instance owns all jobs in the store.

This design introduces a shared S3-backed store where multiple app instances coexist, each with a unique `app_id`. Jobs carry a `target_app_id` (glob pattern) that determines which instances execute them. The store filters jobs locally after fetching from S3.

Key constraints:
- ≤20 jobs total in the store
- Minute-level polling frequency
- Pure S3 (no DynamoDB, SQS, or other AWS services)
- Eventual consistency is acceptable
- Broadcast semantics for wildcard targets (all matching instances execute)

## Goals / Non-Goals

**Goals:**
- Implement `BaseJobStore` interface fully so the store works with any existing scheduler type
- Enable cross-instance job targeting with glob-based patterns
- Provide execution status visibility to job creators
- Keep S3 API usage minimal (cost and latency)
- Zero infrastructure beyond a single S3 bucket

**Non-Goals:**
- Competing/exclusive execution (only one instance runs a wildcard job) — out of scope
- Health checking or job reassignment when target instances are down
- Sub-second job scheduling precision
- Supporting non-pickle serialization formats
- S3 event notifications or push-based discovery

## Decisions

### 1. S3 Object Layout

**Decision**: Flat layout with one object per job plus an executions prefix for receipts.

```
s3://{bucket}/{prefix}/
  ├── jobs/{job_id}.pkl
  └── executions/{job_id}/{timestamp}_{app_id}.pkl
```

**Rationale**: With ≤20 jobs, a flat layout requires only one LIST call + up to 20 GETs per poll. Partitioning by target would complicate wildcard matching (patterns like `"web-*"` don't map to S3 prefixes). The simplicity outweighs any marginal efficiency gain.

**Alternatives considered**:
- Partitioned by target app_id: rejected because wildcard patterns don't map to prefix-based queries
- Single index object: rejected because it creates a write contention point with multiple writers

### 2. Job Envelope Format

**Decision**: Each S3 object stores a pickled dict envelope wrapping the job state:

```python
{
    "target_app_id": "web-*",      # glob pattern
    "created_by": "web-1",         # app_id of creator
    "job_state": { ... }           # job.__getstate__() dict
}
```

**Rationale**: Keeps routing metadata separate from the Job class (no core modifications needed). The envelope is pickled as a unit — simple to serialize/deserialize.

**Alternatives considered**:
- S3 object metadata for target: rejected because you still need GET for the job state, and it splits truth across two locations
- Modifying the Job class: rejected to avoid touching APScheduler core

### 3. Target Specification via Job ID Convention

**Decision**: Encode target in the job ID using `::` separator: `"my-job::web-*"`. If no separator present, target defaults to the store's `app_id` (self-targeting).

**Rationale**: This is the simplest approach that requires zero changes to the scheduler's `add_job()` API. The store strips the target from the ID before storage, so the actual job_id in S3 remains clean. Users who don't need cross-targeting just use normal job IDs.

**Alternatives considered**:
- Thread-local / context manager: too magical, hard to debug
- Custom `add_job_for()` method: doesn't integrate with scheduler's `add_job()` flow
- Encoding in kwargs: pollutes the callable's arguments

### 4. Polling with Local Cache

**Decision**: On each `get_due_jobs()` call, refresh from S3 (LIST + GET all). Cache results locally for `get_all_jobs()`, `lookup_job()`, and `get_next_run_time()` between polls.

**Rationale**: The scheduler calls `get_due_jobs()` on its wakeup cycle (configured to minutes). Other methods (`lookup_job`, `get_next_run_time`) are called between polls and should use cached data to avoid redundant S3 calls. With ≤20 jobs, fetching everything on each poll is cheap (~$0.01/month).

### 5. Broadcast Execution and Trigger Advancement

**Decision**: For broadcast jobs (target matches multiple instances), each instance independently computes whether the job is due by deriving the most recent fire time from the trigger's schedule (using `start_date` and `interval`). This is compared against a local `_last_executed` dict to avoid re-execution. Only the job **creator** advances the trigger in S3.

For exact-target jobs (target matches only one instance), the normal behavior applies: the single executor advances the trigger in S3.

Mechanism:
- Each store maintains `_last_executed = {job_id: datetime}` in memory
- For broadcast jobs with interval triggers, `get_due_jobs()` computes: `current_fire = start_date + interval * floor((now - start_date) / interval)`
- If `current_fire <= now AND current_fire > _last_executed[job_id]` → job is due
- `_last_executed` is recorded immediately when the job is returned as due (prevents duplicates between `get_due_jobs` and `update_job` calls)
- `update_job()` preserves the existing `target_app_id` from the envelope (doesn't re-parse from job ID)
- Only the creator's store writes the advanced trigger back to S3

**Rationale**: Each instance independently computes the schedule from the trigger definition, completely decoupled from what other instances write to S3. This eliminates all race conditions: it doesn't matter when the creator advances the trigger in S3, because non-creators never read `next_run_time` from S3 for due-ness decisions.

**Alternatives considered**:
- Cache preservation (keep old `next_run_time` locally when S3 advances): rejected because it only works for one hop — if the creator advances twice before a non-creator polls, the non-creator loses track
- All instances advance trigger (last-writer-wins): rejected because S3 latency causes a race where fast pollers advance the trigger before slow pollers see the original fire time
- Using `get_next_fire_time(last, now)`: rejected because it returns `last + interval` which drifts from the aligned schedule

### 6. Execution Receipts

**Decision**: After `update_job()` detects a trigger advancement (next_run_time moved forward), write an execution receipt to `executions/{job_id}/{timestamp}_{app_id}.pkl`. The receipt contains `{app_id, timestamp, success: True}`.

**Rationale**: Enables job creators to observe that their delegated jobs are being executed. Append-only (one object per execution) avoids write contention between broadcast executors. Receipts are lightweight and can be cleaned up with S3 lifecycle rules.

**Limitation**: The store can only confirm execution happened (trigger advanced), not capture return values or exceptions. Full execution result tracking would require executor-level hooks, which is out of scope.

### 7. Visibility Rules

**Decision**:
- `get_due_jobs(now)`: returns jobs where `fnmatch(my_app_id, target_app_id) AND next_run_time <= now`
- `get_all_jobs()`: returns jobs where `created_by == my_app_id OR fnmatch(my_app_id, target_app_id)`
- `lookup_job(job_id)`: returns any job by ID (needed for `update_job` after execution)

**Rationale**: Job creators need visibility into jobs they delegated. Executors need to see jobs targeting them. `lookup_job` is unrestricted because the scheduler calls it internally after execution to update the job.

## Risks / Trade-offs

- **[Missed execution on first poll]** If an instance starts mid-cycle, it computes the most recent fire time from the trigger. If that fire time is <= now and hasn't been executed locally, it fires. → First execution may be slightly delayed (up to one poll interval) but won't be missed.

- **[Local state lost on restart]** The `_last_executed` dict is in-memory only. If an instance restarts, it may re-execute a broadcast job's current fire time. → Acceptable: duplicate execution is better than missed execution for broadcast semantics. The window is small (one fire time at most).

- **[Interval triggers only for broadcast]** The trigger-based fire time computation (`start_date + N*interval`) only works for interval triggers. Other trigger types (cron, date) fall back to S3's `next_run_time` for broadcast due-ness. → Acceptable: interval is the primary use case for broadcast jobs.

- **[Stale cache between polls]** Jobs added by other instances won't be visible until the next poll cycle. → Acceptable per requirements (minute-level discovery is fine).

- **[Pickle security]** Deserializing pickled data from S3 is a security risk if the bucket is compromised. → Mitigated by bucket access controls (IAM policies). Same risk exists in Redis/MongoDB stores.

- **[No execution failure tracking]** The receipt mechanism only confirms execution happened, not whether it succeeded or failed. → Acceptable for v1. Could be extended later with executor hooks.

- **[S3 throttling]** S3 has a 3,500 PUT/5,500 GET per second per prefix limit. → Irrelevant at ≤20 jobs with minute-level polling.

- **[Job ID collision]** The `::` separator in job IDs could conflict with user-chosen IDs. → Document the convention; users must avoid `::` in their base job IDs.
