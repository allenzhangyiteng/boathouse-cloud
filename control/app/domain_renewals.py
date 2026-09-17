"""Prepaid domain renewals. Registrar auto-renew is disabled on allocated domains.

A renewal has an immutable price, an expiry-cycle identity and a durable credit
reservation before any provider write. Ambiguous results retain that reservation
and can only replay the same operation inside the provider's replay window.
"""
import datetime as dt
import hashlib
import json
import time

from fastapi import HTTPException

from . import billing, config, db, domain_payments as purchases, mail, registrar

DAY = 86400
NOTICE_DAYS = 30
RENEW_DAYS = 14
MIN_NOTICE_DAYS = 7


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or dt.timezone.utc).timestamp()
    except (ValueError, TypeError):
        raise registrar.RegistrarError('The registrar returned an invalid expiry date.', 'BAD_EXPIRY')


def date(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%B %d, %Y')


def _owners(c, wid):
    return [r[0] for r in c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner'", (wid,))]


def _allocation(c, wid, domain):
    if domain == config.PLATFORM_DOMAIN or domain.endswith('.' + config.PLATFORM_DOMAIN):
        raise HTTPException(404, 'The platform domain is not a customer domain.')
    if not c.execute('SELECT 1 FROM domain_allocations WHERE domain=? AND workspace_id=?', (domain, wid)).fetchone():
        raise HTTPException(404, 'This organization does not own this domain through Boat House.')


def _notice(key, domain, wid, kind, subject, body, *, host=False):
    with db.conn() as c:
        if host:
            w = c.execute('SELECT id FROM workspaces WHERE slug=?', (config.HOST_WORKSPACE,)).fetchone()
            recipients = _owners(c, w['id']) if w else []
        else:
            recipients = _owners(c, wid)
        for email in recipients:
            c.execute('''INSERT OR IGNORE INTO domain_renewal_notices
                (notice_key,email,domain,workspace_id,kind,subject,body,created)
                VALUES (?,?,?,?,?,?,?,?)''', (key, email, domain, wid, kind, subject, body, time.time()))


def flush_notices():
    """Durable outbox; failed email is retried and never counts as advance notice."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM domain_renewal_notices WHERE sent_at IS NULL AND lease_until<=?', (time.time(),))]
    for n in rows:
        now = time.time()
        with db.conn() as c:
            changed = c.execute('''UPDATE domain_renewal_notices SET lease_until=?
                WHERE notice_key=? AND email=? AND sent_at IS NULL AND lease_until<=?''',
                (now + 300, n['notice_key'], n['email'], now)).rowcount
            # Do not send old queued notices to people who have since lost ownership.
            w = c.execute('SELECT id FROM workspaces WHERE slug=?', (config.HOST_WORKSPACE,)).fetchone() if n['kind']=='host' else None
            wid = w['id'] if w else n['workspace_id']
            recipient_still_owner = n['email'] in _owners(c, wid)
        if not changed or not recipient_still_owner:
            continue
        try:
            sent = mail.send(n['email'], n['subject'], n['body'])
        except Exception:
            sent = False
        with db.conn() as c:
            c.execute('UPDATE domain_renewal_notices SET sent_at=?,lease_until=? WHERE notice_key=? AND email=?',
                      (time.time() if sent else None, 0 if sent else time.time()+3600, n['notice_key'], n['email']))


def _link(wid):
    with db.conn() as c:
        ws = c.execute('SELECT slug FROM workspaces WHERE id=?', (wid,)).fetchone()
    return f'https://{config.PLATFORM_DOMAIN}/account/{ws["slug"]}/domains'


def _problem(domain, wid, message, *, customer=True):
    now = time.time()
    with db.conn() as c:
        c.execute('UPDATE domain_renewal_settings SET error=?,checked_at=? WHERE domain=?', (message, now, domain))
    # Weekly deduplication bounds alerts without hiding persistent failures.
    key = 'problem:' + domain + ':' + str(int(now // (7*DAY))) + ':' + hashlib.sha256(message.encode()).hexdigest()[:12]
    text = f'{domain}: {message}\n\nReview domain renewal and balance: {_link(wid)}\nNo additional card payment has been started.'
    if customer:
        _notice(key, domain, wid, 'problem', f'Action needed for {domain}', text)
    _notice('host:' + key, domain, wid, 'host', f'Domain renewal needs attention: {domain}', text, host=True)


def settings(wid):
    with db.conn() as c:
        rows = c.execute('''SELECT a.domain,s.enabled,s.max_cost_cents,s.expires_at,s.managed,s.error,s.checked_at
             FROM domain_allocations a LEFT JOIN domain_renewal_settings s ON s.domain=a.domain
             WHERE a.workspace_id=? ORDER BY a.domain''', (wid,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            op = c.execute("SELECT state,total_cents FROM domain_renewals WHERE domain=? AND state IN ('processing','uncertain') ORDER BY created DESC LIMIT 1", (r['domain'],)).fetchone()
            d['pending'] = dict(op) if op else None
            result.append(d)
        return result


def set_policy(wid, domain, enabled, maximum, by):
    if type(enabled) is not bool:
        raise HTTPException(422, 'enabled must be true or false.')
    if enabled and (type(maximum) is not int or not 1 <= maximum <= 100000):
        raise HTTPException(422, 'Set a renewal spending limit between $0.01 and $1,000, including the service fee.')
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        _allocation(c, wid, domain)
        row = c.execute('SELECT * FROM domain_renewal_settings WHERE domain=?', (domain,)).fetchone()
        if not row:
            raise HTTPException(409, 'Renewal setup is still being checked. Please try again shortly.')
        # Once submitted, its financial outcome must be reconciled, not cancelled.
        pending = c.execute("SELECT 1 FROM domain_renewals WHERE domain=? AND state IN ('processing','uncertain')", (domain,)).fetchone()
        if pending:
            raise HTTPException(409, 'A renewal is already being confirmed. Please wait for that result before changing its settings.')
        c.execute('UPDATE domain_renewal_settings SET enabled=?,max_cost_cents=?,approved_by=?,updated=? WHERE domain=?',
                  (int(enabled), maximum if enabled else row['max_cost_cents'], by, time.time(), domain))
        c.execute('COMMIT')
    db.audit(by, 'domain.renewal_policy', domain, {'enabled': enabled, 'max_cost_cents': maximum if enabled else row['max_cost_cents']}, wid)
    _notice('policy:' + db.new_id('n'), domain, wid, 'policy', f'Renewal settings updated: {domain}',
            f"Automatic renewal is {'on' if enabled else 'off'} for {domain}. " +
            (f'The maximum charge is ${maximum/100:.2f} per renewal, including our fee. ' if enabled else 'It will expire unless renewed or transferred before its expiry date. ') +
            f'Your current registration is unchanged.\n\n{_link(wid)}')
    return next(r for r in settings(wid) if r['domain']==domain)


def sync_domain(domain, wid):
    """Take over only an allocated domain. Never enable the registrar's card fallback."""
    with db.conn() as c:
        _allocation(c, wid, domain)
        current = c.execute('SELECT * FROM domain_renewal_settings WHERE domain=?', (domain,)).fetchone()
    fingerprint = purchases._fingerprint()
    sandbox = int(registrar.is_sandbox())
    if current and (current['provider_fingerprint'] != fingerprint or current['sandbox'] != sandbox):
        raise registrar.RegistrarError('The registrar connection changed. Support must verify domain ownership before renewal.', 'REGISTRAR_CHANGED')
    info = registrar.domain_info(domain)
    expiry = timestamp(info.get('expireDate'))
    if info.get('status') != 'ACTIVE' or str(info.get('autoRenew')) not in ('0', '1') or str(info.get('apiAccess')) != '1':
        raise registrar.RegistrarError('The registrar reports an inactive domain or unknown renewal setting. Contact support.', 'DOMAIN_INACTIVE')
    if not current:
        q = registrar.renewal_quote(domain)
        maximum = q['cost_cents'] + q['years'] * billing.PRICES['domain_margin']
        with db.conn() as c:
            c.execute('''INSERT OR IGNORE INTO domain_renewal_settings
                (domain,workspace_id,enabled,max_cost_cents,expires_at,provider_fingerprint,sandbox,approved_by,updated)
                VALUES (?,?,?,?,?,?,?,?,?)''', (domain,wid,int(str(info['autoRenew'])=='1'),maximum,expiry,fingerprint,sandbox,'existing registrar renewal preference',time.time()))
        _notice('enrolled:'+domain, domain, wid, 'policy', f'Domain renewal billing: {domain}',
                f"Boat House now manages renewal billing for {domain}. Automatic renewal is {'on' if str(info['autoRenew'])=='1' else 'off'}. "
                f'Your domain expires {date(expiry)}. The current renewal limit is ${maximum/100:.2f} for {q["years"]} year(s), including our $2 per year fee. '
                'We email you before renewal and attempt it 14 days before expiry, using only your Boat House credit. '
                'If the price exceeds your limit or your balance is short, we ask you to act. '
                f'No renewal or card charge has happened now. Manage or turn off renewal: {_link(wid)}')
    if str(info['autoRenew']) == '1':
        info = registrar.disable_auto_renew(domain)
        expiry = timestamp(info.get('expireDate'))
    with db.conn() as c:
        c.execute('UPDATE domain_renewal_settings SET managed=1,expires_at=?,checked_at=? WHERE domain=?', (expiry,time.time(),domain))
        return dict(c.execute('SELECT * FROM domain_renewal_settings WHERE domain=?', (domain,)).fetchone())


def _notice_key(row):
    return f'renewal:{row["domain"]}:{row["expires_at"]}:{row["total_cents"]}'


def _plan(policy, q):
    cost, years = q['cost_cents'], q['years']
    margin = billing.PRICES['domain_margin'] * years
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute("SELECT * FROM domain_renewals WHERE domain=? AND expires_at=? AND state NOT IN ('failed','cancelled')", (policy['domain'],policy['expires_at'])).fetchone()
        if old and (old['state'] != 'waiting' or (old['registrar_cents']==cost and old['years']==years)):
            return dict(old)
        if old:
            c.execute("UPDATE domain_renewals SET state='cancelled' WHERE id=?", (old['id'],))
        oid = db.new_id('dr')
        c.execute('''INSERT INTO domain_renewals (id,domain,workspace_id,expires_at,registrar_cents,margin_cents,
            total_cents,years,created,state,provider_fingerprint,sandbox) VALUES (?,?,?,?,?,?,?,?,?,'waiting',?,?)''',
            (oid,policy['domain'],policy['workspace_id'],policy['expires_at'],cost,margin,cost+margin,years,time.time(),policy['provider_fingerprint'],policy['sandbox']))
        c.execute('COMMIT')
        return dict(c.execute('SELECT * FROM domain_renewals WHERE id=?', (oid,)).fetchone())


def _reserve(row):
    now = time.time()
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        r = dict(c.execute('SELECT * FROM domain_renewals WHERE id=?', (row['id'],)).fetchone())
        _allocation(c, r['workspace_id'], r['domain'])
        if r['state'] in ('renewed','failed','cancelled'):
            return None
        if r['provider_fingerprint'] != purchases._fingerprint() or bool(r['sandbox']) != registrar.is_sandbox():
            raise registrar.RegistrarError('The registrar connection changed; the renewal needs review.', 'REGISTRAR_CHANGED')
        retry = r['state'] in ('processing','uncertain')
        if retry:
            if r['lease_until'] and r['lease_until'] > now:
                return None
            if not r['provider_result'] and now-r['started'] >= purchases.REPLAY_SECONDS:
                raise registrar.RegistrarError('A renewal result is still uncertain. Credit is reserved; support must reconcile it before any further renewal.', 'RECONCILIATION_REQUIRED')
        else:
            p = c.execute('SELECT * FROM domain_renewal_settings WHERE domain=?', (r['domain'],)).fetchone()
            if not p['managed'] or not p['enabled'] or p['expires_at'] != r['expires_at'] or r['expires_at'] <= now or r['expires_at']-now > RENEW_DAYS*DAY:
                return None
            if r['total_cents'] > p['max_cost_cents']:
                raise registrar.RegistrarError('The renewal price exceeds your approved limit. Review and update your limit to keep this domain.', 'PRICE_LIMIT')
            owners = _owners(c,r['workspace_id'])
            sent = {n['email']: n['sent_at'] for n in c.execute('SELECT email,sent_at FROM domain_renewal_notices WHERE notice_key=?', (_notice_key(r),))}
            if not owners or any(not sent.get(e) or now-sent[e] < MIN_NOTICE_DAYS*DAY for e in owners):
                return None
            bal = c.execute('SELECT balance_cents FROM workspaces WHERE id=?', (r['workspace_id'],)).fetchone()[0]
            if bal < r['total_cents']:
                raise registrar.RegistrarError(f"Your renewal costs ${r['total_cents']/100:.2f}, but your balance is ${bal/100:.2f}. Add credit before expiry to keep this domain.", 'INSUFFICIENT_FUNDS')
            after = bal-r['total_cents']
            c.execute('UPDATE workspaces SET balance_cents=? WHERE id=?', (after,r['workspace_id']))
            c.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?)',
                      (db.new_id('l'),r['workspace_id'],now,'pending_renewal',-r['total_cents'],after,f"Reserved for domain renewal {r['domain']}",'domain-renewal:'+r['id'],'domain-renewal'))
        c.execute("UPDATE domain_renewals SET state='processing',started=COALESCE(started,?),lease_until=? WHERE id=?", (now,now+purchases.LEASE_SECONDS,r['id']))
        c.execute('COMMIT')
        r['retrying_uncertain'] = retry
        return r


