import time
import traceback
from pathlib import Path

import redis

from src.config.database import Base, engine, Session
from src.config.settings import REDIS_URL
from src.models import identity, source, run, worktime
from src.models.run import Run
from src.services.calculation import calculate
from src.services import ledger, recovery
from src.services.delivery import tick

Base.metadata.create_all(engine)
r = redis.Redis.from_url(REDIS_URL)
last_recover = 0.0

while True:
    Path('/tmp/worker-heartbeat').write_text(str(time.time()))
    try:
        with Session() as db:
            ledger.apply_pending(db, actor='worker')
        item = r.lpop('report-jobs')
        if item:
            with Session() as db:
                if recovery.claim(db, int(item)):
                    job = db.get(Run, int(item))
                    try:
                        snapshot = job.snapshot or {}
                        job.result = calculate(snapshot['sources'], snapshot['policy'],
                                               snapshot.get('worktime'))
                        job.status = 'completed'
                        job.error = None
                    except Exception as exc:
                        job.status = 'failed'
                        job.error = str(exc)
                        traceback.print_exc()
                    db.commit()
                else:
                    db.rollback()
        with Session() as db:
            if time.time() - last_recover > 30:
                last_recover = time.time()
                recovery.recover(db)
        with Session() as db:
            tick(db)
    except Exception:
        traceback.print_exc()
    time.sleep(1)
