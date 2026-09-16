"""Money. A workspace holds prepaid credit in cents. Running tools and storage
are metered once a day; domains are charged at purchase. A card on file
(Stripe) refills the balance up to a monthly cap. At zero the workspace pauses:
tools stop, data stays. Every purchase is quoted first and only happens on an
explicit confirm."""
import hashlib
import hmac
import json
import time
import datetime as dt
import calendar
import fcntl
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

import httpx
import psycopg
from docker.errors import NotFound

from . import auth, config, db, deploy, referrals

# ---- the price sheet (cents) --------------------------------------------------
PRICES = {
    "organization_month": 1000,
    "tool_month": 1000,         # deprecated compatibility alias; never multiply by tool count
    "storage_gb_month": 25,     # per GB above the first GB, database + files, metered daily
    "domain_margin": 200,       # per domain per year on top of registrar cost
    "included_gb": 1,
}
TOOL_DAY = PRICES["tool_month"] // 30                        # approximate display rate; never used for metering
PAUSE_AT = 0
REFILL_BELOW = 500                                           # try the card when under $5


def monthly_rate(ws, date: dt.date | None = None) -> int:
    date = date or dt.datetime.now(dt.timezone.utc).date()
    stamp = dt.datetime.combine(date, dt.time(), tzinfo=dt.timezone.utc).timestamp()
    return referrals.tool_day_for(ws, PRICES["organization_month"], now=stamp)


