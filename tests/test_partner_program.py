"""End-to-end attribution and money invariants, with a synthetic processor."""
import calendar
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault('BH_STATE_DIR',tempfile.mkdtemp(prefix='partner-test-'))
os.environ.setdefault('BH_DOMAIN','platform.test')
os.environ.setdefault('BH_OWNERS','host@fixture.test')
os.environ.update(BH_METER='0',BH_PG_PASSWORD='synthetic')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'control'))
import pytest
from fastapi.testclient import TestClient
from app import auth,billing,config,db,deploy,main,mail,payment_returns,referrals

PARTNER='partner@fixture.test'
CUSTOMER='customer@fixture.test'


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'STATE_DIR',tmp_path)
    monkeypatch.setattr(config,'DB_PATH',tmp_path/'state.sqlite3')
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('No network in partner tests'))
    db.init()
    main.create_workspace('Partner Studio',PARTNER,password='Synthetic-test-password-448!')
    code=referrals.code_for(PARTNER)
    main.create_workspace('Customer Private Business',CUSTOMER,code=code)
    ws=db.workspace('customer-private-busines')  # slug length is capped
    if ws is None:
        with db.conn() as c:
            ws=c.execute('SELECT * FROM workspaces WHERE owner_email=?',(CUSTOMER,)).fetchone()
    processor={'pi':{},'refunds':{},'disputes':{}}
    def stripe(method,path,*a,**k):
        assert method=='GET'
        if path.startswith('/payment_intents/'):
            return processor['pi'][path.split('/')[-1]]
        kind=path.split('?')[0].lstrip('/')
        ident=parse_qs(urlsplit(path).query)['payment_intent'][0]
        return {'data':processor[kind].get(ident,[]),'has_more':False}
    monkeypatch.setattr(billing,'_stripe',stripe)
    monkeypatch.setattr(billing,'_setting',lambda name:'synthetic')
    def pause(w,why):
        with db.conn() as c:c.execute('UPDATE workspaces SET paused=1 WHERE id=?',(w['id'],))
    def resume(w):
        with db.conn() as c:c.execute('UPDATE workspaces SET paused=0 WHERE id=?',(w['id'],))
    monkeypatch.setattr(billing,'pause',pause)
    monkeypatch.setattr(billing,'resume',resume)
    return ws,processor


def cash(env,amount,workspace=None,complete=True):
    ws,processor=env
    ws=workspace or ws
    with db.conn() as c:
        op=billing._new_payment(c,ws,amount,'synthetic test')
    ident='pi_'+op['id']
    pi={'id':ident,'status':'succeeded','amount':amount,'amount_received':amount,
        'currency':'usd','customer':op['customer'],'metadata':{'boathouse_operation':op['id']}}
    processor['pi'][ident]=pi
    if complete:billing._complete_payment(op,pi)
    return ident


def spend(ws,cents,kind='charge',prefix='meter:'):
    return billing.post(ws['id'],kind,-cents,'synthetic usage',prefix+db.new_id('test'),'test')


def total():
    return referrals.summary(PARTNER)


def browser(email=None):
    cl=TestClient(main.app,base_url='https://'+config.PLATFORM_DOMAIN,follow_redirects=False)
    if email:cl.cookies.set(config.PLATFORM_COOKIE,auth.create_platform_session(email))
    return cl


def test_attribution_is_customer_lifetime_and_does_not_reset_discount(env):
    ws,_=env
    code=referrals.code_for(PARTNER)
    result=main.create_workspace('Future Customer App',CUSTOMER)
    future=db.workspace(result['workspace'])
    assert future['referred_by']==PARTNER
    assert future['discount_until']==ws['discount_until']
    assert total()['customer_count']==1
    with pytest.raises(Exception,match='already has a referral'):
        main.create_workspace('Cannot Reassign',CUSTOMER,code=referrals.code_for('other@fixture.test'))
    with pytest.raises(Exception,match='own code'):
        main.create_workspace('Cannot Self Refer',PARTNER,code=code)
    with pytest.raises(Exception,match='new customer'):
        main.create_workspace('Cannot Add Later',PARTNER,code=referrals.code_for('other@fixture.test'))


def test_exact_carry_across_small_charges_and_workspaces(env):
    ws,_=env
    cash(env,100)
    main.create_workspace('Second Customer', 'second@fixture.test',code=referrals.code_for(PARTNER))
    second=db.workspace('second-customer')
    cash(env,100,second)
    for _ in range(9):spend(ws,1)
    assert total()['earned_cents']==0
    assert total()['fractional_cent_tenths']==9
    spend(second,1)
    assert total()['earned_cents']==1
    assert total()['fractional_cent_tenths']==0
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _:total()['earned_cents'],range(12)))==[1]*12


