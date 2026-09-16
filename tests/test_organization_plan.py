"""Customer-visible bundle billing and hard organization admission invariants."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from test_resource_isolation import state, insert_tool, headers
from app import billing, config, db, deploy, main, resources

@pytest.fixture
def five(state,monkeypatch):
    tools=[insert_tool('a',f'app-{i}') for i in range(5)]
    monkeypatch.setattr(billing,'_storage_gb',lambda t:0.1)
    monkeypatch.setattr(billing,'_settle',lambda ws:None)
    return tools

def test_five_tools_cost_ten_total_for_full_month(five):
    for day in range(1,31): billing.meter_once(f'2026-09-{day:02}')
    assert billing.balance('ws_a')==9000
    assert len(billing.ledger('ws_a',100))==30

def test_repeated_ticks_and_adding_tools_do_not_multiply_charge(five,monkeypatch):
    monkeypatch.setattr(deploy,'status',lambda t:{'state':'running' if t['slug']=='app-0' else 'stopped'})
    billing.meter_once('2026-09-01')
    monkeypatch.setattr(deploy,'status',lambda t:{'state':'running'})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _:billing.meter_once('2026-09-01'),range(6)))
    assert billing.balance('ws_a')==9967

def test_two_organizations_are_billed_independently(five):
    insert_tool('b')
    billing.meter_once('2026-09-01')
    assert billing.balance('ws_a')==billing.balance('ws_b')==9967

def test_all_stopped_means_no_hosting_charge(five,monkeypatch):
    monkeypatch.setattr(deploy,'status',lambda t:{'state':'stopped'})
    assert billing.meter_once('2026-09-01')==[]
    assert billing.balance('ws_a')==10000

def test_legacy_rollout_day_is_not_billed_again(five):
    billing.post('ws_a','charge',-33,'app-0: running day 2026-09-01','meter:legacy')
    billing.meter_once('2026-09-01')
    assert billing.balance('ws_a')==9967
    billing.meter_once('2026-09-02')
    assert billing.balance('ws_a')==9934

def test_sixth_tool_is_refused_without_debit_and_other_org_is_unaffected(five):
    api=TestClient(main.app)
    a=headers('a');b=headers('b')
    response=api.post('/api/tools',headers=a,json={'slug':'sixth'})
    assert response.status_code==409 and 'five tools' in response.json()['detail']
    assert billing.balance('ws_a')==10000
    assert api.post('/api/tools',headers=b,json={'slug':'first'}).status_code==200
    info=api.get('/api/billing',headers=a).json()
    assert info['running_tools']==5 and info['burn_cents_per_day']==billing.daily_rate(db.workspace_by_id('ws_a'))
    assert info['organization_month_cents']==1000 and info['included_tools']==5

def test_concurrent_creates_cannot_exceed_five(state):
    api=TestClient(main.app);h=headers('a')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda n:api.post('/api/tools',headers=h,json={'slug':f'app-{n}'}).status_code,range(8)))
    assert results.count(200)==5 and results.count(409)==3

def test_pool_capacity_blocks_new_org_but_keeps_existing_org_working(state,monkeypatch):
    t=insert_tool('a');other=insert_tool('b')
    monkeypatch.setattr(config,'RESOURCE_GUARD',True)
    monkeypatch.setattr(config,'TOOL_MAX_RUNNING',30)
    monkeypatch.setattr(resources,'broker',lambda *a,**kw:{'enforced':True,'host_available_bytes':30*1024**3,'host_reserve_bytes':8*1024**3})
    live=[SimpleNamespace(name='tool-'+str(n),labels={'boathouse.workspace':('ws_a' if n==0 else f'ws_c{n}')}) for n in range(6)]
    monkeypatch.setattr(deploy,'client',lambda:SimpleNamespace(containers=SimpleNamespace(list=lambda **kw:live)))
    resources.admission(t)
    with pytest.raises(resources.ResourceError,match='organization capacity'):resources.admission(other)

def test_retained_allocations_cannot_grow_without_bound(state,monkeypatch):
    monkeypatch.setattr(config,'RESOURCE_GUARD',True)
    tools=[insert_tool('a',f'app-{n}') for n in range(6)]
    for t in tools:resources.limits(t)
    monkeypatch.setattr(resources,'broker',lambda *a,**kw:pytest.fail('must refuse before native allocation'))
    with pytest.raises(resources.ResourceError,match='retained data'):resources.ensure(tools[-1])


def test_extra_storage_is_metered_for_each_tool_without_multiplying_hosting(five,monkeypatch):
    monkeypatch.setattr(billing,'_storage_gb',lambda t:2.0)
    for day in range(1,31):billing.meter_once(f'2026-09-{day:02}')
    assert billing.balance('ws_a')==10000-1000-5*25