def daily_rate(ws, today: str | None = None) -> int:
    """Deterministic cents per UTC date; any full calendar month sums exactly.

    Rounding is distributed across days instead of multiplying a rounded daily
    rate. Past ledger entries are never retroactively changed during rollout.
    """
    date = dt.date.fromisoformat(today) if today else dt.datetime.now(dt.timezone.utc).date()
    days = calendar.monthrange(date.year, date.month)[1]
    month = monthly_rate(ws, date)
    return (date.day * month // days) - ((date.day - 1) * month // days)


def plan_limits() -> dict:
    return {"name": "Organization", "included_tools": config.PLAN_TOOLS,
            "shared_memory_bytes": config.PLAN_MEMORY_BYTES,
            "shared_cpu_cores": config.PLAN_CPU_PERCENT / 100,
            "included_storage_bytes_per_tool": 1024**3,
            "definition": "Small websites, forms, trackers, calculators and dashboards that respond to everyday use. Up to five tools share 512 MB of running memory and half a CPU core; each tool includes 1 GB of saved data. Video processing, AI model hosting, large imports and continuously busy jobs need a larger plan.",
            "on_limit": "No automatic upgrade or surprise charge. Computing is capped; full storage refuses new writes and pauses the affected tool while keeping its data. Ask your agent to reduce usage or contact support for more capacity."}


def price_sheet() -> dict:
    return {**PRICES, "billing_unit": "organization", "included_tools": config.PLAN_TOOLS,
            "organization_day": TOOL_DAY, "tool_day": TOOL_DAY, "currency": "usd",
            "billing_period": "UTC calendar month", "tool_day_is_estimate": True,
            "plan": plan_limits(),
            "words": ["$10 per organization for a full calendar month, including up to five lightweight tools; one daily charge while any tool is running, not a charge for each tool",
                      "A static website counts as one tool. Stopped tools still count toward the five-tool allowance. No hosting charge while every tool is stopped; today's charge is not reversed.",
                      plan_limits()["definition"], plan_limits()["on_limit"],
                      "Each tool includes 1 GB of database and saved files. More storage requires approval, then $0.25 per used extra GB per month. Deleted tools with retained data still use storage capacity.",
                      "Domains are registrar cost plus $2 per year, quoted separately. External AI and email services are separate.",
                      "At zero balance tools pause; nothing is deleted. No paid resource upgrade is automatic.",
                      "A referral code makes organization hosting half price for the first 60 days.",
                      "Partners earn 10% of referred customers' paid hosting and storage for their lifetime; monthly payouts, $10 minimum."]}


# ---- ledger --------------------------------------------------------------------

class InsufficientFunds(Exception):
    pass


def balance(workspace_id: str) -> int:
    with db.conn() as c:
        r = c.execute("SELECT balance_cents FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return int(r["balance_cents"] or 0) if r else 0


def post(workspace_id: str, kind: str, amount_cents: int, memo: str, ref: str | None = None, by: str | None = None) -> int:
    """Append a ledger line and move the balance. Positive adds credit, negative spends.
    A ref makes the line idempotent (a repeated ref is ignored). Returns balance after."""
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            if ref and c.execute("SELECT 1 FROM ledger WHERE ref=?", (ref,)).fetchone():
                c.execute("COMMIT")
                return balance(workspace_id)
            cur = int(c.execute("SELECT balance_cents FROM workspaces WHERE id=?", (workspace_id,)).fetchone()["balance_cents"] or 0)
            new = cur + amount_cents
            c.execute("UPDATE workspaces SET balance_cents=? WHERE id=?", (new, workspace_id))
            c.execute("INSERT INTO ledger (id, workspace_id, ts, kind, amount_cents, balance_after, memo, ref, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                      (db.new_id("l"), workspace_id, time.time(), kind, amount_cents, new, memo, ref, by))
            referrals.sync(c, workspace_id)
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return new


def require(workspace_id: str, cents: int, what: str):
    if balance(workspace_id) < cents:
        raise InsufficientFunds(f"{what} costs ${cents/100:.2f} but the balance is ${balance(workspace_id)/100:.2f}; top up first (bh billing topup)")


def ledger(workspace_id: str, limit: int = 50) -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT ts, kind, amount_cents, balance_after, memo, ref, created_by FROM ledger WHERE workspace_id=? ORDER BY ts DESC LIMIT ?",
                                           (workspace_id, limit))]


# ---- metering ---------------------------------------------------------------------

def _storage_gb(tool) -> float:
    """Database plus volume, in GiB. Failed measurements must not silently bill zero."""
    if getattr(config, "RESOURCE_GUARD", False):
        from . import resources
        return resources.measure(tool)["storage_bytes"] / (1024 ** 3)
    total = 0
    try:
        with psycopg.connect(host=config.PG_HOST, user="postgres", password=config.PG_ADMIN_PASSWORD, dbname="postgres", autocommit=True) as pg:
            r = pg.execute("SELECT pg_database_size(%s)", (deploy.db_name(tool),)).fetchone()
            total += int(r[0] or 0)
    except Exception as e:
        raise RuntimeError("Database storage could not be measured; this usage charge will retry.") from e
    try:
        c = deploy.client()
        out = c.containers.run("alpine", ["du", "-sk", "/v"], remove=True, volumes={deploy.vol_name(tool): {"bind": "/v", "mode": "ro"}})
        total += int(out.decode().split()[0]) * 1024
    except Exception as e:
        raise RuntimeError("File storage could not be measured; this usage charge will retry.") from e
    return total / (1024 ** 3)


def meter_once(today: str | None = None) -> list[dict]:
    """One organization debit per UTC day, irrespective of its active tool count.

    The storage measurements and accruals commit with the debit. An old per-tool
    debit on the rollout date makes that whole organization day already paid;
    historical entries and balances are never rewritten.
    """
    today = today or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    date = dt.date.fromisoformat(today)
    denominator = 1024**3 * calendar.monthrange(date.year, date.month)[1]
    lines = []
    with db.conn() as c:
        workspaces = c.execute("SELECT * FROM workspaces").fetchall()
    for ws in workspaces:
        with db.conn() as c:
            tools = c.execute("SELECT * FROM tools WHERE workspace_id=?", (ws["id"],)).fetchall()
        running = [t for t in tools if deploy.status(t)["state"] == "running"]
        if not running:
            _settle(ws)
            continue
        ref = f"organization-meter:{ws['id']}:{today}"
        paid_sql = "SELECT 1 FROM ledger WHERE ref=? OR (workspace_id=? AND kind='charge' AND memo LIKE ?)"
        paid_args = (ref, ws['id'], f"%: running day {today}%")
        with db.conn() as c:
            if c.execute(paid_sql, paid_args).fetchone():
                _settle(ws)
                continue
        try:
            measured = [(t, _storage_gb(t)) for t in running]
        except Exception:
            db.audit("meter", "billing.measurement_failed", None, {"date": today}, ws['id'])
            continue
        with db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute(paid_sql, paid_args).fetchone():
                c.execute("COMMIT")
                continue
            storage = 0
            for t, gb in measured:
                extra = max(0, round(gb * 1024**3) - PRICES['included_gb'] * 1024**3)
                key = (ws['id'], t['resource_key'], today[:7])
                c.execute('INSERT OR IGNORE INTO storage_accrual(workspace_id,resource_key,month) VALUES(?,?,?)', key)
                accrued = c.execute('SELECT byte_cent_days,charged_cents FROM storage_accrual WHERE workspace_id=? AND resource_key=? AND month=?', key).fetchone()
                numerator = accrued['byte_cent_days'] + extra * PRICES['storage_gb_month']
                total = numerator // denominator
                storage += total - accrued['charged_cents']
                c.execute('UPDATE storage_accrual SET byte_cent_days=?,charged_cents=? WHERE workspace_id=? AND resource_key=? AND month=?', (numerator,total,*key))
            amount = daily_rate(ws, today) + storage
            bal = c.execute('SELECT balance_cents FROM workspaces WHERE id=?',(ws['id'],)).fetchone()[0] - amount
            memo = f"Organization: running day {today}; {len(running)} tools included"
            if monthly_rate(ws,date) < PRICES['organization_month']: memo += " (half price)"
            if storage: memo += f"; extra storage {storage} cents"
            c.execute('UPDATE workspaces SET balance_cents=? WHERE id=?',(bal,ws['id']))
            c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?)',(db.new_id('l'),ws['id'],time.time(),'charge',-amount,bal,memo,ref,'meter'))
            referrals.sync(c, ws['id'])
            c.execute('COMMIT')
        lines.append({'workspace':ws['slug'],'running_tools':len(running),'cents':amount,'storage_cents':storage})
        _settle(ws)
    return lines