def _settle(row, result):
    with db.conn() as c:
        c.execute('UPDATE domain_renewals SET provider_result=? WHERE id=?', (json.dumps(result),row['id']))
    purchases._validate_purchase_result(row,result)
    expiry = timestamp(result.get('expirationDate'))
    if expiry <= row['expires_at']:
        raise registrar.RegistrarError('Renewal proof does not extend the registration. Support must reconcile it.', 'BAD_RENEWAL_PROOF', uncertain=True)
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT state FROM domain_renewals WHERE id=?', (row['id'],)).fetchone()
        if current['state']=='renewed':
            return
        _allocation(c,row['workspace_id'],row['domain'])
        c.execute("UPDATE ledger SET kind='charge',memo=? WHERE ref=?", (f"domain renewal {row['domain']} (registrar ${row['registrar_cents']/100:.2f} + ${row['margin_cents']/100:.2f})",'domain-renewal:'+row['id']))
        c.execute("UPDATE domain_renewals SET state='renewed',settled=?,lease_until=0,error=NULL WHERE id=?", (time.time(),row['id']))
        c.execute('UPDATE domain_renewal_settings SET expires_at=?,error=NULL WHERE domain=?', (expiry,row['domain']))
        c.execute('INSERT INTO audit VALUES (?,?,?,?,?,?)', (time.time(),row['workspace_id'],'domain-renewal','domain.renewed',row['domain'],json.dumps({'operation_id':row['id'],'total_cents':row['total_cents'],'expires_at':expiry})))
        c.execute('COMMIT')
    _notice('receipt:'+row['id'],row['domain'],row['workspace_id'],'receipt',f"Renewed: {row['domain']}",
            f"{row['domain']} is renewed until {date(expiry)}. We deducted ${row['total_cents']/100:.2f} from your Boat House credit "
            f"(${row['registrar_cents']/100:.2f} registrar + ${row['margin_cents']/100:.2f} service fee).\n\n{_link(row['workspace_id'])}")


