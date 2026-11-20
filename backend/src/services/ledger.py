"""工时台账服务：只追加操作日志，按人日增量重算，整批校验后原子切换。

关键不变量：
- 台账单元 (人员, 日, 部门) 的秒数始终由“该人员该日全部有效条目”重新推导，
  因此重复执行不会重复计时；
- 一次重算先写入 building 版本，逐格回读校验通过后才把游标切到该版本；
- 游标推进与版本切换在同一事务内完成，崩溃后重跑从同一基版本开始；
- 客户端可携带幂等 op_id，重复提交不会产生第二条操作。
"""
import hashlib
import time
from fractions import Fraction

from src.models.worktime import (WorkEntryOp, WorkLedger, LedgerGen, WorkRecompute, LedgerCursor)
from src.services import worktime as wt
from src.services.audit import log

KEEP_GENS = 5


def _cursor(db, lock=False):
    query = db.query(LedgerCursor).filter_by(name='worktime')
    if lock:
        query = query.with_for_update()
    cursor = query.first()
    if not cursor:
        cursor = LedgerCursor(name='worktime', last_op_id=0, active_gen=0,
                              full_checksum='', updated=time.time())
        db.add(cursor)
        db.flush()
    return cursor


def _normalize(changes):
    out = {}
    for key, value in dict(changes).items():
        if key not in wt.ENTRY_FIELDS:
            raise ValueError(f'未知工时字段：{key}')
        if key in ('start', 'end'):
            out[key] = wt.parse_instant(value).isoformat(timespec='seconds')
        else:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
                raise ValueError(f'{key}不能为空且不超过120字')
            out[key] = value.strip()
    if not out:
        raise ValueError('没有需要记录的修改')
    return out


def append(db, actor, kind, entry_id, changes, base_rev=0, op_id=None):
    """追加一条操作。op_id 幂等；撤回丢弃其余字段修改。"""
    if kind not in ('create', 'update', 'withdraw'):
        raise ValueError('未知操作类型')
    op_id = (op_id or '').strip()[:64]
    if op_id:
        found = db.query(WorkEntryOp).filter_by(op_id=op_id).first()
        if found:
            return found
    if not isinstance(entry_id, str) or not 1 <= len(entry_id) <= 64:
        raise ValueError('工时记录编号不合法')
    changes = {} if kind == 'withdraw' else _normalize(changes)
    if kind != 'create':
        existing = (db.query(WorkEntryOp.rev).filter_by(entry_id=entry_id)
                    .order_by(WorkEntryOp.rev.desc()).limit(1).scalar())
        if not existing:
            raise ValueError('工时记录不存在')
        if base_rev and base_rev > existing:
            raise ValueError('基版本超出当前记录版本')
    if kind in ('create', 'update'):
        merged = _effective(db, entry_id)
        merged.update(changes)
        if all(merged.get(k) for k in wt.ENTRY_FIELDS):
            wt.affected_days(merged['start'], merged['end'])
    rev = (db.query(WorkEntryOp.rev).filter_by(entry_id=entry_id)
           .order_by(WorkEntryOp.rev.desc()).limit(1).scalar() or 0) + 1
    op = WorkEntryOp(entry_id=entry_id, rev=rev,
                     op_id=op_id or f'{entry_id}:{rev}:{time.time_ns()}',
                     kind=kind, base_rev=max(0, int(base_rev or 0)), changes=changes,
                     actor=actor, at=time.time())
    db.add(op)
    db.flush()
    log(db, actor, f'worktime.{kind}', f'{entry_id} rev={rev}')
    return op


def _history(db, upto=None):
    query = db.query(WorkEntryOp).order_by(WorkEntryOp.id)
    if upto is not None:
        query = query.filter(WorkEntryOp.id <= upto)
    return [{'rev': o.rev, 'op_id': o.op_id, 'entry_id': o.entry_id, 'kind': o.kind,
             'changes': o.changes, 'base_rev': o.base_rev}
            for o in query]


def _effective(db, entry_id):
    """条目在追加本次操作前的有效字段值。"""
    values = {}
    for op in db.query(WorkEntryOp).filter_by(entry_id=entry_id).order_by(WorkEntryOp.id):
        values.update(op.changes or {})
    return values


def projected(db):
    return wt.project_ops(_history(db))


def _cell_rows(entries, affected):
    """只重算给定 (人员, 日) 的结果，返回 {(person, day, dept): Fraction}。"""
    cells = {}
    for person, day in sorted(affected):
        for department, seconds in wt.person_day_shares(entries, person, day).items():
            if seconds > 0:
                cells[(person, day, department)] = seconds
    return cells


def _checksum(cells):
    digest = hashlib.sha256()
    for person, day, department, num, den in sorted(
            (p, d, dep, Fraction(s).numerator, Fraction(s).denominator) for (p, d, dep), s in cells.items()):
        digest.update(f'{person}|{day}|{department}|{num}/{den}\n'.encode())
    return digest.hexdigest()


