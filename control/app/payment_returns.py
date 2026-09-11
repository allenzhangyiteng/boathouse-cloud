"""Reconcile processor refunds/disputes; never issue refunds or transfers."""
import time
from urllib.parse import urlencode
from . import db, referrals


def _list(kind, provider_id):
    from . import billing
    result, cursor = [], None
    while True:
        params = {'payment_intent':provider_id,'limit':100}
        if cursor:
            params['starting_after'] = cursor
        page = billing._stripe('GET',f'/{kind}?'+urlencode(params))
        if not isinstance(page.get('data'),list):
            raise billing.StripeError('The processor did not return valid payment reconciliation.')
        result.extend(page['data'])
        if not page.get('has_more'):
            return result
        if not page['data'] or page['data'][-1].get('id')==cursor:
            raise billing.StripeError('Payment reconciliation pagination did not advance.')
        cursor = page['data'][-1]['id']


def reconcile(provider_id):
    from . import billing
    with db.conn() as c:
        op = c.execute("SELECT * FROM payment_operations WHERE provider_id=? AND status='succeeded'",(provider_id,)).fetchone()
    if not op:
        # A refund event may arrive before payment_intent.succeeded. Resolve
        # only an approved Boat House operation, then validate it under its lock.
        pi = billing._stripe('GET',f'/payment_intents/{provider_id}')
        opid = (pi.get('metadata') or {}).get('boathouse_operation')
        with db.conn() as c:
            op = c.execute('SELECT * FROM payment_operations WHERE id=?',(opid,)).fetchone()
        if not op:
            return {'ignored':True}
    # Serialize the fresh provider read and application; old concurrent events
    # cannot roll back a newer provider state. Never apply event-payload deltas.
    with billing.payment_lock(op['workspace_id']):
        pi = billing._stripe('GET',f'/payment_intents/{provider_id}')
        billing._validate_payment_identity(op,pi)
        if op['status'] != 'succeeded':
            billing._complete_payment(op,pi)
        refunds = _list('refunds',provider_id)
        disputes = _list('disputes',provider_id)
        for row in refunds+disputes:
            if row.get('payment_intent')!=provider_id or row.get('currency')!='usd' or type(row.get('amount')) is not int or row['amount']<0:
                raise billing.StripeError('A returned payment did not match the original transaction.')
        refunded = sum(r['amount'] for r in refunds if r.get('status')=='succeeded')
        pending = sum(r['amount'] for r in refunds if r.get('status') in ('pending','requires_action'))
        # Inquiry/warning statuses have not withdrawn the customer's funds.
        disputed = sum(r['amount'] for r in disputes if r.get('status') in ('needs_response','under_review','lost'))
        held = min(op['cents'],refunded+pending+disputed)
        with db.conn() as c, c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM payment_returns WHERE provider_id=?',(provider_id,)).fetchone()
            delta = held-(old['held_cents'] if old else 0)
            c.execute('INSERT INTO payment_returns VALUES(?,?,?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET '
                      'refunded_cents=excluded.refunded_cents,disputed_cents=excluded.disputed_cents,held_cents=excluded.held_cents,updated=excluded.updated',
                      (provider_id,op['workspace_id'],refunded,disputed,held,time.time()))
            if delta:
                bal = c.execute('SELECT balance_cents FROM workspaces WHERE id=?',(op['workspace_id'],)).fetchone()[0]-delta
                c.execute('UPDATE workspaces SET balance_cents=? WHERE id=?',(bal,op['workspace_id']))
                c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)',
                          (db.new_id('l'),op['workspace_id'],time.time(),'payment_return',-delta,bal,
                           'Processor refund/dispute reconciliation',db.new_id('return'),'stripe'))
            referrals.sync(c,op['workspace_id'])
        ws = db.workspace_by_id(op['workspace_id'])
        if ws['balance_cents']<=0 and not ws['paused']:
            billing.pause(ws,'payment refunded or disputed')
        elif delta<0 and ws['balance_cents']>0 and ws['paused']:
            billing.resume(ws)
        return {'reconciled':True,'held_cents':held,'balance_delta_cents':-delta}


def reconcile_partner(email):
    with db.conn() as c:
        ids = [r[0] for r in c.execute("SELECT p.provider_id FROM payment_operations p JOIN workspaces w ON w.id=p.workspace_id "
                                      "WHERE p.status='succeeded' AND p.provider_id IS NOT NULL AND w.referred_by=?",(email,))]
    for ident in ids:
        reconcile(ident)
    return len(ids)
