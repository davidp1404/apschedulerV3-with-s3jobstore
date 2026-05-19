# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "apscheduler[s3]",
#     "setuptools",
# ]
#
# [tool.uv.sources]
# apscheduler = { path = ".", editable = true }
# ///
#
# Run with:
#   pip install -e ".[s3]" && python sample1.py
#
# Note: `uv run` script mode doesn't work here because APScheduler
# uses pkg_resources at runtime, which uv excludes from isolated envs.
"""Two app instances sharing jobs via MinIO playground."""
import sys
import time
from datetime import datetime

import boto3
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.s3 import S3JobStore

MINIO_ENDPOINT = 'https://play.min.io:9000'
MINIO_ACCESS_KEY = 'minioadmin'
MINIO_SECRET_KEY = 'minioadmin'
BUCKET = 'apscheduler-test'
PREFIX = 'scheduler'


def task_for_worker():
    print('[worker-1] Executing task at %s' % datetime.now(), flush=True)


def task_for_all():
    import threading
    print('[broadcast] executed by thread=%s at %s' % (threading.current_thread().name, datetime.now()), flush=True)


def ensure_bucket():
    s3 = boto3.client('s3',
                      endpoint_url=MINIO_ENDPOINT,
                      aws_access_key_id=MINIO_ACCESS_KEY,
                      aws_secret_access_key=MINIO_SECRET_KEY)
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:
        s3.create_bucket(Bucket=BUCKET)


def make_store(app_id):
    return S3JobStore(
        bucket=BUCKET,
        app_id=app_id,
        prefix=PREFIX,
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def main():
    ensure_bucket()

    scheduler_a = BackgroundScheduler(timezone='UTC',
                                      job_defaults={'misfire_grace_time': 30})
    store_a = make_store('web-1')
    scheduler_a.add_jobstore(store_a, 'shared')

    scheduler_b = BackgroundScheduler(timezone='UTC',
                                      job_defaults={'misfire_grace_time': 30})
    store_b = make_store('worker-1')
    scheduler_b.add_jobstore(store_b, 'shared')

    scheduler_a.start()
    scheduler_b.start()

    # Clean previous runs
    store_a.remove_all_jobs()

    scheduler_a.add_job(task_for_worker, 'interval', seconds=10,
                        id='do-work::worker-1', jobstore='shared',
                        replace_existing=True, misfire_grace_time=30)
    scheduler_a.add_job(task_for_all, 'interval', seconds=15,
                        id='refresh::*', jobstore='shared',
                        replace_existing=True, misfire_grace_time=30)

    # Give store_b time to discover jobs on next poll
    time.sleep(2)
    store_b._refresh_cache()

    print("web-1 sees:", [j.id for j in store_a.get_all_jobs()], flush=True)
    print("worker-1 sees:", [j.id for j in store_b.get_all_jobs()], flush=True)
    print("Waiting for jobs to fire (10-15s intervals)...", flush=True)

    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        scheduler_a.shutdown()
        scheduler_b.shutdown()


if __name__ == '__main__':
    main()
