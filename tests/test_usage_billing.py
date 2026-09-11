"""Calendar pricing, small storage amounts and concurrent metering use real SQLite."""
import calendar
import datetime as dt
import os
from pathlib import Path
import socket
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault('BH_DOMAIN','billing.test')
os.environ.setdefault('BH_PG_PASSWORD','synthetic')
os.environ.setdefault('BH_METER','0')
os.environ.setdefault('BH_STATE_DIR',tempfile.mkdtemp())
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'control'))
import pytest
from app import billing, config, db, deploy

@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'STATE_DIR',tmp_path)
    monkeypatch.setattr(config,'DB_PATH',tmp_path/'state.sqlite3')
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('network prohibited'))
    db.init()
    ws=db.workspace()
    with db.conn() as c:
        c.execute('UPDATE workspaces SET balance_cents=100000 WHERE id=?',(ws['id'],))
        c.execute('INSERT INTO tools(id,workspace_id,slug,name,signing_key,db_password,created) VALUES(?,?,?,?,?,?,?)',
                  ('tool',ws['id'],'sample','Sample','synthetic','synthetic',0))
    monkeypatch.setattr(deploy,'status',lambda t:{'state':'running'})
    monkeypatch.setattr(billing,'_storage_gb',lambda t:1)
    return db.workspace()

def month(year,number):
    return [f'{year}-{number:02}-{day:02}' for day in range(1,calendar.monthrange(year,number)[1]+1)]

@pytest.mark.parametrize('year,number',[(2026,2),(2028,2),(2026,9),(2026,10),(2026,12)])
def test_full_calendar_month_is_exactly_ten_dollars(state,year,number):
    dates=month(year,number)
    for day in dates: billing.meter_once(day)
    assert billing.balance(state['id'])==99000
    for day in reversed(dates): assert billing.meter_once(day)==[]
    assert len(billing.ledger(state['id'],100))==len(dates)

def test_referral_full_month_is_exactly_five_dollars(state):
    with db.conn() as c:
        c.execute('UPDATE workspaces SET discount_until=? WHERE id=?',
                  (dt.datetime(2027,1,1,tzinfo=dt.timezone.utc).timestamp(),state['id']))
    for day in month(2026,10): billing.meter_once(day)
    assert billing.balance(state['id'])==99500

@pytest.mark.parametrize('gib,extra_cents',[(1.5,12),(2,25),(5,100),(10,225)])
def test_storage_fractional_cents_accumulate_instead_of_rounding_daily(state,monkeypatch,gib,extra_cents):
    monkeypatch.setattr(billing,'_storage_gb',lambda t:gib)
    for day in month(2026,10): billing.meter_once(day)
    assert billing.balance(state['id'])==100000-1000-extra_cents

def test_concurrent_meter_is_exactly_once_for_debit_and_storage(state,monkeypatch):
    monkeypatch.setattr(billing,'_storage_gb',lambda t:2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:billing.meter_once('2026-09-03'),range(4)))
    assert billing.balance(state['id'])==100000-billing.daily_rate(state,'2026-09-03')
    with db.conn() as c:
        assert c.execute('SELECT byte_cent_days FROM storage_accrual').fetchone()[0]==25*1024**3
        assert c.execute("SELECT count(*) FROM ledger WHERE kind='charge'").fetchone()[0]==1

def test_failed_measurement_does_not_commit_a_zero_storage_charge(state,monkeypatch):
    def unavailable(t): raise RuntimeError('measurement unavailable')
    monkeypatch.setattr(billing,'_storage_gb',unavailable)
    assert billing.meter_once('2026-09-03')==[]
    assert billing.balance(state['id'])==100000
    monkeypatch.setattr(billing,'_storage_gb',lambda t:2)
    assert len(billing.meter_once('2026-09-03'))==1

def test_legacy_charged_day_is_not_charged_again(state):
    billing.post(state['id'],'charge',-33,'sample: running day 2026-09-03','legacy-ref','meter')
    assert billing.meter_once('2026-09-03')==[]
    assert billing.balance(state['id'])==99967