def _cells_of(db, gen):
    if not gen:
        return {}
    return {(r.person, r.day, r.department): Fraction(r.seconds_num, r.seconds_den)
            for r in db.query(WorkLedger).filter_by(gen=gen).all()}


def _write_gen(db, base_gen, cells, affected, conflicts, note):
    """写 building 版本 → 回读校验 → 标 active、旧版 retired、移动游标。"""
    cursor = _cursor(db)
    gen = (db.query(LedgerGen.gen).order_by(LedgerGen.gen.desc()).limit(1).scalar() or 0) + 1
    checksum = _checksum(cells)
    row = LedgerGen(gen=gen, status='building', parent_gen=base_gen,
                    scope=sorted(f'{p}@{d}' for p, d in affected), checksum=checksum,
                    rows=len(cells), created=time.time())
    db.add(row)
    db.flush()
    for (person, day, department), seconds in sorted(cells.items()):
        seconds = Fraction(seconds)
        db.add(WorkLedger(gen=gen, person=person, day=day, department=department,
                          seconds_num=seconds.numerator, seconds_den=seconds.denominator))
    db.flush()
    db.expire_all()
    written = _cells_of(db, gen)
    if written != cells or _checksum(written) != checksum:
        db.query(WorkLedger).filter_by(gen=gen).delete()
        db.query(LedgerGen).filter_by(gen=gen).delete()
        db.flush()
        raise ValueError('台账批次校验失败，未切换生效版本')
    row = db.query(LedgerGen).filter_by(gen=gen).first()
    row.status = 'active'
    row.activated = time.time()
    for old in db.query(LedgerGen).filter(LedgerGen.status == 'active', LedgerGen.gen != gen).all():
        old.status = 'retired'
    stale = (db.query(LedgerGen.gen)
             .filter(LedgerGen.status == 'retired')
             .order_by(LedgerGen.gen.desc()).offset(KEEP_GENS).all())
    for (old_gen,) in stale:
        db.query(WorkLedger).filter_by(gen=old_gen).delete()
        db.query(LedgerGen).filter_by(gen=old_gen).delete()
    cursor = _cursor(db)
    cursor.active_gen = gen
    cursor.updated = time.time()
    db.add(WorkRecompute(gen=gen, parent_gen=base_gen,
                         affected=sorted(f'{p}@{d}' for p, d in affected),
                         conflicts=conflicts, result='switched', note=note, created=time.time()))
    db.flush()
    return gen, checksum


def apply_pending(db, actor='worker', limit=500):
    """把游标之后的整批操作一次算齐；整批校验后才切换。

    先锁住游标行，使 API 的同步补齐与 worker 的周期重算串行，
    避免两个进程各自基于同一基版本写出不同批次。
    """
    cursor = _cursor(db, lock=True)
    db.flush()
    ops = (db.query(WorkEntryOp).filter(WorkEntryOp.id > cursor.last_op_id)
           .order_by(WorkEntryOp.id).limit(limit).all())
    if not ops:
        return {'result': 'noop', 'gen': cursor.active_gen, 'affected': [], 'conflicts': [],
                'last_op_id': cursor.last_op_id}
    last = ops[-1].id
    entry_ids = {o.entry_id for o in ops}
    # 只投影本批次及之前的操作：批次之后的操作留给下一批，避免提前计入。
    history = _history(db, upto=last)
    entries, conflicts = wt.project_ops(history)
    affected = wt.affected_person_days(entry_ids, history)

    base_gen = cursor.active_gen
    cells = _cells_of(db, base_gen)
    for key in list(cells):
        if (key[0], key[1]) in affected:
            del cells[key]
    cells.update(_cell_rows(entries, affected))

    gen, checksum = _write_gen(db, base_gen, cells, affected, conflicts,
                               f'增量重算 {len(ops)} 条操作 / {len(affected)} 个人日')
    db.query(LedgerCursor).filter_by(name='worktime').update(
        {'last_op_id': last, 'active_gen': gen, 'updated': time.time()})
    db.commit()
    log(db, actor, 'worktime.recompute', f'gen={gen} ops={len(ops)} 人日={len(affected)}')
    db.commit()
    return {'result': 'switched', 'gen': gen, 'checksum': checksum, 'last_op_id': last,
            'affected': sorted(f'{p}@{d}' for p, d in affected), 'conflicts': conflicts}


