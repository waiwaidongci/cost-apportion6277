import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import worktime as wt
from src.services.calculation import calculate


def sec(n):
    return Fraction(n)


def test_overlap_counts_once():
    entries = [
        {'person': '林岚', 'department': '光学研究', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '林岚', 'department': '光学研究', 'start': '2026-11-03T10:00', 'end': '2026-11-03T13:00'},
    ]
    shares = wt.person_day_shares(entries, '林岚', '2026-11-03')
    assert shares == {'光学研究': sec(4 * 3600)}, shares


def test_overlap_split_across_departments():
    entries = [
        {'person': '林岚', 'department': '光学研究', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '林岚', 'department': '材料研究', 'start': '2026-11-03T10:00', 'end': '2026-11-03T13:00'},
    ]
    shares = wt.person_day_shares(entries, '林岚', '2026-11-03')
    # 09-10 光学 1h、12-13 材料 1h、10-12 两部门各 1h
    assert shares == {'光学研究': sec(2 * 3600), '材料研究': sec(2 * 3600)}, shares
    assert sum(shares.values()) == sec(4 * 3600)


def test_three_way_instant_split():
    entries = [
        {'person': '周砚', 'department': 'A', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '周砚', 'department': 'B', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '周砚', 'department': 'C', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
    ]
    shares = wt.person_day_shares(entries, '周砚', '2026-11-03')
    assert shares == {'A': sec(3600), 'B': sec(3600), 'C': sec(3600)}
    assert wt.fraction_text(shares['A']) == '3600/1'


def test_fraction_seconds_preserved():
    entries = [
        {'person': '沈阔', 'department': 'A', 'start': '2026-11-03T09:00:01', 'end': '2026-11-03T10:00:02'},
        {'person': '沈阔', 'department': 'B', 'start': '2026-11-03T09:00:01', 'end': '2026-11-03T10:00:02'},
    ]
    shares = wt.person_day_shares(entries, '沈阔', '2026-11-03')
    assert shares['A'] == shares['B']
    assert shares['A'].denominator == 2
    assert wt.fraction_text(shares['A']) == '3601/2'


def test_cross_day_split():
    entries = [{'person': '周砚', 'department': '工艺研究',
                'start': '2026-11-05T23:00', 'end': '2026-11-06T02:30'}]
    assert wt.affected_days(entries[0]['start'], entries[0]['end']) == ['2026-11-05', '2026-11-06']
    first = wt.person_day_shares(entries, '周砚', '2026-11-05')
    second = wt.person_day_shares(entries, '周砚', '2026-11-06')
    assert first == {'工艺研究': sec(3600)}
    assert second == {'工艺研究': sec(9000)}
    assert sum(first.values()) + sum(second.values()) == sec(3.5 * 3600)


def test_same_department_duplicate_not_double_counted():
    entries = [
        {'person': '许澄', 'department': 'A', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '许澄', 'department': 'A', 'start': '2026-11-03T10:00', 'end': '2026-11-03T11:00'},
    ]
    assert wt.person_day_shares(entries, '许澄', '2026-11-03') == {'A': sec(3 * 3600)}


def test_withdrawn_excluded():
    entries = [
        {'person': '许澄', 'department': 'A', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '许澄', 'department': 'B', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00', 'withdrawn': True},
    ]
    assert wt.person_day_shares(entries, '许澄', '2026-11-03') == {'A': sec(3 * 3600)}


def test_clock_timezone_stable():
    assert wt.parse_instant('2026-11-05T23:00').isoformat() == '2026-11-05T23:00:00+08:00'
    assert wt.parse_instant(1793462400).isoformat() == '2026-11-01T00:00:00+08:00'


def test_format_decimal_is_exact():
    assert wt.format_decimal(Fraction(1, 3), 6) == '0.333333'
    assert wt.format_decimal(Fraction(3601, 2) / 3600, 6) == '0.500138'
    assert wt.format_decimal(Fraction(3)) == '3'


def test_one_cent_three_weights_conserved_from_fractional_hours():
    entries = [{'person': '甲', 'department': d, 'start': '2026-11-03T09:00:00',
                'end': '2026-11-03T09:00:07'} for d in ('A', 'B', 'C')]
    shares = wt.person_day_shares(entries, '甲', '2026-11-03')
    assert {wt.fraction_text(v) for v in shares.values()} == {'7/3'}  # 7 秒三分，精确到分数
    policy = {'version': '工时口径-测试', 'weights': {k: wt.fraction_text(v) for k, v in shares.items()}}
    result = calculate([{'period': '2026-11', 'department': '公共', 'category': 'x', 'amount_cents': 1}], policy)
    assert sum(r['分摊金额分'] for r in result['rows']) == 1
    assert result['summary']['守恒差额分'] == 0
    tie = sorted(r['部门'] for r in result['rows'] if r['分摊金额分'] == 1)
    assert tie == ['A']  # 份额相同按部门名升序稳定补分


def test_zero_weight_rejected_and_pivot_total_equals_source():
    from src.services.calculation import allocate
    try:
        allocate(100, {'A': 0, 'B': 0})
    except ValueError as exc:
        assert '权重合计' in str(exc)
    else:
        raise AssertionError('零权重必须拒绝')
    sources = [{'period': '2026-11', 'department': '公共', 'category': 'x', 'amount_cents': n}
               for n in (1, 2, 3)]
    policy = {'version': '工时口径-测试', 'weights': {'A': '1/1', 'B': '2/1'}}
    result = calculate(sources, policy)
    assert sum(r['分摊金额分'] for r in result['rows']) == 6