def _settle(ws):
    """After charging: refill from the card if allowed, else pause at zero. Resume if credit came back."""
    bal = balance(ws["id"])
    if bal < REFILL_BELOW and ws["stripe_pm"] and ws["autorefill_cents"]:
        try:
            auto_refill(ws)
            bal = balance(ws["id"])
        except Exception as e:  # noqa: BLE001
            db.audit("meter", "billing.refill_failed", ws["slug"], {"error": str(e)[:200]}, ws["id"])
    if bal <= PAUSE_AT and not ws["paused"]:
        pause(ws, "balance reached zero")
    elif bal > PAUSE_AT and ws["paused"]:
        resume(ws)


def pause(ws, why: str):
    with db.conn() as c:
        tools = c.execute("SELECT * FROM tools WHERE workspace_id=?", (ws["id"],)).fetchall()
        c.execute("UPDATE workspaces SET paused=1 WHERE id=?", (ws["id"],))
    for t in tools:
        deploy.stop(t)
    db.audit("billing", "workspace.paused", ws["slug"], {"why": why}, ws["id"])


def resume(ws):
    with db.conn() as c:
        tools = c.execute("SELECT * FROM tools WHERE workspace_id=?", (ws["id"],)).fetchall()
        c.execute("UPDATE workspaces SET paused=0 WHERE id=?", (ws["id"],))
    for t in tools:
        if config.RESOURCE_GUARD:
            from . import resources
            if resources.blocked(t):
                continue
        try:
            ctr = deploy.client().containers.get(deploy.ctr_name(t))
            if ctr.status != "running":
                if config.RESOURCE_GUARD:
                    with resources.host_operation():
                        resources.admission(t)
                        ctr.start()
                else: ctr.start()
        except NotFound:
            pass
        except Exception:  # noqa: BLE001
            pass
    db.audit("billing", "workspace.resumed", ws["slug"], None, ws["id"])


