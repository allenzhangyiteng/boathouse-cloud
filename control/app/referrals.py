"""Lifetime attribution, exact paid-usage commissions, and manual payout records."""
from collections import deque
import io
import secrets
import time

from . import config, db

DISCOUNT_DAYS = 60
DISCOUNT = 0.5
SHARE = 0.10
MIN_PAYOUT_CENTS = 1000
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TERMS_VERSION = "2026-09-11"
TERMS = ("Earn 10% of referred customers' paid hosting and storage usage for as long as they use Boat House. "
         "Their first 60 days of app hosting are half price. Free credit, unused deposits, domain purchases, "
         "taxes, refunds, and disputed payments do not earn commission. Cash payouts are reviewed monthly once your balance reaches $10; smaller balances roll over. "
         "Transfers are made and recorded by Boat House, not automatically sent by this dashboard.")


class ReferralError(ValueError):
    pass


def code_for(email: str) -> str:
    email = email.lower().strip()
    with db.conn() as c, c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT code FROM referral_codes WHERE email=?", (email,)).fetchone()
        if row:
            return row['code']
        # A public code must not reveal part of the partner's email address.
        while True:
            code = "BH-" + "".join(secrets.choice(ALPHABET) for _ in range(10))
            if not c.execute("SELECT 1 FROM referral_codes WHERE code=?", (code,)).fetchone():
                break
        c.execute("INSERT INTO referral_codes VALUES(?,?,?)", (code,email,time.time()))
        return code


def lookup(code: str):
    with db.conn() as c, c:
        row = c.execute("SELECT email FROM referral_codes WHERE code=?", ((code or '').strip().upper(),)).fetchone()
    return row['email'] if row else None


def link_for(email: str) -> str:
    return f"https://{config.PLATFORM_DOMAIN}/signup?code={code_for(email)}"


def attribution(c, customer_email, code='', when=None):
    """First valid referral wins. Caller holds the signup transaction."""
    email = customer_email.strip().lower()
    old = c.execute("SELECT * FROM referral_customers WHERE email=?", (email,)).fetchone()
    if old:
        if code and code.strip().upper() != old['code']:
            raise ReferralError("This account already has a referral partner. Its original referral stays with the account.")
        return old
    if not code:
        return None
    r = c.execute("SELECT code,email FROM referral_codes WHERE code=?", (code.strip().upper(),)).fetchone()
    if not r:
        raise ReferralError("That referral code is not one of ours; check it, or leave it out.")
    if r['email'] == email:
        raise ReferralError("That is your own code; it works for other people.")
    if c.execute("SELECT 1 FROM workspaces w JOIN accounts a ON a.email=w.owner_email WHERE w.owner_email=? AND a.pw_hash IS NOT NULL", (email,)).fetchone():
        raise ReferralError("Referral codes are for new customer accounts. This account already owns apps or a workspace.")
    when = time.time() if when is None else when
    c.execute("INSERT INTO referral_customers VALUES(?,?,?,?,?)", (email,r['email'],r['code'],when,when+DISCOUNT_DAYS*86400))
    return c.execute("SELECT * FROM referral_customers WHERE email=?", (email,)).fetchone()


