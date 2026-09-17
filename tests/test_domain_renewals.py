"""Renewal money safety, owner controls and crash recovery; network prohibited."""
import concurrent.futures
import datetime as dt
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
os.environ.setdefault('BH_DOMAIN','renewals.example')
os.environ.setdefault('BH_PG_PASSWORD','synthetic')
os.environ.setdefault('BH_OWNERS','owner@example.test')
os.environ.setdefault('BH_STATE_DIR',tempfile.mkdtemp())
os.environ['BH_METER']='0'
sys.path.insert(0,str(Path(__file__).parents[1]/'control'))
import pytest
from fastapi import HTTPException
from app import auth,billing,config,db,domain_renewals as rn,front,main,mail,registrar
DOMAIN='renewal.example'
NOW=dt.datetime(2027,7,1,tzinfo=dt.timezone.utc).timestamp()

@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'STATE_DIR',tmp_path)
    monkeypatch.setattr(config,'DB_PATH',tmp_path/'state.sqlite3')
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('network forbidden'))
    clock=[NOW];monkeypatch.setattr(rn,'time',types.SimpleNamespace(time=lambda:clock[0]))
    db.init();ws=db.workspace();billing.post(ws['id'],'grant',5000,'synthetic')
    with db.conn() as c:c.execute('INSERT INTO domain_allocations VALUES (?,?,?)',(DOMAIN,ws['id'],NOW))
    monkeypatch.setattr(registrar,'creds',lambda:{'apikey':'synthetic','secretapikey':'not-a-key'})
    monkeypatch.setattr(registrar,'is_sandbox',lambda:True)
    monkeypatch.setattr(registrar,'status',lambda:{'balance_cents':50000})
    info={'domain':DOMAIN,'status':'ACTIVE','apiAccess':1,'autoRenew':1,'expireDate':dt.datetime.fromtimestamp(NOW+30*rn.DAY,dt.timezone.utc).isoformat()}
    monkeypatch.setattr(registrar,'domain_info',lambda d:dict(info,domain=d))
    disabled=[]
    def turn_off(d):
        disabled.append(d);info['autoRenew']=0;return dict(info,domain=d)
    monkeypatch.setattr(registrar,'disable_auto_renew',turn_off)
    q={'cost_cents':1100,'years':1};monkeypatch.setattr(registrar,'renewal_quote',lambda d:dict(q))
    calls=[]
    def renew(d,cost,dry_run,idempotency_key=None):
        if dry_run:return {'status':'SUCCESS','dryRun':True,'wouldSucceed':True,'sufficientFunds':True,'cost':cost}
        calls.append(idempotency_key)
        info['expireDate']='2028-07-31'
        return {'status':'SUCCESS','domain':d,'cost':cost,'orderId':8001,'expirationDate':'2028-07-31'}
    monkeypatch.setattr(registrar,'renew',renew)
    messages=[];monkeypatch.setattr(mail,'send',lambda *a,**kw:messages.append(a) or True)
    monkeypatch.setattr(billing,'_stripe',lambda *a,**k:pytest.fail('renewals must never charge a card'))
    return types.SimpleNamespace(ws=ws,clock=clock,info=info,q=q,calls=calls,disabled=disabled,messages=messages)

def ready(s):
    rn.run_once();s.clock[0]+=16*rn.DAY

def row():
    with db.conn() as c:return dict(c.execute('SELECT * FROM domain_renewals ORDER BY created DESC LIMIT 1').fetchone())

def balance(s):return billing.balance(s.ws['id'])

def test_notice_no_early_charge_and_only_allocated_domain(setup):
    s=setup;assert rn.run_once()['errors']==0
    assert s.disabled==[DOMAIN] and not s.calls and balance(s)==5000
    assert any('Upcoming renewal' in m[1] for m in s.messages)
    rn.run_once();assert s.disabled==[DOMAIN]
    assert len([m for m in s.messages if 'Upcoming renewal' in m[1]])==1

