"""起止工时台账的纯算法层。

口径：
- 同一人员的工时区间先按本地自然日切分，跨日部分分别计入各自人日；
- 同一人日内的重叠时段只计一次；某一瞬时若同时报给多个不同部门，
  该瞬时的时长在部门间均分；同一部门重复申报不重复计时；
- 时长使用 Fraction 秒精确保留分数，直到费用分摊取整时才转整数分；
- 所有重算都由有效操作日志重新推导，因此可重复执行而不重复计时。
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
MAX_SECONDS = 7 * 24 * 3600
ENTRY_FIELDS = ('person', 'department', 'start', 'end')


def parse_instant(value):
    """接受 epoch 秒、本地无时区时间串或带时区 ISO 串，统一为上海时区 aware datetime。"""
    if isinstance(value, bool):
        raise ValueError('时间不合法')
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, ZONE)
        except (OverflowError, OSError, ValueError):
            raise ValueError('时间不合法')
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError('时间格式不合法，请使用 YYYY-MM-DDTHH:MM')
    else:
        raise ValueError('时间不能为空')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZONE)
    return dt.astimezone(ZONE)


def day_bounds(day):
    return (datetime.combine(day, time(0), tzinfo=ZONE),
            datetime.combine(day + timedelta(days=1), time(0), tzinfo=ZONE))


def _seconds(delta):
    return Fraction(delta.days * 86400 + delta.seconds) + Fraction(delta.microseconds, 1_000_000)


def split_days(start, end):
    """按本地自然日切分，返回 [(day_iso, segment_start, segment_end)]。"""
    start, end = parse_instant(start), parse_instant(end)
    if end <= start:
        raise ValueError('结束时间必须晚于开始时间')
    if _seconds(end - start) > MAX_SECONDS:
        raise ValueError('单条工时不得超过7天')
    out, cursor = [], start
    while cursor < end:
        day = cursor.astimezone(ZONE).date()
        _, boundary = day_bounds(day)
        stop = min(end, boundary)
        if stop > cursor:
            out.append((day.isoformat(), cursor, stop))
        cursor = stop
    return out


def affected_days(start, end):
    return [day for day, _, _ in split_days(start, end)]


def day_shares(intervals):
    """同一人日内按瞬时部门集合均分；重叠只覆盖一次。"""
    live = sorted((s, e, d) for s, e, d in intervals if e > s and d)
    points = sorted({p for s, e, _ in live for p in (s, e)})
    shares = defaultdict(Fraction)
    for left, right in zip(points, points[1:]):
        departments = {d for s, e, d in live if s <= left and right <= e}
        if not departments:
            continue
        part = _seconds(right - left) / len(departments)
        for department in departments:
            shares[department] += part
    return dict(shares)


def clip_to_day(entry, day):
    day_start, day_end = day_bounds(day if isinstance(day, date) else date.fromisoformat(day))
    start = max(parse_instant(entry['start']), day_start)
    end = min(parse_instant(entry['end']), day_end)
    if end <= start:
        return None
    return start, end, entry['department']


def _iter_entries(entries):
    return entries.values() if isinstance(entries, dict) else entries


def person_day_shares(entries, person, day):
    intervals = []
    for entry in _iter_entries(entries):
        if entry.get('withdrawn') or entry.get('person') != person:
            continue
        piece = clip_to_day(entry, day)
        if piece:
            intervals.append(piece)
    return day_shares(intervals)


def days_of(entry):
    """有效条目触及的人日；无效或已撤回条目返回空。"""
    if not entry or entry.get('withdrawn'):
        return []
    try:
        return affected_days(entry['start'], entry['end'])
    except (ValueError, KeyError, TypeError):
        return []


def project_ops(ops):
    """把操作日志投影为有效条目。

    按服务端序号顺序合并；每个字段独立采用后写胜出，因此并发更正只要
    字段不重叠就都会保留。撤回对条目终态生效，撤回后的字段更正不会让其复活。
    同一字段若有多条同基版本（base_rev 相同）且取值不同，视为并发冲突：
    以 (rev, op_id) 确定性取胜者，并列出一条冲突说明。

    返回 ({entry_id: entry}, [冲突])。
    """
    slots = defaultdict(dict)            # entry_id -> field -> (clock, op_id, value)
    revs = defaultdict(int)
    withdrawn_at = defaultdict(int)
    base_values = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))   # entry -> field -> base_rev -> {value: clock}
    conflicts = []

    for op in sorted(ops, key=lambda x: (x['rev'], str(x['op_id']))):
        entry_id, clock = op['entry_id'], op['rev']
        revs[entry_id] = max(revs[entry_id], clock)
        changes = dict(op.get('changes') or {})
        if op['kind'] == 'withdraw':
            changes['withdrawn'] = True
            withdrawn_at[entry_id] = max(withdrawn_at[entry_id], clock)
        base = op.get('base_rev') or 0
        for name, value in changes.items():
            previous = slots[entry_id].get(name)
            if previous is None or clock >= previous[0]:
                slots[entry_id][name] = (clock, str(op['op_id']), value)
            # 只有基于某个已存在版本（base>=1）的并发修改才构成字段冲突；
            # 首建条目（base=0）与其后更正不算并发。
            if base >= 1:
                base_values[entry_id][name][base][_key(value)] = clock

    for entry_id, fields in base_values.items():
        for name, groups in fields.items():
            for base, values in groups.items():
                if len(values) > 1:
                    conflicts.append({'记录编号': entry_id, '字段': name,
                                      '并发现值': sorted(values),
                                      '采用': slots[entry_id][name][2]})

    projected = {}
    for entry_id, slot in slots.items():
        values = {name: item[2] for name, item in slot.items()}
        if not all(values.get(key) not in (None, '') for key in ENTRY_FIELDS):
            continue
        values['entry_id'] = entry_id
        values['rev'] = revs[entry_id]
        values['withdrawn'] = bool(withdrawn_at.get(entry_id))
        if not values['withdrawn']:
            try:
                affected_days(values['start'], values['end'])
            except (ValueError, TypeError):
                continue
        projected[entry_id] = values
    return projected, conflicts


def _key(value):
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def states_of(entry_id, ops):
    """该条目的历史状态序列（含修改前旧值），用于确定受影响的 (人, 日)。"""
    states, current = [], {}
    for op in sorted(ops, key=lambda x: (x['rev'], str(x['op_id']))):
        if op['entry_id'] != entry_id:
            continue
        current = dict(current)
        current.update(op.get('changes') or {})
        if op['kind'] == 'withdraw':
            current['withdrawn'] = True
        states.append(current)
    return states


def affected_person_days(entry_ids, ops):
    """操作涉及的所有 (person, day)，含修改前旧值与修改后新值。"""
    out = set()
    for entry_id in entry_ids:
        for state in states_of(entry_id, ops):
            if not state.get('person'):
                continue
            for day in days_of(state):
                out.add((state['person'], day))
    return out


def hours(seconds):
    """精确小时数，返回 (Fraction 小时, 展示字符串)。"""
    value = Fraction(seconds) / 3600
    return value, format_decimal(value)


def format_decimal(value, places=6):
    value = Fraction(value)
    sign = '-' if value < 0 else ''
    digit, _ = divmod(abs(value).numerator * 10 ** places, abs(value).denominator)
    whole, frac = divmod(digit, 10 ** places)
    text = f'{sign}{whole}.{str(frac).zfill(places)}'.rstrip('0').rstrip('.')
    return text or '0'


def fraction_text(value):
    value = Fraction(value)
    return f'{value.numerator}/{value.denominator}'
