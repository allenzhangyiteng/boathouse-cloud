"""Hosted manual top-ups. Immutable submissions, provider reconciliation, no redirect trust."""
import json
import time
from . import billing, config, db

def _result(op, session):
    if (session.get('mode')!='payment' or session.get('customer')!=op['customer'] or
        session.get('amount_total')!=op['cents'] or session.get('currency')!='usd' or
        (session.get('metadata') or {}).get('boathouse_operation')!=op['id'] or
        (op['checkout_id'] and op['checkout_id']!=session.get('id'))):
        raise billing.StripeError('Checkout does not match the approved payment. Contact support.',ambiguous=True)
    if not session.get('id','').startswith('cs_'):
        raise billing.StripeError('Checkout identity is missing.',ambiguous=True)
    with db.conn() as c:
        c.execute('UPDATE payment_operations SET checkout_id=?,updated=? WHERE id=?',(session['id'],time.time(),op['id']))
    pi=session.get('payment_intent')
    if isinstance(pi,str): pi=billing._stripe('GET','/payment_intents/'+pi)
    if pi and pi.get('status')=='succeeded':
        balance=billing._complete_payment(op,pi)
        ws=db.workspace_by_id(op['workspace_id'])
        if ws['paused'] and balance>billing.PAUSE_AT: billing.resume(ws)
        return {'payment_status':'succeeded','balance_cents':balance,'operation_id':op['id']}
    if pi: billing._validate_payment_identity(op,pi)
    if session.get('status')=='expired' and (not pi or pi.get('status') in ('canceled','requires_payment_method')):
        with db.conn() as c:
            c.execute("UPDATE payment_operations SET status='failed',error='Checkout expired without payment.',updated=? WHERE id=? AND status!='succeeded'",(time.time(),op['id']))
        return {'payment_status':'expired','operation_id':op['id']}
    url=session.get('url')
    if url and not url.startswith('https://checkout.stripe.com/'):
        raise billing.StripeError('Unexpected checkout address.',ambiguous=True)
    return {'payment_status':'pending','operation_id':op['id'],'checkout_url':url,
            'requires_checkout':True,'message':'Complete this payment on Stripe. Credit appears only after payment is confirmed.'}

def start(ws,cents,by,operation_id):
    with billing.payment_lock(ws['id']),db.conn() as c:
        ws=db.workspace_by_id(ws['id'])
        op=c.execute("SELECT * FROM payment_operations WHERE id=? AND workspace_id=? AND kind='manual'",(operation_id,ws['id'])).fetchone()
        if not op or type(cents) is not int or op['cents']!=cents:
            raise billing.StripeError('That payment does not match this workspace and amount. Request a fresh quote.')
        if op['status']=='succeeded': return {'payment_status':'succeeded','balance_cents':billing.balance(ws['id']),'operation_id':op['id']}
        if op['status']=='failed': raise billing.StripeError('That payment ended. Request a fresh quote.')
        if op['status']=='quoted':
            if op['expires']<time.time(): raise billing.StripeError('That quote expired. Reload the page for a new quote.')
            if c.execute("SELECT 1 FROM payment_operations WHERE workspace_id=? AND id!=? AND status IN ('pending','unknown')",(ws['id'],op['id'])).fetchone():
                raise billing.StripeError('Finish the previous payment before starting another.',ambiguous=True)
            customer=billing._ensure_customer(ws,by)
            payload={'mode':'payment','customer':customer,'payment_method_types[0]':'card',
                'managed_payments[enabled]':'false','line_items[0][quantity]':1,
                'line_items[0][price_data][currency]':'usd',
                'line_items[0][price_data][unit_amount]':cents,
                'line_items[0][price_data][product_data][name]':'Boat House hosting credit',
                'payment_intent_data[metadata][boathouse_operation]':op['id'],
                'payment_intent_data[metadata][workspace]':ws['slug'],
                'payment_intent_data[setup_future_usage]':'off_session',
                'metadata[boathouse_operation]':op['id'],
                'expires_at':int(time.time())+1800,
                'custom_text[submit][message]':'Adds prepaid hosting credit. Your card is saved for future payments. Auto-refill is off unless you enable it separately.',
                'success_url':f'https://{config.PLATFORM_DOMAIN}/account/{ws["slug"]}/payment-return?operation_id={op["id"]}',
                'cancel_url':f'https://{config.PLATFORM_DOMAIN}/account?ws={ws["slug"]}'}
            c.execute("UPDATE payment_operations SET status='pending',submitted=?,updated=?,customer=?,checkout_payload=? WHERE id=?",
                      (time.time(),time.time(),customer,json.dumps(payload),op['id']))
            op=c.execute('SELECT * FROM payment_operations WHERE id=?',(op['id'],)).fetchone()
        if not op['checkout_payload']:
            # Old off-session operations must settle under their original identity.
            bal=billing._execute_payment(ws,op)
            return {'payment_status':'succeeded','balance_cents':bal,'operation_id':op['id']}
        try:
            if op['checkout_id']:
                session=billing._stripe('GET','/checkout/sessions/'+op['checkout_id'])
            elif time.time()-op['submitted']>=23*3600:
                raise billing.StripeError('This payment needs operator reconciliation. A second charge will not be started.',ambiguous=True)
            else:
                session=billing._stripe('POST','/checkout/sessions',json.loads(op['checkout_payload']),idempotency_key='bh-checkout:'+op['id'])
            return _result(op,session)
        except billing.StripeError as e:
            with db.conn() as writer:
                writer.execute("UPDATE payment_operations SET status='unknown',error=?,updated=? WHERE id=? AND status NOT IN ('succeeded','failed')",(str(e),time.time(),op['id']))
            raise

def reconcile(ws,operation_id):
    with billing.payment_lock(ws['id']),db.conn() as c:
        op=c.execute('SELECT * FROM payment_operations WHERE id=? AND workspace_id=?',(operation_id,ws['id'])).fetchone()
        if not op or not op['checkout_id']: raise billing.StripeError('This checkout has not been confirmed. Open your account to check the payment.')
        return _result(op,billing._stripe('GET','/checkout/sessions/'+op['checkout_id']))

def reconcile_pending():
    with db.conn() as c:
        pending=c.execute("SELECT workspace_id,id FROM payment_operations WHERE checkout_id IS NOT NULL AND status IN ('pending','unknown') ORDER BY updated LIMIT 20").fetchall()
    for op in pending:
        try: reconcile(db.workspace_by_id(op['workspace_id']),op['id'])
        except billing.StripeError: pass
