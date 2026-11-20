from fractions import Fraction
from collections import defaultdict

from src.services.validation import validate_source, validate_policy


def exact_weight(value):
    """把权重转成精确分数：整数、小数串、'n/d' 分数串与 Fraction 都支持。"""
    if isinstance(value, bool):
        raise ValueError('权重必须为非负数')
    if isinstance(value, Fraction):
        fraction = value
    elif isinstance(value, int):
        fraction = Fraction(value)
    elif isinstance(value, float):
        fraction = Fraction(str(value))
    elif isinstance(value, str):
        try:
            fraction = Fraction(value.strip())
        except (ValueError, ZeroDivisionError):
            raise ValueError('权重格式不合法')
    else:
        raise ValueError('权重必须为非负数')
    if fraction < 0:
        raise ValueError('权重必须为非负数')
    return fraction


def allocate(cents, weights):
    """整数分最大余数法：精确份额向下取整后按余数降序、部门名升序补分。"""
    if isinstance(cents, bool) or not isinstance(cents, int) or cents < 0:
        raise ValueError('金额必须为非负整数分')
    ws = {k: exact_weight(v) for k, v in weights.items()}
    if not ws or sum(ws.values()) <= 0:
        raise ValueError('权重合计必须大于0且非负')
    total = sum(ws.values())
    raw = {k: Fraction(cents) * w / total for k, w in ws.items()}
    base = {k: v.numerator // v.denominator for k, v in raw.items()}
    order = sorted(ws, key=lambda k: (-(raw[k] - base[k]), k))
    for k in order[:cents - sum(base.values())]:
        base[k] += 1
    assert sum(base.values()) == cents
    return base


def calculate(sources, policy, worktime=None):
    validate_policy(policy)
    weights = policy['weights']
    pivot = defaultdict(int)
    source_total = 0
    public = 0
    trace = []
    for s in sources:
        validate_source(s)
        source_total += s['amount_cents']
        pieces = allocate(s['amount_cents'], weights) if s['department'] == '公共' else {s['department']: s['amount_cents']}
        if s['department'] == '公共':
            public += s['amount_cents']
        for dept, v in pieces.items():
            pivot[(s['period'], dept, s['category'])] += v
        trace.append({'来源序号': len(trace) + 1, '期间': s['period'], '来源部门': s['department'],
                      '费用类别': s['category'], '原始金额分': s['amount_cents'],
                      '分配明细': pieces, '分配合计分': sum(pieces.values())})
    rows = [{'期间': p, '部门': d, '费用类别': c, '分摊金额分': v}
            for (p, d, c), v in sorted(pivot.items())]
    total = sum(r['分摊金额分'] for r in rows)
    worktime = worktime or {}
    return {'summary': {'来源金额分': source_total, '分摊金额分': total,
                        '公共成本分': public, '守恒差额分': total - source_total},
            'rows': rows, 'trace': trace,
            'weights': {k: str(exact_weight(v)) for k, v in weights.items()},
            'worktime': {'gen': worktime.get('gen'), 'checksum': worktime.get('checksum'),
                         'period': worktime.get('period'),
                         'departments': worktime.get('departments', []),
                         'cells': worktime.get('cells', []),
                         'entries': worktime.get('entries', [])},
            'method': ('公共费用按起止工时台账的部门人日秒数精确权重分摊：跨日按自然日拆分，'
                       '同人重叠只计一次，同时报给多部门时该瞬时均分；份额以分数保留，'
                       '整数分向下取整后按余数降序、部门名升序补分。部门直归费用不分摊。'),
            'warnings': worktime.get('warnings') or []}
