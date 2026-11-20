import sys
from fractions import Fraction
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.database import Base
from src.models import identity, source, run, worktime
from src.services import ledger, worktime as wt


@pytest.fixture()
def db():
    engine = create_engine('sqlite+pysqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        yield session
    Base.metadata.drop_all(engine)


def add(db, entry_id, person, department, start, end, **kwargs):
    ledger.append(db, 'tester', 'create', entry_id,
                  {'person': person, 'department': department, 'start': start, 'end': end}, **kwargs)
    db.commit()


def cells(db, period='全部'):
    rows = ledger.ledger_view(db, period)['rows']
    return {(r['人员'], r['日期'], r['部门']): Fraction(r['秒数分数']) for r in rows}


def test_cross_day_ledger_and_incremental_matches_full(db):
    add(db, 'a', '周砚', '工艺研究', '2026-11-05T23:00', '2026-11-06T02:30')
    add(db, 'b', '沈阔', '光学研究', '2026-11-05T09:00', '2026-11-05T12:00')
    first = ledger.apply_pending(db)
    assert first['result'] == 'switched'
    view = cells(db)
    assert view[('周砚', '2026-11-05', '工艺研究')] == Fraction(3600)
    assert view[('周砚', '2026-11-06', '工艺研究')] == Fraction(9000)
    assert view[('沈阔', '2026-11-05', '光学研究')] == Fraction(3 * 3600)

    full = ledger.rebuild_full(db)
    assert full['consistent'] is True
    assert full['checksum'] == first['checksum']


def test_batch_boundary_leaves_later_ops_for_next_batch(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    add(db, 'b', '沈阔', '光学研究', '2026-11-03T13:00', '2026-11-03T14:00')
    ledger.append(db, 'tester', 'update', 'a', {'end': '2026-11-03T15:00'}, base_rev=1)
    db.commit()
    first = ledger.apply_pending(db, limit=2)
    assert first['last_op_id'] == 2
    # 第三批操作尚未投影，台账仍是 3 小时
    assert cells(db)[('林岚', '2026-11-03', '光学研究')] == Fraction(3 * 3600)
    second = ledger.apply_pending(db, limit=5)
    assert second['last_op_id'] == 3
    assert cells(db)[('林岚', '2026-11-03', '光学研究')] == Fraction(6 * 3600)
    assert ledger.rebuild_full(db)['consistent'] is True


def test_cross_day_withdraw_only_recomputes_affected_person_days(db):
    add(db, 'a', '周砚', '工艺研究', '2026-11-05T23:00', '2026-11-06T02:30')
    add(db, 'b', '沈阔', '光学研究', '2026-11-05T09:00', '2026-11-05T12:00')
    base = ledger.apply_pending(db)
    ledger.append(db, 'tester', 'withdraw', 'a', {}, base_rev=1)
    db.commit()
    report = ledger.apply_pending(db)
    assert set(report['affected']) == {'周砚@2026-11-05', '周砚@2026-11-06'}
    view = cells(db)
    assert ('周砚', '2026-11-05', '工艺研究') not in view
    assert ('周砚', '2026-11-06', '工艺研究') not in view
    assert view[('沈阔', '2026-11-05', '光学研究')] == Fraction(3 * 3600)
    # 未受影响的人日在版本切换中保持同一分子分母
    assert view['沈阔', '2026-11-05', '光学研究'] == Fraction(3 * 3600)
    full = ledger.rebuild_full(db)
    assert full['consistent'] is True


def test_switched_generation_is_all_or_nothing(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    first = ledger.apply_pending(db)
    add(db, 'b', '林岚', '材料研究', '2026-11-03T10:00', '2026-11-03T13:00')
    second = ledger.apply_pending(db)
    assert second['gen'] == first['gen'] + 1
    gens = {g.gen: g for g in db.query(worktime.LedgerGen).all()}
    assert gens[first['gen']].status == 'retired'
    assert gens[second['gen']].status == 'active'
    # 生效版本中同一人日两部门各 2 小时，重叠只计一次
    view = cells(db)
    assert view[('林岚', '2026-11-03', '光学研究')] == Fraction(2 * 3600)
    assert view[('林岚', '2026-11-03', '材料研究')] == Fraction(2 * 3600)
    # 旧版本数据仍完整保留，便于追溯
    assert db.query(worktime.WorkLedger).filter_by(gen=first['gen']).count() == 1


def test_reapply_pending_is_idempotent_no_double_counting(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    ledger.apply_pending(db)
    check = ledger.apply_pending(db)
    assert check['result'] == 'noop'
    again = ledger.apply_pending(db)
    assert again['result'] == 'noop'
    assert cells(db)[('林岚', '2026-11-03', '光学研究')] == Fraction(3 * 3600)


def test_retry_after_crash_before_cursor_move_keeps_baseline(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    base = ledger.apply_pending(db)
    # 模拟崩溃：操作已写入但游标未推进，下次从同一基版本重跑
    ledger.append(db, 'tester', 'update', 'a', {'end': '2026-11-03T13:00'}, base_rev=1)
    db.commit()
    crashed = ledger.apply_pending(db)
    retried = ledger.apply_pending(db)
    assert retried['result'] == 'noop'
    assert cells(db)[('林岚', '2026-11-03', '光学研究')] == Fraction(4 * 3600)
    assert ledger.rebuild_full(db)['consistent'] is True
    assert crashed['gen'] > base['gen']


def test_crash_before_switch_rolls_back_and_rerun_matches_full(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    base = ledger.apply_pending(db)
    add(db, 'b', '周砚', '工艺研究', '2026-11-05T23:00', '2026-11-06T02:30')

    # 模拟进程在“已写完批次、尚未提交切换”时崩溃：提交点抛异常并回滚
    original_commit = db.commit

    def crash():
        raise RuntimeError('模拟崩溃')

    db.commit = crash
    try:
        ledger.apply_pending(db)
        raise AssertionError('应触发崩溃')
    except RuntimeError:
        db.commit = original_commit
        db.rollback()
    finally:
        db.commit = original_commit

    cursor = db.query(worktime.LedgerCursor).filter_by(name='worktime').first()
    assert cursor.active_gen == base['gen']
    assert cursor.last_op_id == 1
    # 半成品批次不应生效，也不残留 active 版本
    active = db.query(worktime.LedgerGen).filter_by(status='active').all()
    assert [g.gen for g in active] == [base['gen']]
    assert db.query(worktime.WorkLedger).count() == 1

    # 重跑从同一基版本继续，结果与全量一致且不重复计时
    retried = ledger.apply_pending(db)
    assert retried['result'] == 'switched'
    assert retried['gen'] > base['gen']
    assert cells(db)[('周砚', '2026-11-05', '工艺研究')] == Fraction(3600)
    assert cells(db)[('周砚', '2026-11-06', '工艺研究')] == Fraction(9000)
    assert cells(db)[('林岚', '2026-11-03', '光学研究')] == Fraction(3 * 3600)
    assert ledger.rebuild_full(db)['consistent'] is True


def test_concurrent_disjoint_field_edits_both_survive(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    ledger.apply_pending(db)
    # 两个客户端基于同一版本并发修改不同字段
    ledger.append(db, 'u1', 'update', 'a', {'end': '2026-11-03T14:00'}, base_rev=1, op_id='op-1')
    ledger.append(db, 'u2', 'update', 'a', {'department': '材料研究'}, base_rev=1, op_id='op-2')
    db.commit()
    ledger.apply_pending(db)
    entries, conflicts = ledger.projected(db)
    assert conflicts == []
    assert entries['a']['end'] == '2026-11-03T14:00:00+08:00'
    assert entries['a']['department'] == '材料研究'
    assert cells(db) == {('林岚', '2026-11-03', '材料研究'): Fraction(5 * 3600)}


def test_concurrent_same_field_reports_conflict_deterministically(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    ledger.apply_pending(db)
    ledger.append(db, 'u1', 'update', 'a', {'end': '2026-11-03T13:00'}, base_rev=1, op_id='op-1')
    ledger.append(db, 'u2', 'update', 'a', {'end': '2026-11-03T15:00'}, base_rev=1, op_id='op-2')
    db.commit()
    report = ledger.apply_pending(db)
    assert len(report['conflicts']) == 1
    conflict = report['conflicts'][0]
    assert conflict['字段'] == 'end' and set(conflict['并发现值']) == {
        '2026-11-03T13:00:00+08:00', '2026-11-03T15:00:00+08:00'}
    # 日志顺序确定性取胜者，重跑不再产生新结果
    assert ledger.rebuild_full(db)['consistent'] is True


def test_moving_person_recomputes_both_persons_days(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    add(db, 'b', '沈阔', '光学研究', '2026-11-03T13:00', '2026-11-03T14:00')
    ledger.apply_pending(db)
    ledger.append(db, 'tester', 'update', 'a', {'person': '沈阔'}, base_rev=1)
    db.commit()
    report = ledger.apply_pending(db)
    assert set(report['affected']) == {'林岚@2026-11-03', '沈阔@2026-11-03'}
    view = cells(db)
    assert ('林岚', '2026-11-03', '光学研究') not in view
    # 沈阔：09-12 与 13-14 不重叠，合计 4 小时
    assert view[('沈阔', '2026-11-03', '光学研究')] == Fraction(4 * 3600)
    assert ledger.rebuild_full(db)['consistent'] is True


def test_create_then_update_is_not_a_conflict(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00')
    ledger.append(db, 'tester', 'update', 'a', {'end': '2026-11-03T13:00'},
                  base_rev=0, op_id='ui-retry')
    db.commit()
    entries, conflicts = ledger.projected(db)
    assert conflicts == []
    assert entries['a']['end'] == '2026-11-03T13:00:00+08:00'


def test_duplicate_op_id_is_not_recorded_twice(db):
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00', op_id='same')
    add(db, 'a', '林岚', '光学研究', '2026-11-03T09:00', '2026-11-03T12:00', op_id='same')
    assert db.query(worktime.WorkEntryOp).count() == 1
    ledger.apply_pending(db)
    assert cells(db)[('林岚', '2026-11-03', '光学研究')] == Fraction(3 * 3600)


def test_entry_across_period_boundary_splits_into_periods(db):
    add(db, 'a', '周砚', '工艺研究', '2026-10-31T23:00', '2026-11-01T02:00')
    ledger.apply_pending(db)
    assert cells(db, '2026-10') == {('周砚', '2026-10-31', '工艺研究'): Fraction(3600)}
    assert cells(db, '2026-11') == {('周砚', '2026-11-01', '工艺研究'): Fraction(2 * 3600)}


def test_snapshot_weights_are_exact_and_sum_to_one(db):
    add(db, 'a', '林岚', 'A', '2026-11-03T09:00', '2026-11-03T12:00')
    add(db, 'b', '沈阔', 'B', '2026-11-03T09:00', '2026-11-03T10:00')
    add(db, 'c', '周砚', 'C', '2026-11-03T09:00', '2026-11-03T09:00:30')
    ledger.apply_pending(db)
    snap = ledger.snapshot(db, '2026-11')
    assert snap['weights'] == {'A': '10800/1', 'B': '3600/1', 'C': '30/1'}
    total = sum(Fraction(v) for v in snap['weights'].values())
    assert total == Fraction(14430)
    assert len(snap['cells']) == 3
    assert len(snap['entries']) == 3


def test_snapshot_skips_pending_ops_and_freezes_complete_version(db):
    add(db, 'a', '林岚', 'A', '2026-11-03T09:00', '2026-11-03T12:00')
    ledger.apply_pending(db)
    add(db, 'b', '沈阔', 'B', '2026-11-03T09:00', '2026-11-03T12:00')
    # 未显式重算也应先把积压算齐再冻结
    snap = ledger.snapshot(db, '2026-11')
    assert set(snap['weights']) == {'A', 'B'}
    assert db.query(worktime.WorkEntryOp).count() == 2