def test_renewal_charges_once_receipt_and_new_expiry(setup):
    s=setup;ready(s);rn.run_once();rn.run_once()
    assert len(s.calls)==1 and balance(s)==3700 and row()['state']=='renewed'
    assert any(m[1].startswith('Renewed:') for m in s.messages)
    assert rn.settings(s.ws['id'])[0]['expires_at']>NOW+365*rn.DAY
    assert len([l for l in billing.ledger(s.ws['id']) if l['kind']=='charge'])==1

def test_existing_off_stays_off_and_detached_is_visible(setup):
    s=setup;s.info['autoRenew']=0;ready(s);rn.run_once()
    assert not s.calls and not s.disabled and balance(s)==5000
    assert rn.settings(s.ws['id'])[0]['enabled']==0

def test_customer_shortfall_never_spends_host_money(setup):
    s=setup;ready(s);billing.post(s.ws['id'],'adjustment',-4000,'synthetic');rn.run_once()
    assert not s.calls and balance(s)==1000 and 'Add credit' in rn.settings(s.ws['id'])[0]['error']
    billing.post(s.ws['id'],'grant',1000,'synthetic');rn.run_once()
    assert len(s.calls)==1 and balance(s)==700

def test_provider_shortfall_never_debits_customer(setup,monkeypatch):
    s=setup;ready(s)
    monkeypatch.setattr(registrar,'renew',lambda *a,**k:{'dryRun':True,'wouldSucceed':False,'sufficientFunds':False,'cost':1100})
    rn.run_once();assert balance(s)==5000 and row()['state']=='waiting'

def test_price_increase_needs_limit_and_new_seven_day_notice(setup):
    s=setup;ready(s);s.q['cost_cents']=1500;rn.run_once();assert balance(s)==5000 and not s.calls
    rn.set_policy(s.ws['id'],DOMAIN,True,1700,'owner@example.test')
    rn.run_once();assert not s.calls
    s.clock[0]+=7*rn.DAY;rn.run_once();assert len(s.calls)==1 and balance(s)==3300

def test_failed_email_blocks_until_notice_is_sent_and_waited(setup,monkeypatch):
    s=setup;monkeypatch.setattr(mail,'send',lambda *a,**k:False);ready(s);rn.run_once()
    assert not s.calls and balance(s)==5000
    monkeypatch.setattr(mail,'send',lambda *a,**k:True)
    s.clock[0]+=rn.DAY;rn.run_once();assert not s.calls
    s.clock[0]+=7*rn.DAY;rn.run_once();assert len(s.calls)==1

def test_cancellation_and_workspace_isolation(setup):
    s=setup;ready(s)
    with pytest.raises(HTTPException) as e:rn.set_policy('wrong',DOMAIN,False,None,'wrong@example.test')
    assert e.value.status_code==404
    rn.set_policy(s.ws['id'],DOMAIN,False,None,'owner@example.test');rn.run_once()
    assert not s.calls and balance(s)==5000

@pytest.mark.parametrize('value',[True,0,-1,1.2,'2000',100001])
def test_limit_validation(value,setup):
    rn.run_once()
    with pytest.raises(HTTPException):rn.set_policy(setup.ws['id'],DOMAIN,True,value,'owner@example.test')

def test_changed_provider_connection_cannot_spend(setup,monkeypatch):
    ready(setup);monkeypatch.setattr(registrar,'creds',lambda:{'apikey':'changed'});rn.run_once()
    assert not setup.calls and balance(setup)==5000 and setup.disabled==[DOMAIN]

def test_lost_response_replays_same_operation(setup,monkeypatch):
    s=setup;ready(s);original=registrar.renew;keys=[]
    def lost(d,cost,dry_run,idempotency_key=None):
        if dry_run:return original(d,cost,dry_run,idempotency_key)
        keys.append(idempotency_key)
        if len(keys)==1:raise registrar.RegistrarError('lost',uncertain=True)
        return original(d,cost,dry_run,idempotency_key)
    monkeypatch.setattr(registrar,'renew',lost);rn.run_once()
    assert row()['state']=='uncertain' and balance(s)==3700
    with pytest.raises(HTTPException):rn.set_policy(s.ws['id'],DOMAIN,False,None,'owner@example.test')
    rn.run_once();assert row()['state']=='renewed' and balance(s)==3700 and keys[0]==keys[1]

