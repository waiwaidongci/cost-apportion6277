import hashlib
import json
import time

import redis
from fastapi import HTTPException
from src.models.source import Source, Policy
from src.models.run import Run
from src.services.audit import log
from src.services import ledger
from src.config.settings import REDIS_URL


def submit(db, user, body):
    """排队核算：先把台账积压算齐，再冻结“来源 + 口径版本 + 完整工时依据”一版。"""
    policy = db.get(Policy, body.policy_id)
    if not policy:
        raise HTTPException(404, '口径版本不存在')
    period = (body.period or '全部').strip() or '全部'
    sources = [x.data for x in db.query(Source).order_by(Source.id).all()
               if period == '全部' or x.data.get('period') == period]
    if not sources:
        raise HTTPException(422, '所选期间没有底稿')
    try:
        basis = ledger.snapshot(db, period)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    warnings = [f"工时记录{c['记录编号']}字段{c['字段']}存在并发更正，已采用{c['采用']}"
                for c in basis['conflicts']]
    frozen_policy = {'version': policy.data['version'],
                     'note': policy.data.get('note', ''),
                     'weights': basis['weights'],
                     'basis': {'gen': basis['gen'], 'checksum': basis['checksum'],
                               'period': basis['period'],
                               'last_op_id': basis['cursor']['last_op_id']}}
    snapshot = {'policy': frozen_policy, 'sources': sources, 'period': period,
                'worktime': {k: basis[k] for k in
                             ('gen', 'checksum', 'period', 'weights', 'departments', 'cells', 'entries')},
                'warnings': warnings}
    checksum = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    obj = Run(name=body.name, status='queued', snapshot=snapshot, checksum=checksum,
              created=time.time(), approved=False)
    db.add(obj)
    log(db, user.email, 'run.queue', f'{body.name} 台账版本{basis["gen"]}')
    db.commit()
    redis.Redis.from_url(REDIS_URL).rpush('report-jobs', obj.id)
    return obj


def approve(db, user, id, note):
    obj = db.get(Run, id)
    if not obj:
        raise HTTPException(404, '报告不存在')
    if obj.status != 'completed':
        raise HTTPException(409, '仅可复核已完成报告')
    if obj.approved:
        raise HTTPException(409, '报告已经复核')
    obj.approved = True
    obj.review = note
    log(db, user.email, 'run.approve', id)
    db.commit()
    return obj
