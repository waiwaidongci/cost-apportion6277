import os
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.mkdtemp()
os.environ.setdefault('DATABASE_URL', f'sqlite+pysqlite:///{_tmp}/api.db')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('SEED_DEMO', 'false')

from fastapi.testclient import TestClient  # noqa: E402

from src.main import app  # noqa: E402
from src.config.database import Session  # noqa: E402
from src.services import report as report_service  # noqa: E402
from src.services.calculation import calculate  # noqa: E402
from src.models.run import Run  # noqa: E402
from src.models.identity import User  # noqa: E402
from src.utils.security import password_hash  # noqa: E402


class FakeQueue:
    def __init__(self):
        self.items = []

    def rpush(self, name, value):
        self.items.append(value)


QUEUE = FakeQueue()


@pytest.fixture(scope='module')
def client():
    report_service.redis.Redis.from_url = lambda *a, **k: QUEUE
    with TestClient(app) as c:
        yield c


def auth(client, email):
    with Session() as db:
        if not db.query(User).filter_by(email=email).first():
            db.add(User(email=email, role='admin', password=password_hash('pw')))
            db.commit()
    token = client.post('/auth/login', json={'email': email, 'password': 'pw'}).json()['token']
    return {'Authorization': 'Bearer ' + token}


def test_full_scenario(client):
    admin = auth(client, 'api-admin@test.local')

    # 1. 登记口径版本（不再手写权重）
    r = client.post('/policies', json={'data': {'version': '工时口径-api', 'note': '按台账'}}, headers=admin)
    assert r.status_code == 200, r.text
    policy_id = r.json()['id']

    # 2. 底稿：公共费用 + 部门直归
    for row in ({'period': '2026-11', 'department': '公共', 'category': '仪器维护', 'amount_cents': 100001},
                {'period': '2026-11', 'department': '光学研究', 'category': '试验耗材', 'amount_cents': 5000}):
        assert client.post('/sources', json={'data': row}, headers=admin).status_code == 200

    # 3. 工时：跨日 + 重叠多部门
    entries = [
        {'person': '周砚', 'department': '工艺研究', 'start': '2026-11-05T23:00', 'end': '2026-11-06T02:30'},
        {'person': '林岚', 'department': '光学研究', 'start': '2026-11-03T09:00', 'end': '2026-11-03T12:00'},
        {'person': '林岚', 'department': '材料研究', 'start': '2026-11-03T10:00', 'end': '2026-11-03T13:00', 'entry_id': 'lin-overlap'},
    ]
    for entry in entries:
        r = client.post('/worktime/entries', json=entry, headers=admin)
        assert r.status_code == 200, r.text

    r = client.post('/worktime/recompute', headers=admin)
    assert r.status_code == 200 and r.json()['result'] == 'switched', r.text

    view = client.get('/worktime?period=2026-11', headers=admin).json()
    cells = {(x['人员'], x['日期'], x['部门']): Fraction(x['秒数分数']) for x in view['rows']}
    assert cells[('周砚', '2026-11-05', '工艺研究')] == Fraction(3600)
    assert cells[('周砚', '2026-11-06', '工艺研究')] == Fraction(9000)
    assert cells[('林岚', '2026-11-03', '光学研究')] == Fraction(7200)
    assert cells[('林岚', '2026-11-03', '材料研究')] == Fraction(7200)

    # 4. 排队核算：冻结完整台账版本
    r = client.post('/runs', json={'name': '十一月核算', 'policy_id': policy_id, 'period': '2026-11'}, headers=admin)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run['snapshot']['worktime']['gen'] == view['gen']
    assert len(run['snapshot']['worktime']['cells']) == 4

    # 5. worker 模拟：同一份快照重复计算，结果确定性一致
    result_a = calculate(run['snapshot']['sources'], run['snapshot']['policy'], run['snapshot']['worktime'])
    result_b = calculate(run['snapshot']['sources'], run['snapshot']['policy'], run['snapshot']['worktime'])
    assert result_a == result_b
    assert result_a['summary']['守恒差额分'] == 0
    assert result_a['summary']['分摊金额分'] == 105001
    with Session() as db:
        job = db.get(Run, run['id'])
        job.status = 'completed'
        job.result = result_a
        db.commit()

    # 6. 报告页面能查工时依据
    detail = client.get(f'/runs/{run["id"]}', headers=admin).json()
    wt = detail['result']['worktime']
    assert wt['gen'] == view['gen']
    assert {d['部门'] for d in wt['departments']} == {'光学研究', '材料研究', '工艺研究'}
    assert any(c['跨日'] for c in wt['entries'])

    # 7. 复核后把工时改掉，已提交报告不受影响
    target = client.get('/worktime/entries', headers=admin).json()['entries']
    lin = next(e for e in target if e['人员'] == '林岚' and e['部门'] == '光学研究')
    r = client.patch(f'/worktime/entries/{lin["记录编号"]}',
                     json={'end': '2026-11-03T15:00', 'base_rev': lin['版本']}, headers=admin)
    assert r.status_code == 200, r.text
    client.post('/worktime/recompute', headers=admin)
    detail = client.get(f'/runs/{run["id"]}', headers=admin).json()
    assert detail['result']['worktime'] == wt
    assert client.post(f'/runs/{run["id"]}/approve', json={'note': '核对跨日与重叠'}, headers=admin).status_code == 200

    # 8. 新报告使用新的台账版本
    r = client.post('/runs', json={'name': '十一月核算-更正', 'policy_id': policy_id, 'period': '2026-11'}, headers=admin)
    assert r.status_code == 200
    assert r.json()['snapshot']['worktime']['gen'] > wt['gen']


def test_withdraw_cross_day_and_rebuild_matches(client):
    admin = auth(client, 'api-admin@test.local')
    created = client.post('/worktime/entries', json={
        'person': '沈阔', 'department': '光学研究',
        'start': '2026-10-08T22:00', 'end': '2026-10-09T01:00', 'entry_id': 'shen-cross'},
        headers=admin).json()
    client.post('/worktime/recompute', headers=admin)
    before = client.get('/worktime?period=2026-10', headers=admin).json()
    assert any(x['人员'] == '沈阔' for x in before['rows'])

    r = client.post('/worktime/entries/shen-cross/withdraw', json={'base_rev': created['rev']}, headers=admin)
    assert r.status_code == 200, r.text
    report = client.post('/worktime/recompute', headers=admin).json()
    assert set(report['affected']) == {'沈阔@2026-10-08', '沈阔@2026-10-09'}
    after = client.get('/worktime?period=2026-10', headers=admin).json()
    assert not any(x['人员'] == '沈阔' for x in after['rows'])

    full = client.post('/worktime/rebuild', headers=admin).json()
    assert full['consistent'] is True


def test_validation_errors(client):
    admin = auth(client, 'api-admin@test.local')
    bad_time = client.post('/worktime/entries', json={
        'person': 'A', 'department': 'B', 'start': '2026-11-03T12:00', 'end': '2026-11-03T09:00'}, headers=admin)
    assert bad_time.status_code == 422
    missing = client.post('/worktime/entries', json={
        'person': 'A', 'department': 'B', 'start': '2026-11-03T09:00'}, headers=admin)
    assert missing.status_code in (422, 400)
    dup_policy = client.post('/policies', json={'data': {'version': '工时口径-api'}}, headers=admin)
    assert dup_policy.status_code == 409
    no_policy = client.post('/runs', json={'name': '缺口径', 'policy_id': 99999, 'period': '2026-11'}, headers=admin)
    assert no_policy.status_code == 404