def test_stale_uncertain_never_rebuys_or_refunds(setup,monkeypatch):
    s=setup;ready(s);original=registrar.renew
    def fail(d,cost,dry_run,idempotency_key=None):
        if dry_run:return original(d,cost,dry_run,idempotency_key)
        raise registrar.RegistrarError('lost',uncertain=True)
    monkeypatch.setattr(registrar,'renew',fail);rn.run_once();s.clock[0]+=rn.DAY
    monkeypatch.setattr(registrar,'renew',lambda *a,**k:pytest.fail('no second purchase'))
    rn.run_once();assert balance(s)==3700 and row()['state']=='uncertain'
    assert 'reconcile' in rn.settings(s.ws['id'])[0]['error']

def test_definite_failure_refunds_once(setup,monkeypatch):
    s=setup;ready(s);original=registrar.renew
    def fail(d,cost,dry_run,idempotency_key=None):
        if dry_run:return original(d,cost,dry_run,idempotency_key)
        raise registrar.RegistrarError('price moved','PRICE_CHANGED')
    monkeypatch.setattr(registrar,'renew',fail);rn.run_once()
    assert row()['state']=='failed' and balance(s)==5000
    rn._release(row(),'again');assert balance(s)==5000

def test_auth_error_after_uncertain_does_not_refund(setup,monkeypatch):
    s=setup;ready(s);original=registrar.renew
    def fail(d,cost,dry_run,idempotency_key=None):
        if dry_run:return original(d,cost,dry_run,idempotency_key)
        raise registrar.RegistrarError('lost',uncertain=True)
    monkeypatch.setattr(registrar,'renew',fail);rn.run_once()
    monkeypatch.setattr(registrar,'renew',lambda *a,**k:(_ for _ in ()).throw(registrar.RegistrarError('auth failed','AUTH')))
    rn.run_once();assert balance(s)==3700 and row()['state']=='uncertain'

@pytest.mark.parametrize('change',[{'cost':999},{'domain':'another.example'},{'orderId':None},{'dryRun':True},{'expirationDate':'bad'},{'expirationDate':'2027-07-01'}])
def test_bad_provider_proof_retains_credit(setup,monkeypatch,change):
    s=setup;ready(s);original=registrar.renew
    def bad(d,cost,dry_run,idempotency_key=None):
        r=original(d,cost,dry_run,idempotency_key);return r if dry_run else {**r,**change}
    monkeypatch.setattr(registrar,'renew',bad);rn.run_once()
    assert balance(s)==3700 and row()['state']=='uncertain'