def _release(row, error):
    with db.conn() as c:
        c.execute('BEGIN IMMEDIATE')
        r = c.execute('SELECT state FROM domain_renewals WHERE id=?', (row['id'],)).fetchone()
        if r['state'] not in ('processing','uncertain'):
            return
        bal = c.execute('SELECT balance_cents FROM workspaces WHERE id=?', (row['workspace_id'],)).fetchone()[0] + row['total_cents']
        c.execute('UPDATE workspaces SET balance_cents=? WHERE id=?', (bal,row['workspace_id']))
        c.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?)', (db.new_id('l'),row['workspace_id'],time.time(),'renewal_release',row['total_cents'],bal,f"Renewal rejected for {row['domain']}; reserved credit returned",'domain-renewal-release:'+row['id'],'domain-renewal'))
        c.execute("UPDATE domain_renewals SET state='failed',lease_until=0,error=? WHERE id=?", (str(error)[:400],row['id']))
        c.execute('COMMIT')


def execute(row):
    row = _reserve(row)
    if row is None:
        return
    try:
        result = json.loads(row['provider_result']) if row['provider_result'] else registrar.renew(row['domain'],row['registrar_cents'],False,idempotency_key=row['id'])
        if result.get('status') == 'PENDING':
            raise registrar.RegistrarError('The registrar is still confirming the renewal.', 'RENEWAL_PENDING', uncertain=True)
        _settle(row,result)
    except Exception as exc:
        known_failure = isinstance(exc,registrar.RegistrarError) and not exc.uncertain and not row['provider_result'] and (not row['retrying_uncertain'] or exc.replayed)
        # Invalid/missing proof after SUCCESS is ambiguous even if validation raised
        # a local error without the provider's uncertainty flag.
        if known_failure and 'result' not in locals():
            _release(row,exc)
        else:
            with db.conn() as c:
                c.execute("UPDATE domain_renewals SET state='uncertain',lease_until=0,error=? WHERE id=? AND state='processing'", (str(exc)[:400],row['id']))
        raise


