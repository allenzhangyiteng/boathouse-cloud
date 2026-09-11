"""Actual state transitions with a fake provider: no real card or network use."""
import json
import concurrent.futures
import time
from types import SimpleNamespace
import pytest
from test_payments import setup, quote
from app import billing, checkout, db, front, main, mcp

class CheckoutProcessor:
    def __init__(self,monkeypatch):
        self.sessions={};self.intents={};self.requests=[];self.lose_response=False
        monkeypatch.setattr(billing,'_stripe',self.request)
    def request(self,method,path,data=None,**kw):
        self.requests.append((method,path,data,kw))
        if path.startswith('/customers/') and method=='GET': return {'id':'cus_synthetic'}
        if path=='/customers': return {'id':'cus_synthetic'}
        if method=='GET' and path.startswith('/payment_intents/'): return self.intents[path.rsplit('/',1)[-1]]
        if method=='GET': return next(iter(self.sessions.values()))
        assert path=='/checkout/sessions'
        key=kw['idempotency_key']
        session=self.sessions.setdefault(key,{'id':'cs_'+str(len(self.sessions)+1),'status':'open','payment_status':'unpaid','mode':'payment','customer':data['customer'],
            'amount_total':data['line_items[0][price_data][unit_amount]'],'currency':'usd','metadata':{'boathouse_operation':data['metadata[boathouse_operation]']},
            'url':'https://checkout.stripe.com/c/pay/synthetic','payment_intent':None})
        if self.lose_response:
            self.lose_response=False
            raise billing.StripeError('Response lost',ambiguous=True)
        return session
    def pay(self):
        session=next(iter(self.sessions.values())); op=session['metadata']['boathouse_operation']
        pi={'id':'pi_checkout','status':'succeeded','amount':session['amount_total'],'amount_received':session['amount_total'],'currency':'usd',
            'customer':session['customer'],'metadata':{'boathouse_operation':op},'payment_method':'pm_checkout'}
        self.intents[pi['id']]=pi
        session.update(status='complete',payment_status='paid',payment_intent=pi['id'],url=None)
        return pi

def start(ws,op,cents=500): return checkout.start(ws,cents,'owner@review.test',op)

def test_no_saved_card_checkout_then_replay_and_webhook_credit_once(setup,monkeypatch):
    processor=CheckoutProcessor(monkeypatch)
    with db.conn() as c: c.execute('UPDATE workspaces SET stripe_pm=NULL,autorefill_cents=0')
    op=quote(setup)
    result=start(setup,op)
    assert result['requires_checkout'] and billing.balance(setup['id'])==0
    pi=processor.pay()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _:checkout.reconcile(setup,op),range(4)))
    with db.conn() as c: row=c.execute('SELECT * FROM payment_operations WHERE id=?',(op,)).fetchone()
    billing._complete_payment(row,pi)
    assert billing.balance(setup['id'])==500
    assert len(billing.ledger(setup['id']))==1
    assert db.workspace()['stripe_pm']=='pm_checkout'
    assert db.workspace()['autorefill_cents']==0

def test_lost_response_reuses_immutable_checkout_and_never_charges_off_session(setup,monkeypatch):
    processor=CheckoutProcessor(monkeypatch);processor.lose_response=True;op=quote(setup)
    with pytest.raises(billing.StripeError): start(setup,op)
    with db.conn() as c:c.execute("UPDATE workspaces SET stripe_pm='different'")
    start(setup,op)
    posts=[r for r in processor.requests if r[0]=='POST']
    assert len(posts)==2 and posts[0]==posts[1] and len(processor.sessions)==1
    with pytest.raises(billing.StripeError,match='secure checkout'):
        billing.charge_card(setup,500,'test','owner',op)
    assert billing.balance(setup['id'])==0

def test_expired_checkout_releases_hold_but_cancellation_redirect_does_not(setup,monkeypatch):
    processor=CheckoutProcessor(monkeypatch);op=quote(setup);start(setup,op)
    assert checkout.reconcile(setup,op)['payment_status']=='pending'
    with pytest.raises(billing.StripeError):billing.quote_topup(setup,1000,'owner')
    next(iter(processor.sessions.values())).update(status='expired',url=None)
    assert checkout.reconcile(setup,op)['payment_status']=='expired'
    assert billing.quote_topup(setup,1000,'owner')['operation_id']!=op
    assert billing.balance(setup['id'])==0

@pytest.mark.parametrize('field,value',[('customer','wrong'),('amount_total',1),('currency','eur'),('mode','setup'),('id','cs_other')])
def test_mismatched_session_never_credits(setup,monkeypatch,field,value):
    processor=CheckoutProcessor(monkeypatch);op=quote(setup);start(setup,op);processor.pay()
    next(iter(processor.sessions.values()))[field]=value
    with pytest.raises(billing.StripeError):checkout.reconcile(setup,op)
    assert billing.balance(setup['id'])==0

def test_unknown_return_or_other_workspace_cannot_credit(setup,monkeypatch):
    processor=CheckoutProcessor(monkeypatch);op=quote(setup);start(setup,op);processor.pay()
    with pytest.raises(billing.StripeError):checkout.reconcile({'id':'other'},op)
    with pytest.raises(billing.StripeError):checkout.reconcile(setup,'fake')
    assert billing.balance(setup['id'])==0

def test_unpaid_completed_session_never_credits(setup,monkeypatch):
    processor=CheckoutProcessor(monkeypatch);op=quote(setup);start(setup,op)
    next(iter(processor.sessions.values())).update(status='complete',payment_status='unpaid',url=None)
    assert checkout.reconcile(setup,op)['payment_status']=='pending'
    assert billing.balance(setup['id'])==0