def test_grants_unverified_credit_unused_deposits_and_domains_do_not_earn(env):
    ws,_=env
    billing.post(ws['id'],'grant',100,'free',db.new_id('grant'),'test')
    billing.post(ws['id'],'topup',100,'unverified legacy',db.new_id('unverified'),'test')
    cash(env,1000)
    assert total()['earned_cents']==0
    spend(ws,200)
    assert total()['earned_cents']==0
    spend(ws,100,'domain','domain:')
    assert total()['earned_cents']==0
    spend(ws,150)
    assert total()['earned_cents']==15


def test_later_cash_only_earns_when_funding_actual_hosting_debt(env):
    ws,_=env
    spend(ws,200)
    assert total()['earned_cents']==0
    billing.post(ws['id'],'grant',50,'free',db.new_id('grant'),'test')
    assert total()['earned_cents']==0
    cash(env,300)
    assert total()['earned_cents']==15
    assert billing.balance(ws['id'])==150


@pytest.mark.parametrize('year,month',[(2027,2),(2028,2),(2026,4),(2026,7)])
@pytest.mark.parametrize('discount',[False,True])
def test_calendar_month_earns_exactly_ten_percent(env,monkeypatch,year,month,discount):
    ws,_=env
    cash(env,5000)
    with db.conn() as c:
        c.execute('UPDATE workspaces SET discount_until=? WHERE id=?',(4102444800 if discount else 0,ws['id']))
        c.execute('INSERT INTO tools(id,workspace_id,slug,name,signing_key,db_password,created,created_by) VALUES(?,?,?,?,?,?,?,?)',
                  ('t_partner_month',ws['id'],'calendar','Calendar','k','p',time.time(),CUSTOMER))
    monkeypatch.setattr(deploy,'status',lambda *a:{'state':'running'})
    monkeypatch.setattr(billing,'_storage_gb',lambda t:0.)
    for day in range(1,calendar.monthrange(year,month)[1]+1):
        billing.meter_once(f'{year}-{month:02d}-{day:02d}')
    assert total()['paid_usage_cents']==(500 if discount else 1000)
    assert total()['earned_cents']==(50 if discount else 100)


def test_commission_failure_rolls_back_usage_and_payment(env,monkeypatch):
    ws,proc=env
    cash(env,100)
    def fail(*a):raise RuntimeError('synthetic commission failure')
    monkeypatch.setattr(referrals,'sync',fail)
    with pytest.raises(RuntimeError):spend(ws,10)
    assert billing.balance(ws['id'])==100
    with pytest.raises(RuntimeError):cash(env,200)
    assert billing.balance(ws['id'])==100
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM ledger WHERE workspace_id=?",(ws['id'],)).fetchone()[0]==1
        assert c.execute("SELECT COUNT(*) FROM payment_operations WHERE workspace_id=? AND status='succeeded'",(ws['id'],)).fetchone()[0]==1


def test_partial_refund_full_refund_and_duplicate_events(env):
    ws,proc=env
    ident=cash(env,1000)
    spend(ws,800)
    row={'id':'re_synthetic','payment_intent':ident,'amount':500,'currency':'usd','status':'succeeded'}
    proc['refunds'][ident]=[row]
    payment_returns.reconcile(ident)
    assert total()['earned_cents']==50
    assert billing.balance(ws['id'])==-300
    assert db.workspace_by_id(ws['id'])['paused']
    payment_returns.reconcile(ident)
    assert billing.balance(ws['id'])==-300
    row['amount']=1000
    payment_returns.reconcile(ident)
    assert total()['earned_cents']==0
    assert billing.balance(ws['id'])==-800


def test_dispute_won_restores_credit_and_commission(env):
    ws,proc=env
    ident=cash(env,1000)
    spend(ws,100)
    row={'id':'dp_synthetic','payment_intent':ident,'amount':1000,'currency':'usd','status':'needs_response'}
    proc['disputes'][ident]=[row]
    payment_returns.reconcile(ident)
    assert total()['earned_cents']==0
    assert billing.balance(ws['id'])==-100
    row['status']='won'
    payment_returns.reconcile(ident)
    assert total()['earned_cents']==10
    assert billing.balance(ws['id'])==900
    assert not db.workspace_by_id(ws['id'])['paused']


def test_return_identity_mismatch_never_changes_balance(env):
    ws,proc=env
    ident=cash(env,1000)
    proc['refunds'][ident]=[{'id':'re_bad','payment_intent':'pi_wrong','amount':1000,'currency':'usd','status':'succeeded'}]
    with pytest.raises(billing.StripeError):payment_returns.reconcile(ident)
    assert billing.balance(ws['id'])==1000