def test_local_settlement_crash_recovers_without_provider_call(setup):
    s=setup;ready(s)
    with db.conn() as c:c.execute("CREATE TRIGGER break_renewal BEFORE UPDATE OF kind ON ledger WHEN NEW.kind='charge' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    rn.run_once();assert row()['state']=='uncertain' and row()['provider_result']
    with db.conn() as c:c.execute('DROP TRIGGER break_renewal')
    rn.run_once();assert row()['state']=='renewed' and len(s.calls)==1 and balance(s)==3700

def test_concurrent_attempts_cannot_double_debit(setup,monkeypatch):
    s=setup;ready(s);r=row();entered=threading.Event();release=threading.Event();original=registrar.renew
    def slow(*a,**k):
        entered.set();assert release.wait(5);return original(*a,**k)
    monkeypatch.setattr(registrar,'renew',slow)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(rn.execute,r);assert entered.wait(5)
        try:assert pool.submit(rn.execute,r).result() is None
        finally:release.set()
        first.result()
    assert len(s.calls)==1 and balance(s)==3700

def test_new_owner_needs_notice(setup):
    s=setup;ready(s)
    with db.conn() as c:c.execute("INSERT INTO users (id,workspace_id,email,role,created) VALUES ('new',?,'new@example.test','owner',?)",(s.ws['id'],NOW))
    rn.run_once();assert not s.calls
    s.clock[0]+=7*rn.DAY;rn.run_once();assert len(s.calls)==1

def test_late_detection_does_not_renew_after_expiry(setup):
    s=setup;s.info['expireDate']=dt.datetime.fromtimestamp(NOW+3*rn.DAY,dt.timezone.utc).isoformat()
    rn.run_once();s.clock[0]+=8*rn.DAY;rn.run_once();assert not s.calls and balance(s)==5000

def test_api_owner_and_domain_scope(setup,monkeypatch):
    s=setup;rn.run_once()
    monkeypatch.setattr(main,'_actor',lambda *a:{'workspace_id':s.ws['id'],'role':'member','email':'member@example.test'})
    with pytest.raises(HTTPException) as e:main.domain_renewal_settings(None,None)
    assert e.value.status_code==403
    monkeypatch.setattr(main,'_actor',lambda *a:{'workspace_id':s.ws['id'],'role':'owner','email':'owner@example.test'})
    assert len(main.domain_renewal_settings(None,None)['domains'])==1
    with pytest.raises(HTTPException):main.domain_renewal_policy('other.example',None,{'enabled':False},None)

def test_owner_page_and_csrf_gate(setup,monkeypatch):
    s=setup;rn.run_once();monkeypatch.setattr(front,'_owner',lambda *a:('owner@example.test',s.ws,None))
    response=front.renewal_page(s.ws['slug'],None)
    assert response.status_code==200 and b'Maximum per renewal' in response.body and DOMAIN.encode() in response.body
    monkeypatch.setattr(front,'_guard',lambda *a:(None,None,None,'csrf rejected'))
    assert front.renewal_form(s.ws['slug'],DOMAIN,None,'bad','off','13')=='csrf rejected'

def test_multi_year_fee(setup):
    s=setup;s.q.update(cost_cents=2200,years=2);ready(s);rn.run_once()
    assert balance(s)==2400 and row()['margin_cents']==400

def test_former_owner_does_not_get_queued_mail(setup,monkeypatch):
    s=setup;monkeypatch.setattr(mail,'send',lambda *a,**k:False);rn.run_once()
    with db.conn() as c:c.execute('DELETE FROM users WHERE workspace_id=?',(s.ws['id'],))
    monkeypatch.setattr(mail,'send',lambda *a,**k:pytest.fail('former owner'))
    s.clock[0]+=rn.DAY;rn.flush_notices()

def test_failed_notice_is_visible_as_action_needed(setup,monkeypatch):
    s=setup;monkeypatch.setattr(mail,'send',lambda *a,**k:False);ready(s);rn.run_once()
    assert 'notice' in rn.settings(s.ws['id'])[0]['error'] and not s.calls

def test_disabled_api_access_does_not_turn_off_existing_registrar_safety(setup):
    setup.info['apiAccess']=0;rn.run_once()
    assert not setup.disabled and not setup.calls

def test_replayed_original_rejection_releases_uncertain_hold(setup,monkeypatch):
    s=setup;ready(s);original=registrar.renew
    def fail(d,cost,dry_run,idempotency_key=None):
        if dry_run:return original(d,cost,dry_run,idempotency_key)
        raise registrar.RegistrarError('lost',uncertain=True)
    monkeypatch.setattr(registrar,'renew',fail);rn.run_once()
    monkeypatch.setattr(registrar,'renew',lambda *a,**k:(_ for _ in ()).throw(registrar.RegistrarError('original declined',replayed=True)))
    rn.run_once();assert balance(s)==5000 and row()['state']=='failed'

def test_inflight_worker_is_not_started_twice(setup):
    with db.conn() as c:c.execute('INSERT INTO domain_renewal_worker VALUES (1,?)',(NOW+1800,))
    assert rn.run_once()=={'busy':True} and not setup.disabled

def test_purchase_quote_exposes_ongoing_consent_and_settlement_retains_cap(setup,monkeypatch):
    from app import domain_payments as dp
    s=setup
    monkeypatch.setattr(registrar,'check',lambda d:{'available':True,'premium':False,'price_cents':400,'renewal_cents':1100})
    monkeypatch.setattr(registrar,'buy',lambda d,cost,dry_run,idempotency_key=None:{'status':'SUCCESS','domain':d,'cost':cost,'orderId':1001,'wouldSucceed':True})
    q=dp.quote(s.ws,'new.example','owner@example.test')
    assert 'automatic renewal' in q['renewal_policy']
    dp.purchase(s.ws,'new.example',q['quote_id'],q['cost_cents'])
    new=next(d for d in rn.settings(s.ws['id']) if d['domain']=='new.example')
    assert new['enabled']==1 and new['max_cost_cents']==1300 and not new['managed']

def test_browser_controls_end_to_end_with_real_session_and_csrf(setup,monkeypatch):
    from fastapi.testclient import TestClient
    import re
    s=setup;rn.run_once()
    with db.conn() as c:owner=c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner' LIMIT 1",(s.ws['id'],)).fetchone()[0]
    client=TestClient(main.app,base_url='https://'+config.PLATFORM_DOMAIN,follow_redirects=False)
    path=f"/account/{s.ws['slug']}/domains"
    assert client.get(path).status_code==302
    client.cookies.set(config.PLATFORM_COOKIE,auth.create_platform_session(owner),domain=config.PLATFORM_DOMAIN)
    page=client.get(path);assert page.status_code==200 and DOMAIN in page.text
    token=re.search(r'name=csrf value="([^"]+)"',page.text)[1]
    post=path+'/'+DOMAIN+'/renewal'
    blocked=client.post(post,data={'csrf':token,'enabled':'off','maximum':'13'},headers={'Origin':'https://untrusted.example'})
    assert blocked.status_code==403 and rn.settings(s.ws['id'])[0]['enabled']==1
    saved=client.post(post,data={'csrf':token,'enabled':'off','maximum':'13'},headers={'Origin':'https://'+config.PLATFORM_DOMAIN})
    assert saved.status_code==303 and rn.settings(s.ws['id'])[0]['enabled']==0
    for bad in ['NaN','Infinity','12.345']:
        assert client.post(post,data={'csrf':token,'enabled':'on','maximum':bad}).status_code==422
    saved=client.post(post,data={'csrf':token,'enabled':'on','maximum':'15.50'})
    assert saved.status_code==303 and rn.settings(s.ws['id'])[0]['max_cost_cents']==1550


def test_mcp_preview_has_no_mutation_and_confirmation_uses_scoped_api(setup):
    import asyncio
    from app import mcp
    class Response:
        def json(self):return {'enabled':False}
    class Api:
        def __init__(self):self.calls=[]
        async def patch(self,path,json):self.calls.append((path,json));return Response()
    api=Api();args={'domain':DOMAIN,'enabled':False}
    text,result=asyncio.run(mcp.t_domain_renewal_set(api,args))
    assert not api.calls and 'Preview only' in text
    asyncio.run(mcp.t_domain_renewal_set(api,{**args,'confirm':True}))
    assert api.calls[0][0]==f'/api/domains/{DOMAIN}/renewal'

def test_registrar_renewal_quote_never_uses_registration_promotion(monkeypatch):
    # Exercise the actual adapter, not the fixture's synthetic price function.
    import importlib.util
    spec=importlib.util.spec_from_file_location('app.registrar_adapter_review',Path(registrar.__file__))
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    response={'response':{'type':'registration','price':'2.04','firstYearPromo':'yes','minDuration':1,'premium':'no','additional':{'renewal':{'type':'renewal','price':'13.04'}}}}
    monkeypatch.setattr(adapter,'_call',lambda *a,**k:response)
    assert adapter.renewal_quote('promo.example')=={'cost_cents':1304,'years':1}
    response['response']['additional']={}
    with pytest.raises(adapter.RegistrarError):adapter.renewal_quote('promo.example')