def is_paused(workspace_id: str) -> bool:
    with db.conn() as c:
        r = c.execute("SELECT paused FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return bool(r and r["paused"])


# ---- Stripe (raw HTTP; keys live encrypted in settings) -------------------------------

STRIPE = "https://api.stripe.com/v1"


class StripeError(Exception):
    def __init__(self, message, *, ambiguous=False, payment_intent=None):
        super().__init__(message)
        self.ambiguous = ambiguous
        self.payment_intent = payment_intent


def _setting(name: str) -> str | None:
    with db.conn() as c:
        r = c.execute("SELECT value_enc FROM settings WHERE name=?", (name,)).fetchone()
    return auth.decrypt(r["value_enc"]) if r else None


def set_stripe_keys(secret: str, webhook_secret: str, by: str | None):
    with db.conn() as c:
        for name, val in (("stripe.secret", secret.strip()), ("stripe.webhook", webhook_secret.strip())):
            c.execute("""INSERT INTO settings VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET value_enc=excluded.value_enc,
                         updated=excluded.updated, updated_by=excluded.updated_by""", (name, auth.encrypt(val), time.time(), by))


def stripe_ready() -> bool:
    return bool(_setting("stripe.secret"))


def _stripe(method: str, path: str, data: dict | None = None, *, idempotency_key: str | None = None) -> dict:
    key = _setting("stripe.secret")
    if not key:
        raise StripeError("no card processor is connected yet (host: bh billing stripe-keys)")
    try:
        with httpx.Client(timeout=30, auth=(key, "")) as h:
            r = h.request(method, STRIPE + path, data=data or None,
                          headers={"Idempotency-Key": idempotency_key or uuid.uuid4().hex} if method == "POST" else None)
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise StripeError("The card processor has not confirmed the result yet. Retry this same payment; do not start another top-up.", ambiguous=True) from e
    if r.status_code >= 400:
        error = body.get("error", {})
        raise StripeError(error.get("message", f"stripe {r.status_code}"),
                          ambiguous=r.status_code >= 500 or r.status_code in (409, 429),
                          payment_intent=error.get("payment_intent"))
    return body


def _ensure_customer(ws, email: str) -> str:
    """The workspace's Stripe customer, created if there is none or if Stripe no longer knows the stored one
    (a customer made in test mode does not exist once the keys are live; the saved card goes with it)."""
    customer = ws["stripe_customer"]
    if customer:
        try:
            if not _stripe("GET", f"/customers/{customer}").get("deleted"):
                return customer
        except StripeError as e:
            if "No such customer" not in str(e):
                raise
    cust = _stripe("POST", "/customers", {"email": email, "name": ws["name"], "metadata[workspace]": ws["slug"]})
    with db.conn() as c:
        c.execute("UPDATE workspaces SET stripe_customer=?, stripe_pm=NULL WHERE id=?", (cust["id"], ws["id"]))
    return cust["id"]


def card_link(ws, return_url: str, email: str) -> str:
    """A one-time page where the owner types the card. The only browser step in Boathouse."""
    customer = _ensure_customer(ws, email)
    sess = _stripe("POST", "/checkout/sessions", {
        "mode": "setup", "customer": customer, "payment_method_types[0]": "card",
        "managed_payments[enabled]": "false",   # accounts with Managed Payments on by default refuse setup mode otherwise
        "success_url": return_url + ("&" if "?" in return_url else "?") + "card=saved",
        "cancel_url": return_url + ("&" if "?" in return_url else "?") + "card=cancelled",
        "metadata[workspace]": ws["slug"]})
    return sess["url"]


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Stripe signs webhooks: t=<ts>,v1=<hmac>. Verify, then record the saved card."""
    secret = _setting("stripe.webhook")
    if not secret:
        raise StripeError("webhook secret not set")
    parts = [p.split("=", 1) for p in sig_header.split(",") if "=" in p]
    stamp = next((v for k, v in parts if k == "t"), "0")
    signed = f"{stamp}.".encode() + payload
    expect = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    try:
        fresh = abs(time.time() - int(stamp)) <= 600
    except ValueError:
        fresh = False
    if not fresh or not any(hmac.compare_digest(expect, value) for key, value in parts if key == "v1"):
        raise StripeError("bad webhook signature")
    event = json.loads(payload)
    obj = event.get("data", {}).get("object", {})
    if event.get('type') in ('charge.refunded','refund.created','refund.updated','refund.failed',
                              'charge.dispute.created','charge.dispute.updated','charge.dispute.closed',
                              'charge.dispute.funds_withdrawn','charge.dispute.funds_reinstated'):
        from . import payment_returns
        provider_id = obj.get('payment_intent')
        if not provider_id and obj.get('charge'):
            charge = _stripe('GET',f"/charges/{obj['charge']}")
            provider_id = charge.get('payment_intent')
        if provider_id:
            return payment_returns.reconcile(provider_id)
    if event.get("type") == "payment_intent.succeeded":
        opid = (obj.get("metadata") or {}).get("boathouse_operation")
        with db.conn() as c:
            op = c.execute("SELECT * FROM payment_operations WHERE id=?", (opid,)).fetchone()
        if op:
            bal = _complete_payment(op, obj)
            ws = db.workspace_by_id(op["workspace_id"])
            if ws["paused"] and bal > PAUSE_AT:
                resume(ws)
            return {"credited": True, "operation_id": opid}
    if event.get("type") == "checkout.session.completed" and obj.get("mode") == "setup":
        si = _stripe("GET", f"/setup_intents/{obj['setup_intent']}")
        pm, customer = si.get("payment_method"), obj.get("customer")
        with db.conn() as c:
            ws = c.execute("SELECT * FROM workspaces WHERE stripe_customer=?", (customer,)).fetchone()
            if ws and pm:
                c.execute("UPDATE workspaces SET stripe_pm=? WHERE id=?", (pm, ws["id"]))
                db.audit("stripe", "billing.card_saved", ws["slug"], None, ws["id"])
                return {"saved": True, "workspace": ws["slug"]}
    return {"ignored": event.get("type")}


@contextmanager
def payment_lock(workspace_id):
    """A process-safe lock for submission, quote creation and refill settings."""
    folder = config.STATE_DIR / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(workspace_id.encode()).hexdigest()
    with (folder / f"payment-{name}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _new_payment(c, ws, cents, by, kind="manual", month=None):
    now = time.time()
    opid = db.new_id("pay")
    c.execute("""INSERT INTO payment_operations
                 (id,workspace_id,kind,cents,status,created,updated,expires,created_by,month)
                 VALUES (?,?,?,?,'quoted',?,?,?,?,?)""",
              (opid, ws["id"], kind, cents, now, now, now + 900, by, month))
    return c.execute("SELECT * FROM payment_operations WHERE id=?", (opid,)).fetchone()


def quote_topup(ws, cents, by, operation_id=None):
    if type(cents) is not int or not 500 <= cents <= 100000:
        raise StripeError("Top up between $5.00 and $1,000.00, using whole cents.")
    with payment_lock(ws["id"]), db.conn() as c:
        if operation_id:
            op = c.execute("SELECT * FROM payment_operations WHERE id=? AND workspace_id=? AND kind='manual'",
                           (operation_id, ws["id"])).fetchone()
            if not op or op["cents"] != cents:
                raise StripeError("That payment does not match this workspace and amount. Get a fresh quote.")
        else:
            # Reopening the page or retrying the quote recovers an unresolved payment.
            op = c.execute("""SELECT * FROM payment_operations WHERE workspace_id=?
                              AND status IN ('pending','unknown') ORDER BY created LIMIT 1""", (ws["id"],)).fetchone()
            if op and (op["kind"] != "manual" or op["cents"] != cents):
                raise StripeError("A previous top-up is still being confirmed. Finish that payment before starting another.")
            if not op:
                op = c.execute("""SELECT * FROM payment_operations WHERE workspace_id=? AND kind='manual'
                                  AND status='quoted' AND cents=? AND created_by=? AND expires>? ORDER BY created DESC LIMIT 1""",
                               (ws["id"], cents, by, time.time())).fetchone()
            if not op:
                c.execute("DELETE FROM payment_operations WHERE workspace_id=? AND status='quoted' AND expires<?", (ws["id"], time.time()))
                op = _new_payment(c, ws, cents, by)
        return {"operation_id": op["id"], "payment_status": op["status"], "expires": op["expires"]}


def form_quotes(ws, by):
    quotes = {}
    for cents in (2000, 4000):
        try:
            quotes[str(cents)] = quote_topup(ws, cents, by)["operation_id"]
        except StripeError:
            pass
    return quotes


def pending_payment(ws):
    with db.conn() as c:
        op = c.execute("SELECT id,cents,kind FROM payment_operations WHERE workspace_id=? AND status IN ('pending','unknown') ORDER BY created LIMIT 1", (ws["id"],)).fetchone()
    return dict(op) if op else None


def dollars_to_cents(value):
    try:
        cents = Decimal(value) * 100
        if not cents.is_finite() or cents != cents.to_integral_value():
            raise ValueError()
        return int(cents)
    except (InvalidOperation, ValueError):
        raise StripeError("Use an amount with at most two decimal places.")


def _validate_payment_identity(op, pi):
    """A failure can release a payment hold only when it belongs to this operation."""
    if (not isinstance(pi, dict) or pi.get("amount") != op["cents"] or pi.get("currency") != "usd" or
            pi.get("customer") != op["customer"] or not pi.get("id") or not isinstance(pi.get("metadata"), dict) or
            (pi.get("metadata") or {}).get("boathouse_operation") != op["id"] or
            (op["provider_id"] and pi["id"] != op["provider_id"])):
        raise StripeError("The payment result did not match the approved top-up. It needs reconciliation before another charge.", ambiguous=True)


def _complete_payment(op, pi):
    """The provider result, ledger and local operation settle atomically."""
    _validate_payment_identity(op, pi)
    if pi.get("status") != "succeeded" or pi.get("amount_received") != op["cents"]:
        raise StripeError("The payment has not confirmed the approved amount. It needs reconciliation before another charge.", ambiguous=True)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        current = c.execute("SELECT * FROM payment_operations WHERE id=?", (op["id"],)).fetchone()
        if current["provider_id"] and current["provider_id"] != pi["id"]:
            raise StripeError("Payment identity mismatch; contact support.", ambiguous=True)
        bal = c.execute("SELECT balance_cents FROM workspaces WHERE id=?", (op["workspace_id"],)).fetchone()[0]
        ref = f"stripe:{pi['id']}"
        existing = c.execute("SELECT * FROM ledger WHERE ref=?", (ref,)).fetchone()
        if existing and (existing["workspace_id"] != op["workspace_id"] or existing["amount_cents"] != op["cents"]):
            raise StripeError("Payment ledger mismatch; contact support.", ambiguous=True)
        if not existing:
            bal += op["cents"]
            memo = f"auto-refill {op['month']}" if op["kind"] == "auto" else f"top-up by {op['created_by']}"
            c.execute("UPDATE workspaces SET balance_cents=? WHERE id=?", (bal, op["workspace_id"]))
            c.execute("INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?)",
                      (db.new_id("l"), op["workspace_id"], time.time(), "topup", op["cents"], bal, memo, ref, op["created_by"]))
        c.execute("UPDATE payment_operations SET status='succeeded',provider_id=?,updated=?,error=NULL WHERE id=?",
                  (pi["id"], time.time(), op["id"]))
        referrals.sync(c, op['workspace_id'])
        # Checkout collected consent to save this card, but auto-refill stays off
        # unless the owner separately enables it with a monthly cap.
        if current["checkout_payload"] and pi.get("payment_method"):
            c.execute("UPDATE workspaces SET stripe_pm=? WHERE id=? AND stripe_customer=?",
                      (pi["payment_method"],op["workspace_id"],op["customer"]))
        c.execute("COMMIT")
    return bal


def _payment_result(op, pi):
    _validate_payment_identity(op, pi)
    if pi.get("status") == "succeeded":
        return _complete_payment(op, pi)
    failed = pi.get("status") in ("canceled", "requires_payment_method")
    error = "The card was declined. Update the card and get a new quote." if failed else "This payment is still being confirmed. Retry the same payment; no extra charge will be started."
    with db.conn() as c:
        c.execute("UPDATE payment_operations SET status=?,provider_id=COALESCE(?,provider_id),updated=?,error=? WHERE id=? AND status!='succeeded'",
                  ("failed" if failed else "unknown", pi.get("id"), time.time(), error, op["id"]))
    raise StripeError(error, ambiguous=not failed)


def _execute_payment(ws, op):
    if op["checkout_payload"]:
        raise StripeError("Complete or check this payment on its secure checkout page; another card charge will not be started.", ambiguous=True)
    previously_submitted = op["status"] in ("pending", "unknown")
    if op["status"] == "succeeded":
        return balance(ws["id"])
    if op["status"] == "failed":
        raise StripeError(op["error"] or "This payment failed. Update the card and get a new quote.")
    if op["status"] == "quoted":
        if op["expires"] < time.time():
            raise StripeError("That payment quote expired. Get a fresh quote before charging.")
        if not ws["stripe_pm"] or not ws["stripe_customer"]:
            raise StripeError("No card on file. Add a card first.")
        with db.conn() as c:
            if c.execute("SELECT 1 FROM payment_operations WHERE workspace_id=? AND id!=? AND status IN ('pending','unknown')", (ws["id"], op["id"])).fetchone():
                raise StripeError("A previous payment is still being confirmed. Finish it before starting another top-up.", ambiguous=True)
            payload = {"amount": op["cents"], "currency": "usd", "customer": ws["stripe_customer"], "payment_method": ws["stripe_pm"],
                       "off_session": "true", "confirm": "true", "description": f"Boathouse credit: {ws['slug']}",
                       "metadata[workspace]": ws["slug"], "metadata[boathouse_operation]": op["id"]}
            c.execute("UPDATE payment_operations SET status='pending',submitted=?,updated=?,customer=?,payment_method=?,payload_json=? WHERE id=?",
                      (time.time(), time.time(), ws["stripe_customer"], ws["stripe_pm"], json.dumps(payload), op["id"]))
            op = c.execute("SELECT * FROM payment_operations WHERE id=?", (op["id"],)).fetchone()
    try:
        if op["provider_id"]:
            return _payment_result(op, _stripe("GET", f"/payment_intents/{op['provider_id']}"))
        if time.time() - op["submitted"] >= 23 * 3600:
            raise StripeError("This payment needs confirmation from the card processor. Contact support; another charge will not be started.", ambiguous=True)
        pi = _stripe("POST", "/payment_intents", json.loads(op["payload_json"]),
            idempotency_key=f"bh-payment:{op['id']}")
        return _payment_result(op, pi)
    except StripeError as e:
        if e.payment_intent:
            try:
                return _payment_result(op, e.payment_intent)
            except StripeError as result_error:
                e = result_error
        # A retry's auth/not-found/validation response is not proof that the
        # original submission did not charge. Only a bound terminal intent above
        # may release a previously submitted operation.
        uncertain = e.ambiguous or previously_submitted
        with db.conn() as c:
            c.execute("UPDATE payment_operations SET status=?,updated=?,error=? WHERE id=? AND status NOT IN ('succeeded','failed')",
                      ("unknown" if uncertain else "failed", time.time(), str(e), op["id"]))
        raise e


def charge_card(ws, cents: int, memo: str, by: str | None, operation_id: str | None = None) -> int:
    if not operation_id:
        raise StripeError("Get a payment quote first, then confirm its operation_id and amount.")
    with payment_lock(ws["id"]), db.conn() as c:
        ws = db.workspace_by_id(ws["id"])
        op = c.execute("SELECT * FROM payment_operations WHERE id=? AND workspace_id=? AND kind='manual'", (operation_id, ws["id"])).fetchone()
        if not op or type(cents) is not int or op["cents"] != cents:
            raise StripeError("That payment does not match this workspace and amount. Get a fresh quote.")
        return _execute_payment(ws, op)


def auto_refill(ws) -> int:
    """One serialized, durable refill; unfinished payments reserve cap capacity."""
    with payment_lock(ws["id"]), db.conn() as c:
        ws = db.workspace_by_id(ws["id"])
        # Resolve submitted operations even after disabling refill, but never submit
        # a new payment after that setting changes.
        pending = c.execute("SELECT * FROM payment_operations WHERE workspace_id=? AND status IN ('pending','unknown') ORDER BY created LIMIT 1", (ws["id"],)).fetchone()
        if pending:
            if pending["kind"] != "auto":
                raise StripeError("A previous top-up is still being confirmed. Auto-refill will wait.", ambiguous=True)
            if not ws["autorefill_cents"] and not pending["provider_id"]:
                raise StripeError("A previously submitted refill is awaiting confirmation; refill is now off.", ambiguous=True)
            bal = _execute_payment(ws, pending)
        else:
            if not ws["autorefill_cents"] or balance(ws["id"]) >= REFILL_BELOW:
                return balance(ws["id"])
            cap = ws["autorefill_cap_cents"] or 0
            if cap <= 0:
                raise StripeError("Set a positive monthly auto-refill limit before enabling refill.")
            month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
            start = dt.datetime.strptime(month, "%Y-%m").replace(tzinfo=dt.timezone.utc).timestamp()
            spent = c.execute("SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE workspace_id=? AND kind='topup' AND memo LIKE 'auto-refill%' AND ts>=?", (ws["id"], start)).fetchone()[0]
            amount = min(ws["autorefill_cents"], max(0, cap - spent))
            if amount < 50:
                raise StripeError(f"Monthly auto-refill limit reached (${cap/100:.2f}); the remainder is below the card processor minimum.")
            op = _new_payment(c, ws, amount, "meter", "auto", month)
            bal = _execute_payment(ws, op)
    if is_paused(ws["id"]) and bal > PAUSE_AT:
        resume(ws)
    return bal


def set_auto_refill(ws, cents, cap):
    if type(cents) is not int or (cents != 0 and not 500 <= cents <= 100000):
        raise StripeError("Refill between $5 and $1,000, or 0 to turn it off.")
    if type(cap) is not int or cap < 0 or (cents and cap < 50):
        raise StripeError("Choose a positive monthly limit of at least $0.50 for auto-refill.")
    with payment_lock(ws["id"]), db.conn() as c:
        fresh = db.workspace_by_id(ws["id"])
        if cents and not fresh["stripe_pm"]:
            raise StripeError("No card on file yet. Add a card first.")
        c.execute("UPDATE workspaces SET autorefill_cents=?,autorefill_cap_cents=? WHERE id=?", (cents, cap if cents else 0, ws["id"]))
    return {"autorefill_cents": cents, "autorefill_cap_cents": cap if cents else 0}
