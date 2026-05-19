from datetime import datetime, timedelta

import pytest
import pytz

from apscheduler.jobstores.base import JobLookupError, ConflictingIdError
from apscheduler.jobstores.s3 import S3JobStore
from apscheduler.job import Job
from apscheduler.schedulers.blocking import BlockingScheduler

try:
    from unittest.mock import Mock, patch
except ImportError:
    from mock import Mock, patch

moto = pytest.importorskip('moto')
import boto3
from moto import mock_aws


BUCKET = 'test-bucket'
PREFIX = 'scheduler'


def dummy_job():
    pass


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client('s3', region_name='us-east-1')
        client.create_bucket(Bucket=BUCKET)
        yield


@pytest.fixture
def store(s3_bucket):
    with mock_aws():
        client = boto3.client('s3', region_name='us-east-1')
        client.create_bucket(Bucket=BUCKET)
        s = S3JobStore(bucket=BUCKET, app_id='web-1', prefix=PREFIX,
                       region_name='us-east-1')
        scheduler = Mock(BlockingScheduler, timezone=pytz.utc)
        s.start(scheduler, 's3')
        yield s
        s.shutdown()


@pytest.fixture
def create_job():
    def _create(job_id='test-job', next_run_time=None):
        scheduler = Mock(BlockingScheduler, timezone=pytz.utc)
        run_date = next_run_time or datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        trigger = BlockingScheduler()._create_trigger('date',
                                                      {'run_date': run_date, 'timezone': pytz.utc})
        job = Job(scheduler, id=job_id, func='tests.test_jobstores_s3:dummy_job',
                  trigger=trigger, executor='default', args=(), kwargs={},
                  misfire_grace_time=1, coalesce=False, name='test', max_instances=1,
                  next_run_time=run_date)
        return job
    return _create


class TestAddUpdateRemoveLookup:
    """Test add/update/remove/lookup operations (task 6.2)."""

    def test_add_job(self, store, create_job):
        job = create_job('my-job')
        store.add_job(job)
        result = store.lookup_job('my-job')
        assert result is not None
        assert result.id == 'my-job'

    def test_add_job_conflicting_id(self, store, create_job):
        job1 = create_job('dup-job')
        job2 = create_job('dup-job')
        store.add_job(job1)
        with pytest.raises(ConflictingIdError):
            store.add_job(job2)

    def test_update_job(self, store, create_job):
        job = create_job('upd-job', datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc))
        store.add_job(job)
        new_time = datetime(2024, 1, 2, 12, 0, tzinfo=pytz.utc)
        job._modify(next_run_time=new_time)
        store.update_job(job)
        result = store.lookup_job('upd-job')
        assert result.next_run_time == new_time

    def test_update_nonexistent_job(self, store, create_job):
        job = create_job('ghost')
        with pytest.raises(JobLookupError):
            store.update_job(job)

    def test_remove_job(self, store, create_job):
        job = create_job('rm-job')
        store.add_job(job)
        store.remove_job('rm-job')
        assert store.lookup_job('rm-job') is None

    def test_remove_nonexistent_job(self, store):
        with pytest.raises(JobLookupError):
            store.remove_job('nope')

    def test_remove_all_jobs(self, store, create_job):
        store.add_job(create_job('j1'))
        store.add_job(create_job('j2'))
        store.remove_all_jobs()
        assert store.get_all_jobs() == []

    def test_lookup_nonexistent(self, store):
        assert store.lookup_job('missing') is None


class TestTargetRouting:
    """Test target routing: exact match, wildcard, broadcast, non-match (task 6.3)."""

    def test_exact_match(self, store, create_job):
        job = create_job('task::web-1')
        store.add_job(job)
        assert store._job_targets_me('web-1') is True

    def test_wildcard_match(self, store, create_job):
        assert store._job_targets_me('web-*') is True

    def test_broadcast_match(self, store, create_job):
        assert store._job_targets_me('*') is True

    def test_non_match(self, store, create_job):
        assert store._job_targets_me('worker-1') is False

    def test_due_jobs_filters_by_target(self, store, create_job):
        # Add a job targeting someone else by manipulating cache directly
        now = datetime(2024, 6, 1, 12, 0, tzinfo=pytz.utc)
        job = create_job('mine', datetime(2024, 6, 1, 11, 0, tzinfo=pytz.utc))
        store.add_job(job)

        # Manually insert a job targeting worker-1
        other_job = create_job('other', datetime(2024, 6, 1, 11, 0, tzinfo=pytz.utc))
        envelope = {
            'target_app_id': 'worker-1',
            'created_by': 'web-2',
            'job_state': other_job.__getstate__()
        }
        store._put_job('other', envelope)

        due = store.get_due_jobs(now)
        ids = [j.id for j in due]
        assert 'mine' in ids
        assert 'other' not in ids


class TestVisibility:
    """Test visibility rules in get_all_jobs() (task 6.4)."""

    def test_sees_jobs_created_by_me(self, store, create_job):
        # Job I created targeting someone else
        job = create_job('delegated::worker-1')
        store.add_job(job)
        jobs = store.get_all_jobs()
        assert any(j.id == 'delegated' for j in jobs)

    def test_sees_jobs_targeting_me(self, store, create_job):
        # Manually insert a job from another app targeting me
        job = create_job('incoming', datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc))
        envelope = {
            'target_app_id': 'web-*',
            'created_by': 'web-2',
            'job_state': job.__getstate__()
        }
        store._put_job('incoming', envelope)
        store._cache['incoming'] = envelope
        jobs = store.get_all_jobs()
        assert any(j.id == 'incoming' for j in jobs)

    def test_does_not_see_unrelated_jobs(self, store, create_job):
        job = create_job('hidden', datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc))
        envelope = {
            'target_app_id': 'worker-1',
            'created_by': 'web-2',
            'job_state': job.__getstate__()
        }
        store._put_job('hidden', envelope)
        store._cache['hidden'] = envelope
        jobs = store.get_all_jobs()
        assert not any(j.id == 'hidden' for j in jobs)


