"""Partner landing, private dashboard, QR download, and operator payout queue."""
import re
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from . import auth, billing, config, db, front, pages, referrals
from .pages import _e, _money, _date

router = APIRouter()
PRIVATE = {'Cache-Control':'private, no-store', 'Vary':'Cookie'}
NAV = '<a href="/account">My apps</a><a href="/partners">Partners</a><a href="/logout">Sign out</a>'


def _html(title, body, private=True, nav=NAV):
    return HTMLResponse(pages.page(title,body,nav=nav,wide=True,
        description="Share Boat House. Earn 10% of referred customers' paid hosting for their lifetime." if not private else '',
        canonical=f'https://{config.PLATFORM_DOMAIN}/partners' if not private else ''),
        headers=PRIVATE if private else {'Cache-Control':'private, no-store','Vary':'Cookie'})


def _host(request):
    if not front._platform(request):
        raise HTTPException(404)


def _operator(request):
    _host(request)
    email = front._email(request)
    if not email:
        raise HTTPException(401, 'Sign in to continue.')
    if not any(m['slug']==config.HOST_WORKSPACE and m['my_role']=='owner' for m in auth.memberships(email)):
        raise HTTPException(403, 'Host workspace owners only.')
    return email


def _copy(text, label='Copy'):
    return f'<div class=copy><span>{_e(text)}</span><button type=button>{_e(label)}</button></div>'


@router.get('/partners')
def partners(request: Request):
    _host(request)
    email = front._email(request)
    intro = '''<p class=flow-progress>BOAT HOUSE PARTNERS</p>
<h1>Bring a customer.<br>Earn for their lifetime.</h1>
<p class=lead>Share the easiest way to put an app online and share it like a Google Doc.<br>You earn <strong>10% of their paid hosting and storage usage</strong> for as long as they keep using Boat House.</p>'''
    if not email:
        return _html('Boat House partners · 10% lifetime revenue share',intro+'''
<p><a class=btn href="/signup?partner=1">Get your free partner code</a> <a class="btn s" href="/login?next=%2Fpartners">Partner sign in</a></p>
<div class=cards><section class=card><h3>1. Get your code</h3><p>Create a free account. Your own link, QR, and referral code are ready in your dashboard.</p></section>
<section class=card><h3>2. Share it anywhere</h3><p>Use it in a client handoff, tutorial, newsletter, or a conversation. Customers scan your QR or use your code at signup.</p></section>
<section class=card><h3>3. Keep earning</h3><p>The customer stays linked to you across future apps and workspaces. No renewal or referral expiration.</p></section></div>
<h2>A reason for them to try it.</h2><p>Your customers get <strong>50% off organization hosting for their first 60 days</strong>. Then standard hosting is $10 per organization per month for up to five lightweight apps, with no per-person charge.</p>
<h2>Simple numbers.</h2><p>A customer using $10 of paid hosting earns you $1. Ten customers each using $10 earn you $10. During the half-price offer, $5 of hosting earns you $0.50. Extra paid storage also counts.</p>
<p>Joining is free. No hosting purchase, payment card, or app required. Boat House is open source under Apache 2.0; commissions apply to our managed hosting service.</p>
<p><small>Monthly cash payouts start at $10; smaller balances roll over. Free credit, unused deposits, domain purchases, taxes, refunds, and disputed payments are excluded. Transfers use a method agreed with you. <a href="/terms">Read the partner terms</a>.</small></p>''',private=False,nav='<a href="/">Product</a><a href="/demo">Demo</a><a href="/login?next=%2Fpartners">Sign in</a>')
    r = referrals.summary(email)
    stats = ''.join(f'<div><b>{_e(value)}</b><span>{label}</span></div>' for label,value in (
        ('Customers referred',r['customer_count']),('Earned to date',_money(r['earned_cents'])),
        ('Paid to you',_money(r['paid_cents'])),('Awaiting payout',_money(r['unpaid_cents']))))
    share = (f"Boat House puts apps online and makes them easy to share with a team. "
             f"My link gives you 50% off organization hosting for 60 days: {r['link']} "
             "I earn 10% of your paid hosting and storage usage if you join through it.")
    rows = ''.join(f"<tr><td>{_date(p['created'])}</td><td>{_money(p['cents'])}</td><td>{_e(p['status'].title())}</td><td>{_e(p['payout_ref'] or '—')}</td></tr>" for p in r['payout_history'])
    admin = '<p><a href="/partners/payouts">Manage partner payouts</a></p>' if any(m['slug']==config.HOST_WORKSPACE and m['my_role']=='owner' for m in auth.memberships(email)) else ''
    body = f'''<p class=flow-progress>YOUR PARTNER DASHBOARD</p><h1>Share once. Keep earning.</h1>
<p>Signed in as {_e(email)}. Your 10% share follows each referred customer for their lifetime.</p>
<div class=strip>{stats}</div>
<div class=cards style="grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))"><section class=card><h3>Your referral link</h3><p>Customers using this link arrive with your code filled in.</p>{_copy(r['link'],'Copy link')}
<h3>Your signup code</h3>{_copy(r['code'],'Copy code')}<small>They can also enter it manually when creating their account.</small></section>
<section class=card><h3>Your QR code</h3><img src="{_e(r['qr_url'])}" alt="Scan to sign up using your referral code" width=230 height=230 style="max-width:100%;height:auto;align-self:flex-start">
<a class="btn s" download="boathouse-partner-qr.png" href="{_e(r['qr_url'])}?download=1">Download QR code</a></section></div>
<h2>Ready to share.</h2><p>Use this as a starting point. The last sentence tells people you earn a commission.</p>{_copy(share,'Copy message')}
<h2>Your earnings.</h2><p>Earn 10% of paid hosting and storage usage, including future apps and workspaces owned by the same referred customer account. Unused deposits and free credit do not earn commission. Customers get half-price organization hosting for 60 days; after that, $10 in paid hosting earns you $1.</p>
<p>Payouts are reviewed monthly once your balance reaches $10. Smaller balances roll over. Refunds and disputes reduce earnings, including a carry-forward adjustment if a commission was already paid. Customer names, emails, and app data stay private.</p>
<p>To arrange a payout method, <a href="mailto:support@example.com?subject=Partner%20payout">contact support</a> from this account's email. Do not send bank details by email. We’ll arrange the transfer and record its reference here.</p>
<h2>Payout history</h2>{f'<table><thead><tr><th>Prepared</th><th>Amount</th><th>Status</th><th>Transfer reference</th></tr></thead><tbody>{rows}</tbody></table>' if rows else '<p>No payouts yet. Your earnings will appear above when a referred customer uses paid hosting.</p>'}
<p><small><a href="/terms">Partner terms</a> · Share your link or code with new customers before they create their account. The first valid referral stays with their account. Self-referrals are excluded.</small></p>{admin}'''
    return _html('Your Boat House partner dashboard',body)