def signed_event(kind,obj):
    payload=json.dumps({'type':kind,'data':{'object':obj}}).encode()
    ts=str(int(time.time()))
    sig=hmac.new(b'synthetic',ts.encode()+b'.'+payload,hashlib.sha256).hexdigest()
    return payload,f't={ts},v1={sig}'


def test_refund_before_success_event_then_repeated_success_does_not_restore_refunded_funds(env):
    ws,proc=env
    ident=cash(env,1000,complete=False)
    proc['refunds'][ident]=[{'id':'re_early','payment_intent':ident,'amount':1000,'currency':'usd','status':'succeeded'}]
    event=signed_event('charge.refunded',{'payment_intent':ident})
    assert billing.handle_webhook(*event)['reconciled']
    assert billing.balance(ws['id'])==0
    billing.handle_webhook(*signed_event('payment_intent.succeeded',proc['pi'][ident]))
    assert billing.balance(ws['id'])==0
    with pytest.raises(billing.StripeError):billing.handle_webhook(event[0],'t=0,v1=bad')


def test_payout_minimum_snapshot_exact_reference_and_refund_carry(env):
    ws,proc=env
    ident=cash(env,20000)
    spend(ws,9999)
    with pytest.raises(referrals.ReferralError,match='start at'):referrals.prepare_payout(PARTNER,'host')
    spend(ws,1)
    p=referrals.prepare_payout(PARTNER,'host')
    assert p['cents']==1000
    assert referrals.prepare_payout(PARTNER,'host')['id']==p['id']
    spend(ws,100)
    with pytest.raises(referrals.ReferralError):referrals.mark_paid(PARTNER,'transfer-test','host',p['id'],1001)
    assert referrals.mark_paid(PARTNER,'transfer-test','host',p['id'],1000)==1000
    assert referrals.mark_paid(PARTNER,'transfer-test','host',p['id'],1000)==1000
    assert total()['unpaid_cents']==10
    proc['refunds'][ident]=[{'id':'re_after_paid','payment_intent':ident,'amount':20000,'currency':'usd','status':'succeeded'}]
    payment_returns.reconcile(ident)
    assert total()['earned_cents']==0 and total()['paid_cents']==1000 and total()['unpaid_cents']==-1000
    assert total()['available_cents']==0


def test_prepare_reconciles_returns_before_payout_and_can_cancel(env):
    ws,proc=env
    ident=cash(env,20000)
    spend(ws,12000)
    p=referrals.prepare_payout(PARTNER,'host')
    referrals.cancel_payout(p['id'],'host')
    assert total()['available_cents']==1200
    proc['refunds'][ident]=[{'id':'re_before_paid','payment_intent':ident,'amount':15000,'currency':'usd','status':'succeeded'}]
    with pytest.raises(referrals.ReferralError,match='start at'):referrals.prepare_payout(PARTNER,'host')
    assert total()['earned_cents']==500


def test_partner_signup_verified_lands_in_dashboard(env,monkeypatch):
    messages=[]
    monkeypatch.setattr(mail,'send',lambda *a,**k:messages.append(a) or True)
    cl=browser()
    page=cl.get('/signup?partner=1')
    assert 'No app or hosting purchase required' in page.text
    r=cl.post('/signup',data={'workspace':'New Partner Studio','email':'newpartner@fixture.test','partner':'1','csrf':auth.csrf_token('signup')})
    assert r.status_code==202
    url=re.search(r'https://\S+/verify-email/\S+',messages[0][2]).group()
    path=urlsplit(url).path
    assert cl.get(path).status_code==200
    r=cl.post(path,data={'password':'Synthetic-test-password-448!','password2':'Synthetic-test-password-448!','csrf':auth.csrf_token('verify-email')})
    assert r.status_code==303 and r.headers['location']=='/partners'
    dashboard=cl.get('/partners')
    assert dashboard.status_code==200 and 'Download QR code' in dashboard.text
    assert 'no-store' in dashboard.headers['cache-control']
    assert referrals.lookup(referrals.code_for('newpartner@fixture.test'))=='newpartner@fixture.test'