def attach(workspace_id: str, referrer_email: str, when=None):
    """Legacy host helper; never reassigns or restarts an attached discount."""
    when = time.time() if when is None else when
    with db.conn() as c, c:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute("SELECT referred_by FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if not old:
            raise ReferralError("Workspace not found.")
        if old['referred_by'] and old['referred_by'] != referrer_email:
            raise ReferralError("This workspace already has a referral partner.")
        if not old['referred_by']:
            c.execute("UPDATE workspaces SET referred_by=?,discount_until=? WHERE id=?", (referrer_email,when+DISCOUNT_DAYS*86400,workspace_id))


def tool_day_for(ws, base_cents: int, now=None) -> int:
    until = ws['discount_until'] if 'discount_until' in ws.keys() else None
    if until and (time.time() if now is None else now) < until:
        return base_cents // 2
    return base_cents


def _paid_usage(c, workspace_id):
    """Replay FIFO funding/spend, including debt funded by a later deposit.

    Provider-verified deposits count as cash. Returns reduce the original
    deposit before replay; skip their balance-adjustment rows to avoid double
    deductions. All other credit, including unverified legacy topups, is free.
    """
    boundary = c.execute("SELECT start_rowid FROM referral_program WHERE id=1").fetchone()[0]
    paid = {}
    for r in c.execute("SELECT p.provider_id,p.cents,COALESCE(r.held_cents,0) returned "
                       "FROM payment_operations p LEFT JOIN payment_returns r ON r.provider_id=p.provider_id "
                       "WHERE p.workspace_id=? AND p.status='succeeded' AND p.provider_id IS NOT NULL", (workspace_id,)):
        paid['stripe:'+r['provider_id']] = (r['cents'],max(0,r['cents']-r['returned']))
    funds, debts, earned = deque(), deque(), 0
    for r in c.execute("SELECT rowid,* FROM ledger WHERE workspace_id=? ORDER BY rowid", (workspace_id,)):
        amount = r['amount_cents']
        if r['kind'] == 'payment_return':
            continue
        if amount > 0:
            proof = paid.get(r['ref']) if r['kind'] == 'topup' else None
            is_cash = bool(proof and proof[0] == amount)
            remaining = proof[1] if is_cash else amount
            while debts and remaining:
                debt = debts[0]
                used = min(debt[0],remaining)
                if is_cash and debt[1]:
                    earned += used
                remaining -= used
                debt[0] -= used
                if not debt[0]:
                    debts.popleft()
            if remaining:
                funds.append([remaining,is_cash])
        elif amount < 0:
            remaining = -amount
            eligible = r['kind']=='charge' and (r['ref'] or '').startswith(('meter:', 'organization-meter:')) and r['rowid']>boundary
            while funds and remaining:
                fund = funds[0]
                used = min(fund[0],remaining)
                if fund[1] and eligible:
                    earned += used
                remaining -= used
                fund[0] -= used
                if not fund[0]:
                    funds.popleft()
            if remaining:
                debts.append([remaining,eligible])
    return earned


def sync(c, workspace_id):
    """Reconcile inside the caller's ledger transaction. Carry tenths of cents."""
    ws = c.execute("SELECT referred_by FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    if not ws or not ws['referred_by']:
        return 0
    email = ws['referred_by']
    c.execute("INSERT OR IGNORE INTO referral_revenue(workspace_id,referrer_email) VALUES(?,?)", (workspace_id,email))
    old = c.execute("SELECT * FROM referral_revenue WHERE workspace_id=?", (workspace_id,)).fetchone()
    basis = _paid_usage(c,workspace_id)
    if old['basis_cents']==basis:
        return 0
    total = c.execute("SELECT COALESCE(SUM(basis_cents),0) FROM referral_revenue WHERE referrer_email=?", (email,)).fetchone()[0]
    delta = (total-old['basis_cents']+basis)//10-total//10
    revision = old['revision']+1
    c.execute("UPDATE referral_revenue SET basis_cents=?,revision=? WHERE workspace_id=?", (basis,revision,workspace_id))
    if delta:
        c.execute("INSERT INTO referral_earnings(id,referrer_email,workspace_id,cents,ledger_ref,created) VALUES(?,?,?,?,?,?)",
                  (db.new_id('re'),email,workspace_id,delta,f"partner:{workspace_id}:{revision}",time.time()))
    return delta


def earn(ws, charged_cents: int, ledger_ref: str):
    """Legacy callers cannot invent earnings: reconcile recorded paid usage."""
    with db.conn() as c, c:
        c.execute("BEGIN IMMEDIATE")
        return sync(c,ws['id'])


def _totals(c,email):
    r = c.execute("SELECT COALESCE(SUM(cents),0) earned,COALESCE(SUM(CASE WHEN paid_at IS NOT NULL THEN cents ELSE 0 END),0) legacy_paid "
                  "FROM referral_earnings WHERE referrer_email=?", (email,)).fetchone()
    paid = r['legacy_paid']+c.execute("SELECT COALESCE(SUM(cents),0) FROM referral_payouts WHERE email=? AND status='paid'",(email,)).fetchone()[0]
    prepared = c.execute("SELECT COALESCE(SUM(cents),0) FROM referral_payouts WHERE email=? AND status='prepared'",(email,)).fetchone()[0]
    return {'earned_cents':r['earned'],'paid_cents':paid,'unpaid_cents':r['earned']-paid,
            'prepared_cents':prepared,'available_cents':max(0,r['earned']-paid-prepared)}


def summary(email: str) -> dict:
    email = email.lower().strip()
    code = code_for(email)
    with db.conn() as c, c:
        c.execute("BEGIN IMMEDIATE")
        referred = c.execute("SELECT id,created,discount_until,owner_email FROM workspaces WHERE referred_by=? ORDER BY created,id",(email,)).fetchall()
        for w in referred:
            sync(c,w['id'])
        totals = _totals(c,email)
        out = []
        for i,w in enumerate(referred,1):
            e = c.execute("SELECT COALESCE(SUM(cents),0) FROM referral_earnings WHERE workspace_id=? AND referrer_email=?",(w['id'],email)).fetchone()[0]
            basis = c.execute("SELECT basis_cents FROM referral_revenue WHERE workspace_id=?",(w['id'],)).fetchone()
            # Never reveal private customer names, email, apps, or access.
            out.append({'workspace':f'Customer workspace {i}','name':f'Customer workspace {i}','since':w['created'],
                        'discount_until':w['discount_until'],'earned_cents':e,'paid_usage_cents':basis[0] if basis else 0})
        history = [dict(r) for r in c.execute("SELECT id,cents,status,created,paid_at,payout_ref FROM referral_payouts WHERE email=? ORDER BY created DESC",(email,))]
        basis = c.execute("SELECT COALESCE(SUM(basis_cents),0) FROM referral_revenue WHERE referrer_email=?",(email,)).fetchone()[0]
    return {'code':code,'link':f'https://{config.PLATFORM_DOMAIN}/signup?code={code}',
            'qr_url':f'https://{config.PLATFORM_DOMAIN}/partners/qr/{code}.png','dashboard':f'https://{config.PLATFORM_DOMAIN}/partners',
            'referred':out,'customer_count':len({w['owner_email'] or w['id'] for w in referred}),
            'paying_workspace_count':sum(x['paid_usage_cents']>0 for x in out),'paid_usage_cents':basis,
            'fractional_cent_tenths':basis%10,'payout_history':history,**totals,'terms':TERMS,'terms_version':TERMS_VERSION}


def qr_png(code):
    code = (code or '').strip().upper()
    if not lookup(code):
        raise ReferralError('Unknown referral code.')
    import qrcode
    from qrcode.image.pure import PyPNGImage
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=10,border=4)
    qr.add_data(f'https://{config.PLATFORM_DOMAIN}/signup?code={code}')
    qr.make(fit=True)
    out = io.BytesIO()
    qr.make_image(image_factory=PyPNGImage).save(out)
    return out.getvalue()


def payouts() -> list[dict]:
    with db.conn() as c, c:
        emails = [r[0] for r in c.execute("SELECT DISTINCT referrer_email FROM referral_earnings UNION SELECT DISTINCT referred_by FROM workspaces WHERE referred_by IS NOT NULL")]
    result = []
    for email in emails:
        s = summary(email)
        if s['unpaid_cents'] or s['prepared_cents']:
            result.append({'email':email,'workspaces':len(s['referred']),**{k:s[k] for k in ('earned_cents','paid_cents','unpaid_cents','prepared_cents','available_cents')},
                           'pending':next((p for p in s['payout_history'] if p['status']=='prepared'),None)})
    return sorted(result,key=lambda r:-r['unpaid_cents'])


def prepare_payout(email,by):
    email = email.strip().lower()
    with db.conn() as c:
        if not c.execute('SELECT 1 FROM referral_codes WHERE email=?',(email,)).fetchone():
            raise ReferralError('No partner account found for this email.')
    from . import payment_returns
    payment_returns.reconcile_partner(email)
    summary(email)
    with db.conn() as c, c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute("SELECT * FROM referral_payouts WHERE email=? AND status='prepared'",(email,)).fetchone()
        if old:
            if old['cents'] > _totals(c,email)['unpaid_cents']:
                raise ReferralError('Earnings fell after this payout was prepared. Cancel its preparation and review the new amount before transferring money.')
            return dict(old)
        cents = _totals(c,email)['available_cents']
        if cents < MIN_PAYOUT_CENTS:
            raise ReferralError('Monthly payouts start at $10; smaller balances roll over.')
        ident = db.new_id('rp')
        c.execute("INSERT INTO referral_payouts(id,email,cents,status,created,created_by) VALUES(?,?,?,'prepared',?,?)",(ident,email,cents,time.time(),by))
        return dict(c.execute('SELECT * FROM referral_payouts WHERE id=?',(ident,)).fetchone())


def mark_paid(email,payout_ref,by,payout_id=None,cents=None):
    """Record an actual completed transfer. This never sends money."""
    email = email.strip().lower()
    payout_ref = (payout_ref or '').strip()
    if not isinstance(payout_id,str) or not 1<=len(payout_id)<=80 or type(cents) is not int or cents<=0 or not 3<=len(payout_ref)<=160:
        raise ReferralError('Prepare a payout first, then supply its payout_id, exact cents, and the completed transfer reference.')
    with db.conn() as c, c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM referral_payouts WHERE id=? AND email=?',(payout_id,email)).fetchone()
        if not row or row['cents']!=cents or row['status']=='cancelled':
            raise ReferralError('The payout identity, amount, or status does not match.')
        duplicate = c.execute('SELECT id FROM referral_payouts WHERE payout_ref=?',(payout_ref,)).fetchone()
        if duplicate and duplicate['id']!=payout_id:
            raise ReferralError('That transfer reference already belongs to another payout.')
        if row['status']=='paid':
            if row['payout_ref']!=payout_ref:
                raise ReferralError('This payout was already recorded with a different transfer reference.')
            return cents
        c.execute("UPDATE referral_payouts SET status='paid',paid_at=?,paid_by=?,payout_ref=? WHERE id=?",(time.time(),by,payout_ref,payout_id))
    db.audit(by,'referral.paid',email,{'cents':cents,'ref':payout_ref,'payout_id':payout_id})
    return cents


def cancel_payout(payout_id,by):
    with db.conn() as c, c:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT * FROM referral_payouts WHERE id=?',(payout_id,)).fetchone()
        if not r or r['status']=='paid':
            raise ReferralError('Only an unpaid prepared payout can be cancelled.')
        c.execute("UPDATE referral_payouts SET status='cancelled' WHERE id=?",(payout_id,))
    db.audit(by,'referral.payout_cancelled',payout_id)