@router.get('/partners/qr/{code}.png')
def qr(code: str, request: Request, download: bool = False):
    _host(request)
    code = code.upper()
    if not re.fullmatch(r'[A-Z0-9-]{3,32}',code) or not referrals.lookup(code):
        raise HTTPException(404, 'Unknown referral code.')
    headers = {'Cache-Control':'public, max-age=86400','X-Content-Type-Options':'nosniff'}
    if download:
        headers['Content-Disposition'] = 'attachment; filename="boathouse-partner-qr.png"'
    return Response(referrals.qr_png(code),media_type='image/png',headers=headers)


@router.get('/partners/payouts')
def payout_queue(request: Request, notice: str = ''):
    _operator(request)
    csrf = f'<input type=hidden name=csrf value="{_e(auth.csrf_token("partner-payout"))}">'
    rows = []
    for r in referrals.payouts():
        fields = csrf+f'<input type=hidden name=email value="{_e(r["email"])}">'
        p = r['pending']
        if p:
            fields += f'<input type=hidden name=payout_id value="{_e(p["id"])}"><input type=hidden name=cents value="{p["cents"]}">'
            actions = f'''<p>Prepared {_money(p['cents'])}. ID <code>{_e(p['id'])}</code>.</p>
<p>Transfer this exact amount through the partner’s agreed payout method. Then record the completed transfer below.</p>
<form method=post action="/partners/payouts/record">{fields}<label>Completed transfer reference<input name=ref required minlength=3 maxlength=160></label>
<label><span><input type=checkbox name=transferred value=yes required> I have already transferred {_money(p['cents'])} to this partner.</span></label><button>Record completed transfer</button></form>
<form method=post action="/partners/payouts/cancel">{fields}<button class=s>Cancel preparation</button></form>'''
            if p['cents'] > r['unpaid_cents']:
                actions = f'<p class=err>Earnings fell after preparation. Do not transfer the old amount. Cancel and prepare again.</p><form method=post action="/partners/payouts/cancel">{fields}<button class=s>Cancel preparation</button></form>'
        else:
            actions = f'<form method=post action="/partners/payouts/prepare">{fields}<button{" disabled" if r["available_cents"]<referrals.MIN_PAYOUT_CENTS else ""}>Review payments and prepare payout</button></form>'
        rows.append(f'<section class=card><h3>{_e(r["email"])}</h3><p>{_money(r["unpaid_cents"])} owed · {_money(r["paid_cents"])} previously paid</p>{actions}</section>')
    body = f'''<h1>Monthly partner payouts</h1><p>Prepare reconciles Stripe refunds and disputes, then reserves an exact amount. This page does not send money. Pay by the method agreed with the partner, then record the transfer. Amounts under $10 roll over.</p>
{f'<p class=notice role=status>{_e(notice)}</p>' if notice else ''}<div class=cards>{''.join(rows) or '<p>No partner payouts are due.</p>'}</div>'''
    return _html('Partner payout queue',body)


@router.post('/partners/payouts/{action}')
def payout_action(action: str, request: Request, email: str = Form(''), csrf: str = Form(''),
                  payout_id: str = Form(''), cents: int = Form(0), ref: str = Form(''), transferred: str = Form('')):
    by = _operator(request)
    if not auth.csrf_ok(csrf,'partner-payout'):
        raise HTTPException(403,'The form expired. Reload the payout page.')
    origin = request.headers.get('origin')
    if origin and origin != f'https://{config.PLATFORM_DOMAIN}':
        raise HTTPException(403,'Use the Boat House payout page.')
    try:
        if action=='prepare':
            referrals.prepare_payout(email,by)
            notice = 'Payout prepared. Transfer the displayed amount, then record its reference.'
        elif action=='record' and transferred=='yes':
            referrals.mark_paid(email,ref,by,payout_id,cents)
            notice = 'Completed transfer recorded.'
        elif action=='cancel':
            referrals.cancel_payout(payout_id,by)
            notice = 'Preparation cancelled. No money was sent.'
        else:
            raise referrals.ReferralError('Choose a valid action and confirm any completed transfer.')
    except (referrals.ReferralError,billing.StripeError) as e:
        notice = str(e)
    from urllib.parse import urlencode
    return RedirectResponse('/partners/payouts?'+urlencode({'notice':notice}),303)