def test_referred_email_signup_preserves_code_and_lifetime_attribution(env,monkeypatch):
    messages=[]
    monkeypatch.setattr(mail,'send',lambda *a,**k:messages.append(a) or True)
    cl=browser()
    code=referrals.code_for(PARTNER)
    r=cl.post('/signup',data={'workspace':'Verified Customer','email':'verified@fixture.test','code':code,'csrf':auth.csrf_token('signup')})
    assert r.status_code==202
    path=urlsplit(re.search(r'https://\S+/verify-email/\S+',messages[0][2]).group()).path
    r=cl.post(path,data={'password':'Synthetic-test-password-448!','password2':'Synthetic-test-password-448!','csrf':auth.csrf_token('verify-email')})
    assert r.status_code==303 and r.headers['location'].startswith('/welcome')
    assert db.workspace('verified-customer')['referred_by']==PARTNER


def test_dashboard_privacy_qr_and_payout_access(env):
    code=referrals.code_for(PARTNER)
    public=browser()
    assert 'Get your free partner code' in public.get('/partners').text
    img=public.get(f'/partners/qr/{code}.png?download=1')
    assert img.status_code==200 and img.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert 'attachment' in img.headers['content-disposition']
    assert public.get('/partners/qr/UNKNOWN.png').status_code==404
    assert public.get('/partners',headers={'host':'attacker.invalid'}).status_code==404
    cl=browser(PARTNER)
    dashboard=cl.get('/partners')
    assert CUSTOMER not in dashboard.text and 'Customer Private Business' not in dashboard.text
    assert 'Customer Private Business' not in json.dumps(total())
    assert cl.get('/partners/payouts').status_code==403
    assert cl.post('/partners/payouts/prepare',data={'email':PARTNER,'csrf':auth.csrf_token('partner-payout')}).status_code==403
    with db.conn() as c:
        hostemail=c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner'",(db.workspace(config.HOST_WORKSPACE)['id'],)).fetchone()[0]
    admin=browser(hostemail)
    assert admin.get('/partners/payouts').status_code==200
    assert admin.post('/partners/payouts/prepare',data={'email':PARTNER}).status_code==403
    assert admin.post('/partners/payouts/prepare',headers={'origin':'https://attacker.invalid'},data={'email':PARTNER,'csrf':auth.csrf_token('partner-payout')}).status_code==403


@pytest.mark.parametrize('status',['warning_needs_response','warning_under_review','warning_closed'])
def test_inquiries_do_not_remove_customer_funds(env,status):
    ws,proc=env
    ident=cash(env,1000)
    spend(ws,100)
    proc['disputes'][ident]=[{'id':'dp_inquiry','payment_intent':ident,'amount':1000,'currency':'usd','status':status}]
    payment_returns.reconcile(ident)
    assert billing.balance(ws['id'])==900
    assert total()['earned_cents']==10


def test_existing_earnings_migration_preserves_history(env):
    ws,_=env
    cash(env,1000);spend(ws,100)
    with db.conn() as c:
        c.execute('DELETE FROM referral_program')
        c.execute('DELETE FROM referral_revenue')
        c.execute('DELETE FROM referral_earnings')
        c.execute("INSERT INTO referral_earnings(id,referrer_email,workspace_id,cents,ledger_ref,created) VALUES('legacy',?,?,7,'legacy-ref',?)",(PARTNER,ws['id'],time.time()))
    db.init();db.init()
    assert total()['earned_cents']==7
    spend(ws,100)
    assert total()['earned_cents']==17
    db.init()
    assert total()['earned_cents']==17


def test_refund_pagination_is_complete(env,monkeypatch):
    rows=[{'id':'re_one'},{'id':'re_two'}]
    calls=[]
    def stripe(method,path):
        calls.append(path)
        second='starting_after=re_one' in path
        return {'data':[rows[1] if second else rows[0]],'has_more':not second}
    monkeypatch.setattr(billing,'_stripe',stripe)
    assert payment_returns._list('refunds','pi_test')==rows
    assert len(calls)==2


def test_payout_form_prepares_and_records_only_confirmed_exact_transfer(env):
    ws,_=env
    cash(env,20000);spend(ws,11000)
    with db.conn() as c:
        email=c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner'",(db.workspace(config.HOST_WORKSPACE)['id'],)).fetchone()[0]
    cl=browser(email)
    fields={'email':PARTNER,'csrf':auth.csrf_token('partner-payout')}
    r=cl.post('/partners/payouts/prepare',data=fields)
    assert r.status_code==303
    p=total()['payout_history'][0]
    assert p['cents']==1100 and p['status']=='prepared'
    fields.update(payout_id=p['id'],cents=1100,ref='completed-bank-test')
    cl.post('/partners/payouts/record',data=fields)
    assert total()['paid_cents']==0
    fields['transferred']='yes'
    cl.post('/partners/payouts/record',data=fields)
    assert total()['paid_cents']==1100 and total()['unpaid_cents']==0
    assert 'no-store' in cl.get('/partners/payouts').headers['cache-control']