def process_domain(domain, wid):
    with db.conn() as c:
        pending = c.execute("SELECT * FROM domain_renewals WHERE domain=? AND state IN ('processing','uncertain')", (domain,)).fetchone()
    if pending:
        execute(dict(pending))
        return
    p = sync_domain(domain,wid)
    remaining = p['expires_at']-time.time()
    if remaining > NOTICE_DAYS*DAY:
        with db.conn() as c:
            c.execute('UPDATE domain_renewal_settings SET error=NULL WHERE domain=?',(domain,))
        return
    if not p['enabled']:
        _notice(f'expiring:{domain}:{p["expires_at"]}:{1 if remaining <= DAY else 7 if remaining <= 7*DAY else 30}',domain,wid,'expiry',f'Renewal is off: {domain}',
                f'{domain} expires {date(p["expires_at"])}. Automatic renewal is off. Enable it or transfer your domain before expiry to keep it.\n\n{_link(wid)}')
        return
    if remaining <= 0:
        raise registrar.RegistrarError('This domain has reached its expiry date. Contact support immediately to check recovery; no automatic purchase will be attempted.', 'DOMAIN_EXPIRED')
    row = _plan(p,registrar.renewal_quote(domain))
    if row['state']=='renewed':
        return
    _notice(_notice_key(row),domain,wid,'advance',f'Upcoming renewal: {domain}',
            f"{domain} expires {date(p['expires_at'])}. We plan to renew it 14 days before expiry for {row['years']} year(s), "
            f"charging ${row['total_cents']/100:.2f} from your Boat House credit (${row['registrar_cents']/100:.2f} registrar + ${row['margin_cents']/100:.2f} service fee). "
            'We wait at least seven days after sending this price notice. Your card is not charged by this renewal; any separately enabled balance refill follows your refill settings. '
            'If your balance is short or the price exceeds your limit, renewal waits for you. To cancel, turn renewal off before it starts. '
            f'No credit has been deducted yet.\n\n{_link(wid)}')
    if row['total_cents'] > p['max_cost_cents']:
        raise registrar.RegistrarError(f"The renewal price is ${row['total_cents']/100:.2f}, above your ${p['max_cost_cents']/100:.2f} limit. Approve a higher limit to keep this domain.", 'PRICE_LIMIT')
    if remaining > RENEW_DAYS*DAY:
        return
    with db.conn() as c:
        owners = _owners(c,wid)
        sent = {n['email']:n['sent_at'] for n in c.execute('SELECT email,sent_at FROM domain_renewal_notices WHERE notice_key=?',(_notice_key(row),))}
    if not owners or any(not sent.get(e) for e in owners):
        raise registrar.RegistrarError('The advance renewal notice has not been sent successfully to every owner. Support has been alerted; no renewal can start until notice is sent.', 'NOTICE_NOT_SENT')
    dry = registrar.renew(domain,row['registrar_cents'],True)
    if (dry.get('dryRun') is not True or dry.get('wouldSucceed') is not True or dry.get('sufficientFunds') is not True or
            type(dry.get('cost')) is not int or dry['cost'] != row['registrar_cents']):
        raise registrar.RegistrarError('The registrar cannot complete this renewal yet. Boat House support has been alerted; no customer credit was spent.', 'PROVIDER_NOT_READY')
    execute(row)


