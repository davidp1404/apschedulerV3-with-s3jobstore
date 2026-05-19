## ADDED Requirements

### Requirement: Store job state as S3 objects
The S3JobStore SHALL persist each job as a pickled envelope object at the key `{prefix}/jobs/{job_id}.pkl` in the configured S3 bucket.

#### Scenario: Adding a new job
- **WHEN** `add_job(job)` is called with a job that does not exist in the store
- **THEN** the store SHALL PUT an object to `{prefix}/jobs/{job_id}.pkl` containing the pickled envelope `{target_app_id, created_by, job_state}`

#### Scenario: Adding a job with conflicting ID
- **WHEN** `add_job(job)` is called with a job whose ID already exists in S3
- **THEN** the store SHALL raise `ConflictingIdError`

### Requirement: Update existing job state in S3
The S3JobStore SHALL overwrite the S3 object when a job is updated, except for broadcast jobs where the store is not the creator (in which case only the local cache is updated).

#### Scenario: Updating an existing job
- **WHEN** `update_job(job)` is called for a job that exists in the store and the store is the creator or the job is not a broadcast job
- **THEN** the store SHALL PUT the updated envelope to `{prefix}/jobs/{job_id}.pkl`

#### Scenario: Updating a broadcast job as non-creator
- **WHEN** `update_job(job)` is called for a broadcast job where `created_by != self.app_id`
- **THEN** the store SHALL update the local cache but SHALL NOT write to S3

#### Scenario: Updating a non-existent job
- **WHEN** `update_job(job)` is called for a job that does not exist in the store
- **THEN** the store SHALL raise `JobLookupError`

### Requirement: Remove job from S3
The S3JobStore SHALL delete the S3 object when a job is removed.

#### Scenario: Removing an existing job
- **WHEN** `remove_job(job_id)` is called for a job that exists in the store
- **THEN** the store SHALL DELETE the object at `{prefix}/jobs/{job_id}.pkl`

#### Scenario: Removing a non-existent job
- **WHEN** `remove_job(job_id)` is called for a job that does not exist in the store
- **THEN** the store SHALL raise `JobLookupError`

### Requirement: Remove all jobs from S3
The S3JobStore SHALL delete all job objects under the prefix when `remove_all_jobs()` is called.

#### Scenario: Removing all jobs
- **WHEN** `remove_all_jobs()` is called
- **THEN** the store SHALL DELETE all objects matching `{prefix}/jobs/*.pkl`

### Requirement: Poll S3 for job discovery
The S3JobStore SHALL refresh its local cache from S3 on each `get_due_jobs()` call by listing and fetching all job objects.

#### Scenario: Discovering jobs on poll
- **WHEN** `get_due_jobs(now)` is called
- **THEN** the store SHALL LIST all objects under `{prefix}/jobs/` and GET each one to rebuild the local cache

### Requirement: Lookup job by ID
The S3JobStore SHALL return a job by its ID from the local cache.

#### Scenario: Looking up an existing job
- **WHEN** `lookup_job(job_id)` is called for a job present in the cache
- **THEN** the store SHALL return the reconstituted Job object

#### Scenario: Looking up a non-existent job
- **WHEN** `lookup_job(job_id)` is called for a job not in the cache
- **THEN** the store SHALL return `None`

### Requirement: Initialize with boto3 S3 client
The S3JobStore SHALL accept `bucket`, `prefix`, and `app_id` as required constructor parameters and optional `boto3_kwargs` for client configuration.

#### Scenario: Constructing the store
- **WHEN** `S3JobStore(bucket='my-bucket', prefix='scheduler', app_id='web-1')` is instantiated
- **THEN** the store SHALL create a boto3 S3 client and be ready for operations after `start()` is called