def rebuild_full(db, actor='worker'):
    """从全部操作重建全量台账，并与重算前生效版本核对一致。"""
    entries, conflicts = projected(db)
    history = _history(db)
    affected = wt.affected_person_days({o['entry_id'] for o in history}, history)
    cells = _cell_rows(entries, affected)
    cursor = _cursor(db)
    before_gen = cursor.active_gen
    before = _checksum(_cells_of(db, before_gen))
    gen, checksum = _write_gen(db, before_gen, cells, affected, conflicts, '全量重算核对')
    cursor = _cursor(db)
    cursor.full_checksum = checksum
    cursor.updated = time.time()
    db.commit()
    log(db, actor, 'worktime.rebuild', f'gen={gen} 与版本{before_gen}一致={before == checksum}')
    db.commit()
    return {'result': 'rebuilt', 'gen': gen, 'checksum': checksum, 'previous_gen': before_gen,
            'previous_checksum': before, 'consistent': before == checksum,
            'conflicts': conflicts, 'cells': len(cells)}


def ensure_current(db, actor='api'):
    """核算排队前确保使用完整最新一版；循环补齐全部积压，不冻结半成品。"""
    cursor = _cursor(db)
    db.commit()
    report = {'result': 'noop', 'gen': cursor.active_gen}
    for _ in range(100):
        cursor = _cursor(db)
        if not db.query(WorkEntryOp).filter(WorkEntryOp.id > cursor.last_op_id).count():
            break
        report = apply_pending(db, actor=actor)
    return report


def snapshot(db, period='全部'):
    """冻结完整一版台账：按期间过滤人日，返回权重与逐人日依据。"""
    ensure_current(db)
    cursor = _cursor(db)
    gen = cursor.active_gen
    if not gen:
        raise ValueError('工时台账尚无生效版本，请先补录工时并重算')
    rows = (db.query(WorkLedger).filter_by(gen=gen)
            .order_by(WorkLedger.person, WorkLedger.day, WorkLedger.department).all())
    cells, totals = [], {}
    for row in rows:
        if period != '全部' and row.day[:7] != period:
            continue
        seconds = Fraction(row.seconds_num, row.seconds_den)
        _, hours_text = wt.hours(seconds)
        cells.append({'人员': row.person, '日期': row.day, '部门': row.department,
                      '秒数分数': wt.fraction_text(seconds), '小时': hours_text})
        totals[row.department] = totals.get(row.department, Fraction()) + seconds
    grand = sum(totals.values(), Fraction())
    weights, evidence = {}, []
    for department in sorted(totals):
        seconds = totals[department]
        portion = seconds / grand if grand else Fraction()
        _, hours_text = wt.hours(seconds)
        weights[department] = wt.fraction_text(seconds)
        evidence.append({'部门': department, '合计小时': hours_text,
                         '秒数分数': wt.fraction_text(seconds),
                         '权重占比': wt.format_decimal(portion, 8)})
    for cell in cells:
        department_total = totals.get(cell['部门'])
        cell['部门内占比'] = (wt.format_decimal(Fraction(cell['秒数分数']) / department_total, 6)
                             if department_total else '0')
    entries, conflicts = projected(db)
    entries_out = []
    for entry in entries.values():
        days = wt.days_of(entry)
        if not days or (period != '全部' and not any(d[:7] == period for d in days)):
            continue
        entries_out.append({'记录编号': entry['entry_id'], '人员': entry['person'],
                            '部门': entry['department'], '开始': entry['start'],
                            '结束': entry['end'], '人日数': len(days), '跨日': len(days) > 1})
    if not grand:
        raise ValueError('所选期间没有工时')
    return {'gen': gen, 'checksum': _checksum(_cells_of(db, gen)), 'period': period,
            'weights': weights, 'departments': evidence, 'cells': cells,
            'entries': entries_out, 'conflicts': conflicts,
            'cursor': {'last_op_id': cursor.last_op_id,
                       'full_checksum': cursor.full_checksum or ''}}


def ledger_view(db, period='全部'):
    cursor = _cursor(db)
    gen = cursor.active_gen
    if not gen:
        return {'gen': 0, 'rows': [], 'totals': [], 'hours_total': '0', 'pending_ops': 0}
    rows = (db.query(WorkLedger).filter_by(gen=gen)
            .order_by(WorkLedger.person, WorkLedger.day, WorkLedger.department).all())
    out, totals = [], {}
    for row in rows:
        if period != '全部' and row.day[:7] != period:
            continue
        seconds = Fraction(row.seconds_num, row.seconds_den)
        _, text = wt.hours(seconds)
        out.append({'人员': row.person, '日期': row.day, '部门': row.department,
                    '小时': text, '秒数分数': wt.fraction_text(seconds)})
        totals[row.department] = totals.get(row.department, Fraction()) + seconds
    grand = sum(totals.values(), Fraction())
    summary = []
    for department in sorted(totals):
        _, text = wt.hours(totals[department])
        summary.append({'部门': department, '合计小时': text,
                        '权重占比': wt.format_decimal(totals[department] / grand, 8) if grand else '0'})
    _, total_text = wt.hours(grand)
    return {'gen': gen, 'rows': out, 'totals': summary, 'hours_total': total_text,
            'last_op_id': cursor.last_op_id,
            'pending_ops': db.query(WorkEntryOp).filter(WorkEntryOp.id > cursor.last_op_id).count()}
