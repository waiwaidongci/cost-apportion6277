import uuid

from fastapi import APIRouter, Depends, HTTPException
from src.config.database import database
from src.controllers.auth import current, editor, admin
from src.schemas.requests import WorkEntryInput, WorkEntryPatch, WorkWithdraw
from src.services import ledger
from src.utils.serialize import row
router = APIRouter()


@router.get('/worktime')
def worktime(period: str = '全部', db=Depends(database), u=Depends(current)):
    return ledger.ledger_view(db, period)


@router.get('/worktime/snapshot')
def snapshot(period: str = '全部', db=Depends(database), u=Depends(current)):
    try:
        return ledger.snapshot(db, period)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get('/worktime/entries')
def entries(db=Depends(database), u=Depends(current)):
    projected, conflicts = ledger.projected(db)
    rows = []
    for entry in sorted(projected.values(), key=lambda x: (x['start'], x['person'])):
        rows.append({'记录编号': entry['entry_id'], '人员': entry['person'],
                     '部门': entry['department'], '开始': entry['start'],
                     '结束': entry['end'], '版本': entry['rev'],
                     '状态': '已撤回' if entry['withdrawn'] else '有效'})
    return {'entries': rows, 'conflicts': conflicts}


@router.get('/worktime/recomputes')
def recomputes(db=Depends(database), u=Depends(current)):
    from src.models.worktime import WorkRecompute, LedgerGen
    history = [row(x) for x in db.query(WorkRecompute).order_by(WorkRecompute.id.desc()).limit(50).all()]
    gens = [row(x) for x in db.query(LedgerGen).order_by(LedgerGen.gen.desc()).limit(20).all()]
    return {'recomputes': history, 'generations': gens}


@router.post('/worktime/entries')
def create(body: WorkEntryInput, db=Depends(database), u=Depends(editor)):
    entry_id = body.entry_id or 'w-' + uuid.uuid4().hex[:12]
    op = ledger.append(db, u.email, 'create', entry_id,
                       {'person': body.person, 'department': body.department,
                        'start': body.start, 'end': body.end},
                       op_id=body.op_id)
    db.commit()
    return {'entry_id': op.entry_id, 'rev': op.rev, 'op_id': op.op_id}


@router.patch('/worktime/entries/{entry_id}')
def update(entry_id: str, body: WorkEntryPatch, db=Depends(database), u=Depends(editor)):
    changes = {k: v for k, v in body.model_dump().items()
               if k in ('person', 'department', 'start', 'end') and v is not None}
    if not changes:
        raise HTTPException(422, '没有需要记录的修改')
    op = ledger.append(db, u.email, 'update', entry_id, changes,
                       base_rev=body.base_rev, op_id=body.op_id)
    db.commit()
    return {'entry_id': op.entry_id, 'rev': op.rev, 'op_id': op.op_id}


@router.post('/worktime/entries/{entry_id}/withdraw')
def withdraw(entry_id: str, body: WorkWithdraw, db=Depends(database), u=Depends(editor)):
    op = ledger.append(db, u.email, 'withdraw', entry_id, {},
                       base_rev=body.base_rev, op_id=body.op_id)
    db.commit()
    return {'entry_id': op.entry_id, 'rev': op.rev, 'op_id': op.op_id}


@router.post('/worktime/recompute')
def recompute(db=Depends(database), u=Depends(admin)):
    return ledger.apply_pending(db, actor=u.email)


@router.post('/worktime/rebuild')
def rebuild(db=Depends(database), u=Depends(admin)):
    return ledger.rebuild_full(db, actor=u.email)
