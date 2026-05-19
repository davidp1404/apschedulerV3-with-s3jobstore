from __future__ import absolute_import

from datetime import datetime
from fnmatch import fnmatch

from pytz import utc
import six

from apscheduler.jobstores.base import BaseJobStore, JobLookupError, ConflictingIdError
from apscheduler.util import datetime_to_utc_timestamp, utc_timestamp_to_datetime
from apscheduler.job import Job

try:
    import cPickle as pickle
except ImportError:
    import pickle

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    raise ImportError('S3JobStore requires boto3 installed')


class S3JobStore(BaseJobStore):
    """
    Stores jobs in an Amazon S3 bucket. Supports multi-instance job targeting via glob patterns.

    Plugin alias: ``s3``

    :param str bucket: S3 bucket name
    :param str prefix: key prefix for all objects (default: 'apscheduler')
    :param str app_id: unique identifier for this application instance
    :param int pickle_protocol: pickle protocol level to use (defaults to highest available)
    :param dict boto3_kwargs: additional keyword arguments passed to boto3.client('s3', ...)
    """

    TARGET_SEPARATOR = '::'

    def __init__(self, bucket, app_id, prefix='apscheduler',
                 pickle_protocol=pickle.HIGHEST_PROTOCOL, **boto3_kwargs):
        super(S3JobStore, self).__init__()
        if not bucket:
            raise ValueError('The "bucket" parameter must not be empty')
        if not app_id:
            raise ValueError('The "app_id" parameter must not be empty')

        self.bucket = bucket
        self.prefix = prefix.rstrip('/')
        self.app_id = app_id
        self.pickle_protocol = pickle_protocol
        self.boto3_kwargs = boto3_kwargs
        self._s3 = None
        self._cache = {}  # job_id -> envelope
        self._last_executed = {}  # job_id -> last fire time we executed

    def start(self, scheduler, alias):
        super(S3JobStore, self).start(scheduler, alias)
        self._s3 = boto3.client('s3', **self.boto3_kwargs)

    def shutdown(self):
        self._cache = {}
        self._last_executed = {}
        self._s3 = None

    # ------------------------------------------------------------------
    # Target routing helpers
    # ------------------------------------------------------------------

    def _parse_job_id(self, job_id):
        """Split 'real-id::target' into (real_id, target). Default target is self.app_id."""
        if self.TARGET_SEPARATOR in job_id:
            real_id, target = job_id.rsplit(self.TARGET_SEPARATOR, 1)
            return real_id, target
        return job_id, self.app_id

    def _job_targets_me(self, target_app_id):
        return fnmatch(self.app_id, target_app_id)

    def _is_visible(self, envelope):
        return (envelope['created_by'] == self.app_id or
                self._job_targets_me(envelope['target_app_id']))

    def _is_broadcast(self, envelope):
        """Return True if this job targets multiple instances (wildcard/glob pattern)."""
        target = envelope['target_app_id']
        return '*' in target or '?' in target or '[' in target

    # ------------------------------------------------------------------
    # Internal S3 operations
    # ------------------------------------------------------------------

    def _job_key(self, job_id):
        return '%s/jobs/%s.pkl' % (self.prefix, job_id)

    def _put_job(self, job_id, envelope):
        data = pickle.dumps(envelope, self.pickle_protocol)
        self._s3.put_object(Bucket=self.bucket, Key=self._job_key(job_id), Body=data)

    def _get_job(self, job_id):
        try:
            resp = self._s3.get_object(Bucket=self.bucket, Key=self._job_key(job_id))
            return pickle.loads(resp['Body'].read())
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return None
            raise

    def _delete_job(self, job_id):
        self._s3.delete_object(Bucket=self.bucket, Key=self._job_key(job_id))

    def _list_jobs(self):
        """Return list of job_id strings from S3."""
        prefix = '%s/jobs/' % self.prefix
        job_ids = []
        paginator = self._s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.pkl'):
                    job_id = key[len(prefix):-4]  # strip prefix and .pkl
                    job_ids.append(job_id)
        return job_ids

    def _refresh_cache(self):
        """Rebuild local cache from S3."""
        self._cache = {}
        for job_id in self._list_jobs():
            envelope = self._get_job(job_id)
            if envelope:
                self._cache[job_id] = envelope

    def _reconstitute_job(self, job_id, envelope):
        job_state = envelope['job_state']
        job = Job.__new__(Job)
        job.__setstate__(job_state)
        job._scheduler = self._scheduler
        job._jobstore_alias = self._alias
        return job

    # ------------------------------------------------------------------
    # Execution receipts
    # ------------------------------------------------------------------

    def _write_execution_receipt(self, job_id):
        now = datetime.now(utc)
        ts = now.strftime('%Y%m%dT%H%M%S')
        key = '%s/executions/%s/%s_%s.pkl' % (self.prefix, job_id, ts, self.app_id)
        receipt = {'app_id': self.app_id, 'timestamp': now, 'job_id': job_id}
        data = pickle.dumps(receipt, self.pickle_protocol)
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_executions(self, job_id):
        """Return execution receipts for a job, sorted descending by timestamp."""
        prefix = '%s/executions/%s/' % (self.prefix, job_id)
        receipts = []
        paginator = self._s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                resp = self._s3.get_object(Bucket=self.bucket, Key=obj['Key'])
                receipt = pickle.loads(resp['Body'].read())
                receipts.append(receipt)
        return sorted(receipts, key=lambda r: r['timestamp'], reverse=True)

    # ------------------------------------------------------------------
    # BaseJobStore interface
    # ------------------------------------------------------------------

    def lookup_job(self, job_id):
        envelope = self._cache.get(job_id)
        if envelope:
            return self._reconstitute_job(job_id, envelope)
        return None

    def get_due_jobs(self, now):
        self._refresh_cache()
        now_timestamp = datetime_to_utc_timestamp(now)
        jobs = []
        for job_id, envelope in six.iteritems(self._cache):
            if not self._job_targets_me(envelope['target_app_id']):
                continue
            job_state = envelope['job_state']
            next_run_time = job_state.get('next_run_time')
            if next_run_time is None:
                continue

            if self._is_broadcast(envelope):
                # For broadcast jobs, each instance independently determines due-ness
                # by computing the most recent fire time from the trigger schedule
                last = self._last_executed.get(job_id)
                job = self._reconstitute_job(job_id, envelope)
                trigger = job.trigger

                # Compute the most recent fire time that's <= now
                # For interval triggers: start_date + N*interval where N = floor((now-start)/interval)
                # For other triggers: fall back to S3's next_run_time
                if hasattr(trigger, 'start_date') and hasattr(trigger, 'interval'):
                    from apscheduler.util import timedelta_seconds
                    diff = timedelta_seconds(now - trigger.start_date)
                    if diff < 0:
                        continue
                    n = int(diff / trigger.interval_length)
                    from apscheduler.util import normalize
                    current_fire = normalize(trigger.start_date + trigger.interval * n)
                else:
                    # Non-interval trigger: use S3's next_run_time
                    current_fire = next_run_time

                # Is this fire time one we haven't executed yet?
                if last is not None and current_fire <= last:
                    continue
                if datetime_to_utc_timestamp(current_fire) > now_timestamp:
                    continue

                # Record immediately to prevent duplicate
                self._last_executed[job_id] = current_fire
                job._modify(next_run_time=current_fire)
                jobs.append(job)
            else:
                if datetime_to_utc_timestamp(next_run_time) > now_timestamp:
                    continue
                jobs.append(self._reconstitute_job(job_id, envelope))

        return sorted(jobs, key=lambda j: j.next_run_time)

    def get_next_run_time(self):
        earliest = None
        for job_id, envelope in six.iteritems(self._cache):
            if not self._job_targets_me(envelope['target_app_id']):
                continue
            next_run_time = envelope['job_state'].get('next_run_time')
            if next_run_time is not None:
                if earliest is None or next_run_time < earliest:
                    earliest = next_run_time
        return earliest

    def get_all_jobs(self):
        jobs = []
        for job_id, envelope in six.iteritems(self._cache):
            if not self._is_visible(envelope):
                continue
            jobs.append(self._reconstitute_job(job_id, envelope))
        paused_sort_key = datetime(9999, 12, 31, tzinfo=utc)
        return sorted(jobs, key=lambda j: j.next_run_time or paused_sort_key)

    def add_job(self, job):
        real_id, target = self._parse_job_id(job.id)
        if real_id in self._cache:
            raise ConflictingIdError(real_id)

        # Update the job's id to the real_id (without target suffix)
        if real_id != job.id:
            object.__setattr__(job, 'id', real_id)

        envelope = {
            'target_app_id': target,
            'created_by': self.app_id,
            'job_state': job.__getstate__()
        }
        self._put_job(real_id, envelope)
        self._cache[real_id] = envelope

    def update_job(self, job):
        real_id, target = self._parse_job_id(job.id)
        envelope = self._cache.get(real_id)
        if envelope is None:
            raise JobLookupError(real_id)

        # Normalize the job id
        if real_id != job.id:
            object.__setattr__(job, 'id', real_id)

        # Preserve existing target unless explicitly provided via :: in job.id
        if real_id == job.id:
            target = envelope['target_app_id']

        # Detect trigger advancement for execution receipt
        old_next = envelope['job_state'].get('next_run_time')
        new_next = job.next_run_time
        advanced = (old_next is not None and new_next is not None and new_next > old_next)

        new_envelope = dict(envelope, target_app_id=target, job_state=job.__getstate__())
        is_broadcast = self._is_broadcast(envelope)

        # Only write to S3 if we're the creator (or it's not a broadcast job)
        if not is_broadcast or envelope['created_by'] == self.app_id:
            self._put_job(real_id, new_envelope)

        self._cache[real_id] = new_envelope

        if advanced:
            self._write_execution_receipt(real_id)

    def remove_job(self, job_id):
        if job_id not in self._cache:
            raise JobLookupError(job_id)
        self._delete_job(job_id)
        del self._cache[job_id]

    def remove_all_jobs(self):
        for job_id in list(self._cache.keys()):
            self._delete_job(job_id)
        self._cache = {}

    def __repr__(self):
        return '<%s (bucket=%s, prefix=%s, app_id=%s)>' % (
            self.__class__.__name__, self.bucket, self.prefix, self.app_id)