def run_once():
    now = time.time()
    lease = now+1800
    with db.conn() as c:
        c.execute('INSERT OR IGNORE INTO domain_renewal_worker VALUES (1,0)')
        acquired = c.execute('UPDATE domain_renewal_worker SET lease_until=? WHERE id=1 AND lease_until<=?', (lease,now)).rowcount
    if not acquired:
        return {'busy':True}
    processed = errors = 0
    try:
        flush_notices()
        with db.conn() as c:
            allocated = [(r['domain'],r['workspace_id']) for r in c.execute('SELECT * FROM domain_allocations')]
        for domain,wid in allocated:
            with db.conn() as c:
                extended = time.time()+1800
                if not c.execute('UPDATE domain_renewal_worker SET lease_until=? WHERE id=1 AND lease_until=?',(extended,lease)).rowcount:
                    return {'lease_lost':True,'domains_checked':processed,'errors':errors}
                lease = extended
            try:
                process_domain(domain,wid)
            except Exception as exc:
                errors += 1
                message = str(exc) if isinstance(exc,registrar.RegistrarError) else 'Renewal checks could not complete. Support must review this domain before expiry.'
                _problem(domain,wid,message)
                db.audit('domain-renewal','domain.renewal_check_failed',domain,{'error_type':type(exc).__name__},wid)
            processed += 1
        if registrar.creds():
            try:
                balance = registrar.status().get('balance_cents')
                if type(balance) is int and balance < 2000:
                    _notice('registrar-low:'+str(int(now//(7*DAY))),'',None,'host','Porkbun credit is running low',
                            f'Porkbun has ${balance/100:.2f} available. Add registrar credit before customer domain purchases or renewals need it. '
                            'Customer Stripe receipts do not automatically fund Porkbun. This monitor does not charge a card.',host=True)
            except Exception:
                db.audit('domain-renewal','domain.registrar_balance_unavailable',None,None)
        flush_notices()
        return {'domains_checked':processed,'errors':errors}
    finally:
        with db.conn() as c:
            c.execute('UPDATE domain_renewal_worker SET lease_until=0 WHERE id=1 AND lease_until=?',(lease,))
