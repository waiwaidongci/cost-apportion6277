"""核算任务的服务端恢复：崩溃时停在 running 的任务可重新排队，不会重复计时。"""
import time

import redis
from src.models.run import Run
from src.config.settings import REDIS_URL
from src.services.audit import log

STALE_SECONDS = 120


def recover(db, stale=STALE_SECONDS):
    """把长时间停在 running 且没有结果的任务重新排队；已完成/失败的不动。"""
    cutoff = time.time() - stale
    stuck = (db.query(Run)
             .filter(Run.status == 'running', Run.result.is_(None), Run.created <= cutoff)
             .all())
    if not stuck:
        return []
    queue = redis.Redis.from_url(REDIS_URL)
    ids = []
    for job in stuck:
        job.status = 'queued'
        job.error = None
        ids.append(job.id)
    log(db, 'worker', 'run.recover', ','.join(str(i) for i in ids))
    db.commit()
    for job_id in ids:
        queue.lpush('report-jobs', job_id)
    return ids


def claim(db, job_id):
    """原子认领排队任务：仅当状态仍为 queued 时置 running。返回是否由本次执行。

    以单条 UPDATE ... WHERE status='queued' 实现，两个 worker 同时取到同一任务时
    只有一个的受影响行数为 1，因此不会重复计算。
    """
    changed = (db.query(Run)
               .filter(Run.id == job_id, Run.status == 'queued')
               .update({'status': 'running', 'error': None}, synchronize_session=False))
    db.commit()
    return bool(changed)