class TestGetDueJobs:
    """Test get_due_jobs() filtering and sorting (task 6.5)."""

    def test_returns_due_jobs_sorted(self, store, create_job):
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        t2 = datetime(2024, 1, 1, 12, 5, tzinfo=pytz.utc)
        store.add_job(create_job('later', t2))
        store.add_job(create_job('earlier', t1))
        now = datetime(2024, 1, 1, 13, 0, tzinfo=pytz.utc)
        due = store.get_due_jobs(now)
        assert [j.id for j in due] == ['earlier', 'later']

    def test_excludes_not_yet_due(self, store, create_job):
        future = datetime(2099, 1, 1, 12, 0, tzinfo=pytz.utc)
        store.add_job(create_job('future-job', future))
        now = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        assert store.get_due_jobs(now) == []

    def test_get_next_run_time(self, store, create_job):
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        t2 = datetime(2024, 1, 1, 14, 0, tzinfo=pytz.utc)
        store.add_job(create_job('a', t1))
        store.add_job(create_job('b', t2))
        assert store.get_next_run_time() == t1


class TestExecutionReceipts:
    """Test execution receipt writing and retrieval (task 6.6)."""

    def test_receipt_written_on_trigger_advance(self, store, create_job):
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        t2 = datetime(2024, 1, 1, 12, 5, tzinfo=pytz.utc)
        job = create_job('exec-job', t1)
        store.add_job(job)
        job._modify(next_run_time=t2)
        store.update_job(job)
        receipts = store.get_executions('exec-job')
        assert len(receipts) == 1
        assert receipts[0]['app_id'] == 'web-1'
        assert receipts[0]['job_id'] == 'exec-job'

    def test_no_receipt_without_advancement(self, store, create_job):
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        job = create_job('no-adv', t1)
        store.add_job(job)
        # Update without changing next_run_time
        store.update_job(job)
        receipts = store.get_executions('no-adv')
        assert len(receipts) == 0

    def test_no_executions_returns_empty(self, store):
        assert store.get_executions('nonexistent') == []


class TestJobIdParsing:
    """Test job ID parsing with and without :: separator (task 6.7)."""

    def test_parse_with_target(self, store):
        job_id, target = store._parse_job_id('my-task::worker-*')
        assert job_id == 'my-task'
        assert target == 'worker-*'

    def test_parse_without_target(self, store):
        job_id, target = store._parse_job_id('simple-task')
        assert job_id == 'simple-task'
        assert target == 'web-1'  # defaults to store's app_id

    def test_parse_with_multiple_separators(self, store):
        job_id, target = store._parse_job_id('ns::task::web-*')
        assert job_id == 'ns::task'
        assert target == 'web-*'

    def test_add_job_strips_target_from_id(self, store, create_job):
        job = create_job('targeted::worker-1')
        store.add_job(job)
        assert store.lookup_job('targeted') is not None
        assert store.lookup_job('targeted::worker-1') is None


class TestBroadcastLocalTracking:
    """Test that broadcast jobs use local tracking to prevent missed/duplicate execution."""

    def test_broadcast_not_returned_after_local_execution(self, store, create_job):
        """Once a broadcast job's fire time is recorded locally, it's not returned again."""
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        t2 = datetime(2024, 1, 1, 12, 5, tzinfo=pytz.utc)
        job = create_job('bcast::*', t1)
        store.add_job(job)

        # First poll: job is due
        now = datetime(2024, 1, 1, 12, 1, tzinfo=pytz.utc)
        due = store.get_due_jobs(now)
        assert len(due) == 1

        # Simulate execution: advance trigger
        due[0]._modify(next_run_time=t2)
        store.update_job(due[0])

        # Second poll with same now: should NOT return it again
        due = store.get_due_jobs(now)
        assert len(due) == 0

    def test_broadcast_returned_for_new_fire_time(self, store, create_job):
        """After trigger advances, the new fire time is returned when due."""
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        t2 = datetime(2024, 1, 1, 12, 5, tzinfo=pytz.utc)
        job = create_job('bcast::*', t1)
        store.add_job(job)

        # Execute and advance
        now = datetime(2024, 1, 1, 12, 1, tzinfo=pytz.utc)
        due = store.get_due_jobs(now)
        due[0]._modify(next_run_time=t2)
        store.update_job(due[0])

        # Poll after new fire time: should return it
        now2 = datetime(2024, 1, 1, 12, 6, tzinfo=pytz.utc)
        due = store.get_due_jobs(now2)
        assert len(due) == 1

    def test_non_creator_skips_s3_write(self, store, create_job):
        """A non-creator store updates cache but doesn't write to S3 for broadcast jobs."""
        t1 = datetime(2024, 1, 1, 12, 0, tzinfo=pytz.utc)
        t2 = datetime(2024, 1, 1, 12, 5, tzinfo=pytz.utc)

        # Simulate a job created by another app
        job = create_job('remote', t1)
        envelope = {
            'target_app_id': '*',
            'created_by': 'other-app',
            'job_state': job.__getstate__()
        }
        store._put_job('remote', envelope)
        store._cache['remote'] = envelope

        # Execute and advance
        reconstituted = store._reconstitute_job('remote', envelope)
        reconstituted._modify(next_run_time=t2)
        store.update_job(reconstituted)

        # S3 should still have the original (not advanced) because we're not the creator
        s3_envelope = store._get_job('remote')
        s3_next = s3_envelope['job_state']['next_run_time']
        assert s3_next == t1
