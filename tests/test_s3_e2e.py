"""End-to-end test: two app instances sharing jobs via S3.

Uses moto mock_aws started before any boto3 clients are created.
"""
import os
import time
from datetime import datetime

import pytest
import pytz

moto = pytest.importorskip('moto')
import boto3
from moto import mock_aws

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.s3 import S3JobStore

BUCKET = 'e2e-test-bucket'
PREFIX = 'scheduler'

executed = []


def task_for_worker():
    executed.append(('worker-task', 'worker-1', datetime.now(pytz.utc)))


def task_for_all():
    executed.append(('broadcast-task', 'any', datetime.now(pytz.utc)))


@pytest.fixture(autouse=True)
def mock_s3():
    os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'
    os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
    os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'
    m = mock_aws()
    m.start()
    boto3.client('s3', region_name='us-east-1').create_bucket(Bucket=BUCKET)
    yield
    m.stop()
    os.environ.pop('AWS_DEFAULT_REGION', None)
    os.environ.pop('AWS_ACCESS_KEY_ID', None)
    os.environ.pop('AWS_SECRET_ACCESS_KEY', None)


def test_cross_instance_job_targeting():
    """App A creates a job, App B discovers and executes it."""
    executed.clear()

    store_a = S3JobStore(bucket=BUCKET, app_id='web-1', prefix=PREFIX,
                         region_name='us-east-1')
    store_b = S3JobStore(bucket=BUCKET, app_id='worker-1', prefix=PREFIX,
                         region_name='us-east-1')

    scheduler_a = BackgroundScheduler(timezone='UTC',
                                      job_defaults={'misfire_grace_time': 60})
    scheduler_a.add_jobstore(store_a, 'shared')
    scheduler_a.start()

    scheduler_b = BackgroundScheduler(timezone='UTC',
                                      job_defaults={'misfire_grace_time': 60})
    scheduler_b.add_jobstore(store_b, 'shared')
    scheduler_b.start()

    try:
        # web-1 creates a job targeting worker-1
        scheduler_a.add_job(
            'tests.test_s3_e2e:task_for_worker', 'interval', seconds=2,
            id='work::worker-1', jobstore='shared', misfire_grace_time=60)

        time.sleep(6)

        # worker-1 should have executed the job
        worker_execs = [e for e in executed if e[0] == 'worker-task']
        assert len(worker_execs) > 0, f"worker-task not executed. executed={executed}"

        # web-1 should see the job (it created it)
        a_jobs = [j.id for j in store_a.get_all_jobs()]
        assert 'work' in a_jobs

        # worker-1 should see the job (it targets worker-1)
        b_jobs = [j.id for j in store_b.get_all_jobs()]
        assert 'work' in b_jobs

        # Execution receipts should exist
        receipts = store_a.get_executions('work')
        assert len(receipts) > 0
        assert receipts[0]['app_id'] == 'worker-1'

    finally:
        scheduler_a.shutdown(wait=False)
        scheduler_b.shutdown(wait=False)


def test_broadcast_execution():
    """A broadcast job (target='*') is executed by all matching instances."""
    executed.clear()

    store_a = S3JobStore(bucket=BUCKET, app_id='web-1', prefix=PREFIX,
                         region_name='us-east-1')
    store_b = S3JobStore(bucket=BUCKET, app_id='worker-1', prefix=PREFIX,
                         region_name='us-east-1')

    scheduler_a = BackgroundScheduler(timezone='UTC',
                                      job_defaults={'misfire_grace_time': 60})
    scheduler_a.add_jobstore(store_a, 'shared')
    scheduler_a.start()

    scheduler_b = BackgroundScheduler(timezone='UTC',
                                      job_defaults={'misfire_grace_time': 60})
    scheduler_b.add_jobstore(store_b, 'shared')
    scheduler_b.start()

    try:
        # web-1 creates a broadcast job
        scheduler_a.add_job(
            'tests.test_s3_e2e:task_for_all', 'interval', seconds=1,
            id='bcast::*', jobstore='shared', misfire_grace_time=60)

        time.sleep(4)

        # Both instances should have executed
        bcast_execs = [e for e in executed if e[0] == 'broadcast-task']
        assert len(bcast_execs) >= 2, \
            f"Expected broadcast to run on multiple instances, got {len(bcast_execs)}"

    finally:
        scheduler_a.shutdown(wait=False)
        scheduler_b.shutdown(wait=False)


def test_visibility_rules():
    """Jobs are visible to creator and target, not to unrelated instances."""
    store_a = S3JobStore(bucket=BUCKET, app_id='web-1', prefix=PREFIX,
                         region_name='us-east-1')
    store_b = S3JobStore(bucket=BUCKET, app_id='worker-1', prefix=PREFIX,
                         region_name='us-east-1')
    store_c = S3JobStore(bucket=BUCKET, app_id='other-1', prefix=PREFIX,
                         region_name='us-east-1')

    scheduler = BackgroundScheduler(timezone='UTC',
                                    job_defaults={'misfire_grace_time': 60})
    scheduler.add_jobstore(store_a, 'shared')
    scheduler.start()

    try:
        # Start standalone stores for querying
        store_b.start(None, 'shared')
        store_c.start(None, 'shared')
        # web-1 creates a job targeting worker-1
        scheduler.add_job(
            'tests.test_s3_e2e:task_for_worker', 'interval', seconds=60,
            id='private::worker-1', jobstore='shared', misfire_grace_time=60)

        # Refresh other stores
        store_b._refresh_cache()
        store_c._refresh_cache()

        # web-1 sees it (creator)
        assert 'private' in [j.id for j in store_a.get_all_jobs()]

        # worker-1 sees it (target)
        assert 'private' in [j.id for j in store_b.get_all_jobs()]

        # other-1 does NOT see it
        assert 'private' not in [j.id for j in store_c.get_all_jobs()]

    finally:
        scheduler.shutdown(wait=False)
