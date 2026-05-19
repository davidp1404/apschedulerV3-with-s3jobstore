## ADDED Requirements

### Requirement: Filter due jobs by target match
The S3JobStore SHALL only return jobs from `get_due_jobs()` where the store's `app_id` matches the job's `target_app_id` pattern using `fnmatch`.

#### Scenario: Exact target match
- **WHEN** `get_due_jobs(now)` is called on a store with `app_id='web-1'` and a due job has `target_app_id='web-1'`
- **THEN** the store SHALL include that job in the result

#### Scenario: Wildcard target match
- **WHEN** `get_due_jobs(now)` is called on a store with `app_id='web-1'` and a due job has `target_app_id='web-*'`
- **THEN** the store SHALL include that job in the result

#### Scenario: Broadcast target match
- **WHEN** `get_due_jobs(now)` is called on a store with `app_id='web-1'` and a due job has `target_app_id='*'`
- **THEN** the store SHALL include that job in the result

#### Scenario: Non-matching target
- **WHEN** `get_due_jobs(now)` is called on a store with `app_id='web-1'` and a due job has `target_app_id='worker-1'`
- **THEN** the store SHALL NOT include that job in the result

### Requirement: Extract target from job ID convention
The S3JobStore SHALL parse the job ID for a `::` separator to extract the target. The portion before `::` is the job ID; the portion after is the `target_app_id`.

#### Scenario: Job ID with target separator
- **WHEN** `add_job(job)` is called with `job.id = 'refresh-cache::web-*'`
- **THEN** the store SHALL store the job with `job_id='refresh-cache'` and `target_app_id='web-*'`

#### Scenario: Job ID without target separator
- **WHEN** `add_job(job)` is called with `job.id = 'my-task'`
- **THEN** the store SHALL store the job with `job_id='my-task'` and `target_app_id` set to the store's own `app_id`

### Requirement: Visibility includes created and targeted jobs
The S3JobStore SHALL return jobs from `get_all_jobs()` where the store's `app_id` matches the `target_app_id` OR the job's `created_by` equals the store's `app_id`.

#### Scenario: Seeing jobs I created for others
- **WHEN** `get_all_jobs()` is called on a store with `app_id='web-1'` and a job has `created_by='web-1'` and `target_app_id='worker-1'`
- **THEN** the store SHALL include that job in the result

#### Scenario: Seeing jobs targeting me from others
- **WHEN** `get_all_jobs()` is called on a store with `app_id='worker-1'` and a job has `created_by='web-1'` and `target_app_id='worker-*'`
- **THEN** the store SHALL include that job in the result

#### Scenario: Not seeing unrelated jobs
- **WHEN** `get_all_jobs()` is called on a store with `app_id='web-1'` and a job has `created_by='web-2'` and `target_app_id='worker-1'`
- **THEN** the store SHALL NOT include that job in the result

### Requirement: Broadcast execution semantics
When a job's `target_app_id` matches multiple app instances, each matching instance SHALL independently execute the job on its schedule using local execution tracking.

#### Scenario: Two instances match a wildcard job
- **WHEN** a job with `target_app_id='web-*'` is due and both `web-1` and `web-2` stores poll
- **THEN** both stores SHALL return the job from `get_due_jobs()` and both schedulers SHALL execute it

#### Scenario: Local tracking prevents missed execution
- **WHEN** instance `web-1` advances the trigger in S3 after executing a broadcast job
- **THEN** instance `web-2` SHALL still execute that same fire time because it computes due-ness independently from the trigger's schedule, not from S3's `next_run_time`

#### Scenario: Local tracking prevents duplicate execution
- **WHEN** instance `web-1` has already executed a broadcast job for fire time 12:00
- **THEN** `web-1` SHALL NOT return that job from `get_due_jobs()` again until the next fire time is due

### Requirement: Only creator advances trigger for broadcast jobs
For broadcast jobs (target matches multiple instances), only the store whose `app_id` matches `created_by` SHALL write the advanced `next_run_time` back to S3.

#### Scenario: Creator advances trigger
- **WHEN** `update_job(job)` is called on a store where `created_by == self.app_id` for a broadcast job
- **THEN** the store SHALL write the updated job state to S3

#### Scenario: Non-creator does not advance trigger in S3
- **WHEN** `update_job(job)` is called on a store where `created_by != self.app_id` for a broadcast job
- **THEN** the store SHALL update its local cache but SHALL NOT write to S3

### Requirement: Due jobs sorted by next run time
The S3JobStore SHALL return due jobs sorted by `next_run_time` ascending.

#### Scenario: Multiple due jobs returned in order
- **WHEN** `get_due_jobs(now)` finds jobs with `next_run_time` of 12:00 and 12:03 (both <= now)
- **THEN** the store SHALL return them ordered [12:00, 12:03]
